# -*- coding: utf-8 -*-
"""
海南本岛 · 1px:20m 比例尺（空白版，不带任何地名标注）
数据源：town/Hainan2002/Hainan_town.shp
生成：儋州、昌江、白沙、乐东、东方 五市县合并地图
输出文件名：West5.png
"""
import os
import re
import warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
import pandas as pd
import numpy as np
from io import BytesIO

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/town/Hainan2002/Hainan_town.shp"
excel_path = "D:/Windows/Documents/海南省村界/海南省村界/HainanMap.xlsx"
out_dir = os.path.dirname(os.path.abspath(__file__))

TARGET_DPI = 100
LINE_WIDTH_PT = 72.0 / TARGET_DPI
TARGET_CRS = "EPSG:32649"
BINARY_THRESHOLD = 200

PIXEL_TO_METER = 20  # 比例尺：1 像素 = 20 米
# ==================================================

warnings.filterwarnings("ignore", category=UserWarning, message="Glyph.*missing from font")
os.makedirs(out_dir, exist_ok=True)

# ---------- 简繁转换 ----------
try:
    from opencc import OpenCC
    _cc_t2s = OpenCC("t2s")
except:
    _cc_t2s = None

def to_simple(s):
    return _cc_t2s.convert(s) if _cc_t2s else s

# ---------- 读取 Excel ----------
df_excel = pd.read_excel(excel_path, header=None, usecols="A,C,D,E", dtype=str)
df_excel.columns = ["key", "en_name", "city_cn", "display_name"]
df_excel = df_excel.dropna(subset=["key", "display_name", "city_cn"])
for col in ["key", "display_name", "en_name", "city_cn"]:
    df_excel[col] = df_excel[col].str.strip()

key_to_info = {}
for _, row in df_excel.iterrows():
    key = row["key"]
    if key not in key_to_info:
        key_to_info[key] = {
            "display": row["display_name"],
            "city": row["city_cn"],
            "en": row["en_name"]
        }
print(f"Excel 映射记录数：{len(key_to_info)}")

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
name_col = next((c for c in ["TOWN", "XZQMC", "NAME", "name", "名称"] if c in cols), None)
code_col = next((c for c in ["XZQDM", "CODE", "code", "行政区码"] if c in cols), None)
if name_col is None or code_col is None:
    raise ValueError(f"未找到名称或代码字段，现有：{cols}")
print(f"名称字段：{name_col}，代码字段：{code_col}")

gdf = gdf.to_crs(TARGET_CRS)
gdf = gdf[~gdf[name_col].astype(str).str.startswith("三沙市")].copy()

gdf["乡镇码"] = gdf[code_col].astype(str).str[:9]
gdf["geometry"] = gdf.geometry.buffer(0)

def extract_township(name):
    s = str(name).strip()
    m = re.match(r'^([\u4e00-\u9fff]+?(?:镇|街道|乡))', s)
    if m:
        return m.group(1)
    m = re.search(r'([\u4e00-\u9fff]+?(?:街道|鎮|鄉|乡|镇))$', s)
    if m:
        return m.group(1)
    return s

township = gdf.dissolve(by="乡镇码", aggfunc="first").reset_index()
township["原始全名"] = township["乡镇码"].map(dict(zip(gdf["乡镇码"], gdf[name_col])))
township["提取乡镇名"] = township["原始全名"].apply(extract_township)

matched = []
for idx, row in township.iterrows():
    code = row["乡镇码"]
    full = row["原始全名"]
    extracted = row["提取乡镇名"]
    info = None
    if code in key_to_info:
        info = key_to_info[code]
    elif full in key_to_info:
        info = key_to_info[full]
    elif extracted in key_to_info:
        info = key_to_info[extracted]
    else:
        for k, v in key_to_info.items():
            if k in extracted or extracted in k:
                info = v
                break
    if info:
        matched.append({"index": idx, "display": info["display"], "city": info["city"], "en": info["en"]})

if not matched:
    raise ValueError("无匹配乡镇")
township = township.loc[[r["index"] for r in matched]].copy()
township["县市_简"] = [to_simple(r["city"]) for r in matched]
print(f"匹配到 {len(township)} 个乡镇/街道")

# ---------- 生成合并地图 ----------
def generate_map(county_data, out_file):
    total_bounds = county_data.total_bounds
    minx, miny, maxx, maxy = total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    if geo_w <= 0 or geo_h <= 0:
        print("警告：范围无效")
        return

    w_px = int(round(geo_w / PIXEL_TO_METER))
    h_px = int(round(geo_h / PIXEL_TO_METER))
    MAX_PX = 20000
    if w_px > MAX_PX or h_px > MAX_PX:
        scale = min(MAX_PX / w_px, MAX_PX / h_px)
        w_px, h_px = int(round(w_px * scale)), int(round(h_px * scale))
        print(f"缩放至 {w_px}×{h_px}，比例尺变为 {geo_w/w_px:.1f} 米/像素")

    print(f"范围 {geo_w:.0f}×{geo_h:.0f} m，像素 {w_px}×{h_px}")

    fig = plt.figure(figsize=(w_px / TARGET_DPI, h_px / TARGET_DPI), dpi=TARGET_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_facecolor("white")
    ax.set_aspect('equal')
    ax.axis("off")

    county_data.plot(ax=ax, edgecolor="black", facecolor="white",
                     linewidth=LINE_WIDTH_PT, antialiased=False, legend=False)

    # 不带地名：不绘制任何乡镇名称标注

    print(f"保存 {out_file} ...")
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=TARGET_DPI, pad_inches=0, bbox_inches=None, facecolor="white")
    plt.close(fig)
    buf.seek(0)

    try:
        import cv2
        img = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, bin_img = cv2.threshold(gray, BINARY_THRESHOLD, 255, cv2.THRESH_BINARY)
        ok, enc_png = cv2.imencode(
            ".png", cv2.cvtColor(bin_img, cv2.COLOR_GRAY2BGR),
            [cv2.IMWRITE_PNG_COMPRESSION, 9])
        if ok:
            with open(out_file, "wb") as f:
                f.write(enc_png.tobytes())
        else:
            with open(out_file, "wb") as f:
                f.write(buf.getvalue())
    except:
        with open(out_file, "wb") as f:
            f.write(buf.getvalue())
        print(f"⚠️ 未安装opencv，直接保存 {out_file}")


TARGET_COUNTIES = [
    ("儋州市", "Danzhou"),
    ("昌江黎族自治县", "Changjiang"),
    ("白沙黎族自治县", "Baisha"),
    ("乐东黎族自治县", "Ledong"),
    ("东方市", "Dongfang"),
]

for county, eng_name in TARGET_COUNTIES:
    subset = township[township["县市_简"] == county].copy()
    if subset.empty:
        print(f"警告：未找到 {county} 的数据，跳过")
        continue
    print(f"生成 {county} 地图，包含 {len(subset)} 个乡镇/街道")
    generate_map(subset, os.path.join(out_dir, f"{eng_name}.png"))

print("\n✅ 所有地图生成完毕")
