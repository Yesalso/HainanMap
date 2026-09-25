# -*- coding: utf-8 -*-
"""
三亚市 · 乡镇地图（无地名）
基于 Hainan.py 的绘制逻辑：共享边合并只描一次、抗锯齿、无标注
输出文件名：Sanya_no_label.png
"""
import os
import warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
from shapely.ops import unary_union
import shapely

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_codefix.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final"
out_name = "Sanya_no_label"

TARGET_DPI = 100
SIMPLIFY_TOL_M = 5
ANTIALIAS_LINES = True
MERGE_SHARED_EDGES = True
SNAP_GRID_M = 10
TOWN_LINE_PX = 2.0      # 三亚图幅较小，适当加粗
COUNTY_LINE_PX = 2
COUNTY_MIN_AREA_KM2 = 2
LINE_WIDTH_PT = TOWN_LINE_PX * 72.0 / TARGET_DPI
TARGET_CRS = "EPSG:32649"
PIXEL_TO_METER = 30          # 县级图用 30 米/像素
# ==================================================

warnings.filterwarnings("ignore", category=UserWarning, message="Glyph.*missing from font")
os.makedirs(out_dir, exist_ok=True)

# ---------- 读取 SHP ----------
gdf = None
for enc in ["utf-8", "gbk", "gb2312", "latin1"]:
    try:
        gdf = gpd.read_file(shp_path, encoding=enc)
        print(f"成功以 {enc} 编码读取")
        break
    except:
        continue
if gdf is None:
    raise ValueError("无法读取SHP")

cols = gdf.columns.tolist()
name_col = next((c for c in ["XZQMC", "NAME", "name", "名称", "TOWN", "Town", "town"] if c in cols), None)
code_col = next((c for c in ["XZQDM", "CODE", "code", "行政区码"] if c in cols), None)
if name_col is None or code_col is None:
    raise ValueError(f"未找到名称或代码字段，现有：{cols}")

gdf = gdf.to_crs(TARGET_CRS)
print(f"原始要素数：{len(gdf)}")

# ---------- 几何清理（与 Hainan.py 相同） ----------
gdf["geometry"] = gdf.geometry.buffer(0).simplify(SIMPLIFY_TOL_M, preserve_topology=True)
if SNAP_GRID_M > 0:
    gdf["geometry"] = gdf.geometry.apply(lambda g0: shapely.set_precision(g0, SNAP_GRID_M))
print(f"几何清理后有效 {int(gdf.geometry.is_valid.sum())}/{len(gdf)}")

# ---------- 筛选三亚市 ----------
city_mask = gdf["CITY"].astype(str).str.contains("三亚", na=False)
sanya = gdf[city_mask].copy()
print(f"三亚市乡镇/街道：{len(sanya)} 个")
for _, r in sanya.iterrows():
    print("   ", r[name_col])
if sanya.empty:
    raise ValueError("未找到三亚市数据")

# ---------- 绘制 ----------
total_bounds = sanya.total_bounds
minx, miny, maxx, maxy = total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
print(f"范围 {geo_w:.0f}×{geo_h:.0f} m")

w_px = int(round(geo_w / PIXEL_TO_METER))
h_px = int(round(geo_h / PIXEL_TO_METER))
MAX_PX = 20000
if w_px > MAX_PX or h_px > MAX_PX:
    scale = min(MAX_PX / w_px, MAX_PX / h_px)
    w_px, h_px = int(round(w_px * scale)), int(round(h_px * scale))
print(f"像素 {w_px}×{h_px}（{PIXEL_TO_METER} 米/像素）")

fig = plt.figure(figsize=(w_px / TARGET_DPI, h_px / TARGET_DPI), dpi=TARGET_DPI)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
ax.set_facecolor("white")
ax.set_aspect("equal")
ax.axis("off")

# 乡镇边界：合并共享边，只描边一次
if MERGE_SHARED_EDGES:
    boundary_line = unary_union([geom.boundary for geom in sanya.geometry])
    gpd.GeoSeries([boundary_line], crs=sanya.crs).plot(
        ax=ax, color="black", linewidth=LINE_WIDTH_PT,
        antialiased=ANTIALIAS_LINES)
else:
    sanya.plot(ax=ax, edgecolor="black", facecolor="white",
               linewidth=LINE_WIDTH_PT, antialiased=ANTIALIAS_LINES)

# 县市边界（三亚仅一县，跳过内部县市线；如需可留空）
# 无地名：不绘制任何标注

out_file = os.path.join(out_dir, f"{out_name}.png")
fig.savefig(out_file, dpi=TARGET_DPI, pad_inches=0, bbox_inches=None, facecolor="white")
plt.close(fig)
print(f"已保存 {out_file}")
