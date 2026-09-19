# -*- coding: utf-8 -*-
"""
根据 Wenchang2002.xlsx 重新划分文昌市乡镇边界（仅显示乡镇级界线）
- 匹配到的村：归入对应乡镇，面填充白色，乡镇边界红色（2像素）
- 未匹配的村（含裁撤、更名等）：面填充灰色（#CCCCCC），无任何边界线
- 不绘制村级边界，避免与当前界线混淆
- 比例尺：1像素 = 60米，无锯齿，像素对齐
输出：C:/Users/Windows/Desktop/Output/60/Wenchang2002.png
"""

import os
import warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.affinity import translate

# ==================== 配置 ====================
SHP_PATH = "D:/Windows/Documents/海南省村界/海南省村界/海南村界.shp"
EXCEL_2002_PATH = "D:/Windows/Documents/海南省村界/海南省村界/Wenchang2002.xlsx"
OUT_DIR = "C:/Users/Windows/Desktop/Output/60"

TARGET_DPI = 100
PIXEL_TO_METER = 60          # 1像素 = 60米
TARGET_CRS = "EPSG:32649"
MAX_PX = 20000

TOWN_LINE_WIDTH = 2.0 * 72.0 / TARGET_DPI   # 2 像素
UNMATCHED_FACE_COLOR = "#CCCCCC"            # 灰色填充
# ==============================================

os.makedirs(OUT_DIR, exist_ok=True)
warnings.filterwarnings("ignore", category=UserWarning, message="Glyph.*missing from font")

# ---------- 1. 读取 Excel ----------
df_2002 = pd.read_excel(EXCEL_2002_PATH, header=0, dtype=str)
print(f"Excel 行数：{len(df_2002)}")

# 自动识别列名
town_col = None
village_col = None
for col in df_2002.columns:
    if '乡镇' in col and '名称' in col:
        town_col = col
    if '建制村' in col and '名称' in col:
        village_col = col
if town_col is None or village_col is None:
    raise ValueError("未找到'乡镇名称'或'建制村名称'列，请检查 Excel 列名")

# 建立 村名 -> 乡镇名 映射
name_to_town = {}
for idx, row in df_2002.iterrows():
    town = str(row[town_col]).strip() if pd.notna(row[town_col]) else ''
    villages = str(row[village_col]).strip() if pd.notna(row[village_col]) else ''
    if not town or not villages or villages == '—':
        continue
    for sep in ['、', '，', ',', ' ', ';']:
        if sep in villages:
            parts = villages.split(sep)
            break
    else:
        parts = [villages]
    for v in parts:
        v = v.strip()
        if v:
            name_to_town[v] = town

print(f"建立映射 {len(name_to_town)} 个村名 -> {len(set(name_to_town.values()))} 个乡镇")

# ---------- 2. 读取 SHP，提取文昌市 ----------
gdf = None
for enc in ["gbk", "utf-8", "gb2312", "latin1"]:
    try:
        gdf = gpd.read_file(SHP_PATH, encoding=enc)
        print(f"成功以 {enc} 编码读取 SHP")
        break
    except:
        continue
if gdf is None:
    raise ValueError("无法读取 SHP")

cols = gdf.columns.tolist()
name_col = next((c for c in ["XZQMC", "NAME", "name", "名称"] if c in cols), None)
code_col = next((c for c in ["XZQDM", "CODE", "code", "行政区码"] if c in cols), None)
if name_col is None or code_col is None:
    raise ValueError(f"未找到名称或代码字段，现有：{cols}")

gdf = gdf.to_crs(TARGET_CRS)
print(f"原始要素数：{len(gdf)}")

WENCHANG_CODE = "469005"
gdf["县码"] = gdf[code_col].astype(str).str[:6]
gdf_wenchang = gdf[gdf["县码"] == WENCHANG_CODE].copy()
print(f"文昌市村级要素数：{len(gdf_wenchang)}")
if gdf_wenchang.empty:
    raise ValueError("未找到文昌市数据，请检查行政区码")

# ---------- 3. 匹配村名，标记新乡镇 ----------
def clean_name(name):
    if not name:
        return ''
    name = str(name).strip()
    suffixes = ['村委会', '村民委员会', '社区居委会', '居委会', '村', '社区']
    for suf in suffixes:
        if name.endswith(suf):
            name = name[:-len(suf)]
            break
    if name.startswith('文昌市'):
        name = name[3:]
    return name.strip()

matched_towns = []
for idx, row in gdf_wenchang.iterrows():
    raw = row[name_col]
    clean = clean_name(raw)
    town = name_to_town.get(clean)
    if town is None:
        for village, t in name_to_town.items():
            if village in clean or clean in village:
                town = t
                break
    matched_towns.append(town)

gdf_wenchang["新乡镇"] = matched_towns
matched_count = sum(1 for t in matched_towns if t is not None)
print(f"匹配成功：{matched_count} / {len(matched_towns)} 个村")

# ---------- 4. 像素对齐 ----------
total_bounds = gdf_wenchang.total_bounds
minx, miny, maxx, maxy = total_bounds
offset_x = np.round(minx / PIXEL_TO_METER) * PIXEL_TO_METER - minx
offset_y = np.round(miny / PIXEL_TO_METER) * PIXEL_TO_METER - miny
if abs(offset_x) > 1e-6 or abs(offset_y) > 1e-6:
    gdf_wenchang["geometry"] = gdf_wenchang.geometry.apply(lambda geom: translate(geom, xoff=offset_x, yoff=offset_y))
    minx += offset_x; miny += offset_y; maxx += offset_x; maxy += offset_y
    print(f"像素对齐偏移量 ({offset_x:.1f}, {offset_y:.1f}) 米")

# ---------- 5. 生成乡镇组（仅匹配成功的） ----------
towns_gdf = gdf_wenchang[gdf_wenchang["新乡镇"].notna()].copy()
towns_dissolved = towns_gdf.dissolve(by="新乡镇", aggfunc="first")
print(f"生成 {len(towns_dissolved)} 个乡镇合并边界")

# ---------- 6. 计算图片尺寸 ----------
geo_w, geo_h = maxx - minx, maxy - miny
w_px = int(round(geo_w / PIXEL_TO_METER))
h_px = int(round(geo_h / PIXEL_TO_METER))
if w_px < 10 or h_px < 10:
    raise ValueError("范围过小")
if w_px > MAX_PX or h_px > MAX_PX:
    scale = min(MAX_PX / w_px, MAX_PX / h_px)
    w_px = int(round(w_px * scale))
    h_px = int(round(h_px * scale))
    print(f"缩放至 {w_px}×{h_px}，比例尺变更为 {geo_w/w_px:.1f} 米/像素")

print(f"文昌2002: 地理范围 {geo_w:.0f}×{geo_h:.0f} m → 图片 {w_px}×{h_px} px")

# ---------- 7. 绘图（不绘制村级边界） ----------
fig = plt.figure(figsize=(w_px / TARGET_DPI, h_px / TARGET_DPI), dpi=TARGET_DPI)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(minx, maxx)
ax.set_ylim(miny, maxy)
ax.set_facecolor("white")
ax.set_aspect('equal')
ax.axis("off")

# 7a. 填充面：匹配的白色，未匹配的灰色
for idx, row in gdf_wenchang.iterrows():
    geom = row.geometry
    if geom.is_empty:
        continue
    facecolor = 'white' if row["新乡镇"] is not None else UNMATCHED_FACE_COLOR
    gpd.GeoSeries([geom]).plot(ax=ax, facecolor=facecolor, edgecolor='none', zorder=0)

# 7b. 仅绘制乡镇级边界（红色，2像素），无村级边界
for geom in towns_dissolved.geometry:
    if geom.is_empty:
        continue
    boundary = geom.boundary
    if not boundary.is_empty:
        gpd.GeoSeries([boundary]).plot(ax=ax, edgecolor='red', facecolor='none',
                                       linewidth=TOWN_LINE_WIDTH, antialiased=False, zorder=2)

# 保存
out_file = os.path.join(OUT_DIR, "Wenchang2002.png")
plt.savefig(out_file, dpi=TARGET_DPI, pad_inches=0, bbox_inches=None, facecolor='white')
plt.close(fig)
print(f"已保存：{out_file}")

print("\n✅ 文昌2002年乡镇界线地图生成完毕（仅显示乡镇界线，无村级边界）")
print(f"输出目录：{OUT_DIR}")