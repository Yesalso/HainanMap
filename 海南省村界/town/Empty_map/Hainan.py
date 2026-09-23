# -*- coding: utf-8 -*-
"""
海南本岛 · 1px:20m 比例尺县市地图（空白版，不带任何地名标注）
数据源：town/Hainan2002/Hainan_town.shp
生成：文昌、临高、澄迈、儋州、万宁、琼海 六张地图
输出文件名：Wenchang.png、Lingao.png、Chengmai.png、Danzhou.png、Wanning.png、Qionghai.png
"""
import os
import re
import warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import geopandas as gpd
import pandas as pd
import numpy as np
from io import BytesIO
from shapely.geometry import Point, Polygon

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/town/Hainan2002/Hainan_town.shp"
excel_path = "D:/Windows/Documents/海南省村界/海南省村界/HainanMap.xlsx"
out_dir = r"D:/Windows/Documents/海南省村界/海南省村界/town/Empty_map/ing_blue"

TARGET_DPI = 100
LINE_WIDTH_PT = 72.0 / TARGET_DPI
TARGET_CRS = "EPSG:32649"
BINARY_THRESHOLD = 200

PIXEL_TO_METER = 20  # 比例尺：1 像素 = 20 米
FONT_MIN = 16
FONT_MAX = 36
FONT_DEFAULT = 28
BASE_BUFFER_M = 3000
# ==================================================

warnings.filterwarnings("ignore", category=UserWarning, message="Glyph.*missing from font")
os.makedirs(out_dir, exist_ok=True)

# ---------- 简繁转换 ----------
try:
    from opencc import OpenCC
    _cc_s2t = OpenCC("s2t")
    _cc_t2s = OpenCC("t2s")
    print("✓ OpenCC 已启用（简繁互转）")
except:
    _cc_s2t = _cc_t2s = None
    print("⚠️ OpenCC 未安装")

def to_trad(s):
    return _cc_s2t.convert(s) if _cc_s2t else s

def to_simple(s):
    return _cc_t2s.convert(s) if _cc_t2s else s

# ---------- 字体 ----------
def find_font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for n in ["PMingLiU", "MingLiU", "新細明體", "Microsoft JhengHei", "SimHei", "SimSun"]:
        if n in names:
            return n
    return None
font_name = find_font()
print("使用字体：", font_name)

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

# 字段识别
cols = gdf.columns.tolist()
name_col = next((c for c in ["TOWN", "XZQMC", "NAME", "name", "名称"] if c in cols), None)
code_col = next((c for c in ["XZQDM", "CODE", "code", "行政区码"] if c in cols), None)
if name_col is None or code_col is None:
    raise ValueError(f"未找到名称或代码字段，现有：{cols}")
print(f"名称字段：{name_col}，代码字段：{code_col}")

# 投影
gdf = gdf.to_crs(TARGET_CRS)
print(f"原始要素数：{len(gdf)}")

# 排除三沙市
gdf = gdf[~gdf[name_col].astype(str).str.startswith("三沙市")].copy()
print(f"排除三沙市后：{len(gdf)}")

# 提取乡镇码
gdf["乡镇码"] = gdf[code_col].astype(str).str[:9]

# 修复无效几何（部分要素边界自相交，否则 dissolve 会报拓扑错误）
gdf = gdf.copy()
gdf["geometry"] = gdf.geometry.buffer(0)

# 提取乡镇名
def extract_township(name):
    s = str(name).strip()
    m = re.match(r'^([\u4e00-\u9fff]+?(?:镇|街道|乡))', s)
    if m:
        return m.group(1)
    m = re.search(r'([\u4e00-\u9fff]+?(?:街道|鎮|鄉|乡|镇))$', s)
    if m:
        return m.group(1)
    return s

# 合并乡镇
township = gdf.dissolve(by="乡镇码", aggfunc="first").reset_index()
township["原始全名"] = township["乡镇码"].map(dict(zip(gdf["乡镇码"], gdf[name_col])))
township["提取乡镇名"] = township["原始全名"].apply(extract_township)

# 匹配 Excel
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
township["显示地名"] = [r["display"] for r in matched]
township["县市"] = [r["city"] for r in matched]
township["英文名"] = [r["en"] for r in matched]
township["县市_简"] = township["县市"].apply(to_simple)
print(f"匹配到 {len(township)} 个乡镇/街道")

# ---------- 跳过标注：海口市和三亚市所有乡镇均不标注；特殊岛屿代码 ----------
SKIP_CODES = {"469023091"}  # 澄迈岛嶼1（Excel显示名为"一"，无标注意义）

def should_skip(row):
    city = row["县市_简"]
    if city in ["海口市", "三亚市"]:
        return True
    if str(row["乡镇码"]) in SKIP_CODES:
        return True
    return False
township["skip_label"] = township.apply(should_skip, axis=1)
print(f"跳过标注数（海口+三亚全部）：{township['skip_label'].sum()}")

# ---------- 强制换行映射（简繁全） ----------
FORCED_WRAP = {
    "兴隆华侨农场": "兴隆华侨\n农场",
    "興隆華僑農場": "興隆華僑\n農場",
    "杨浦经济开发区": "杨浦经济\n开发区",
    "洋浦經濟開發區": "洋浦經濟\n開發區",
}

def apply_forced_wrap(text):
    """根据映射强制换行，若匹配则返回换行后文本，否则原样"""
    for key, val in FORCED_WRAP.items():
        if key in text:
            return val
    return text

# ---------- 压线检测 ----------
def text_overlaps_boundary(point, text, font_size, geom, boundary, margin=2.0):
    px_per_pt = TARGET_DPI / 72.0
    char_width_px = font_size * 0.9 * px_per_pt
    char_height_px = font_size * 1.2 * px_per_pt
    lines = text.split('\n')
    max_len = max(len(line) for line in lines) if lines else len(text)
    text_width_px = max_len * char_width_px
    text_height_px = len(lines) * char_height_px
    text_width_m = text_width_px * PIXEL_TO_METER / TARGET_DPI
    text_height_m = text_height_px * PIXEL_TO_METER / TARGET_DPI
    minx = point.x - text_width_m/2
    maxx = point.x + text_width_m/2
    miny = point.y - text_height_m/2
    maxy = point.y + text_height_m/2
    bbox = Polygon([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
    if boundary.is_empty:
        return False
    return bbox.intersects(boundary) or bbox.distance(boundary) < margin

# ---------- 智能标注 ----------
def optimize_label(geom, raw_text):
    # 先强制换行
    raw_text = apply_forced_wrap(raw_text)
    simple_text = to_simple(raw_text) if _cc_t2s else raw_text

    # 处理 MultiPolygon
    if geom.geom_type == "MultiPolygon":
        polygons = list(geom.geoms)
        if not polygons:
            return raw_text, FONT_MIN, geom.representative_point()
        geom = max(polygons, key=lambda p: p.area)
    boundary = geom.boundary
    centroid = geom.centroid

    # ---- 北峙岛特例 ----
    if "北峙" in simple_text:
        print(f"  [北峙岛] 强制左侧外部，距离≥100像素")
        min_dist_m = 100 * PIXEL_TO_METER  # 3000米
        # 左侧方向（180度及其附近）
        angles = [180, 150, 210, 135, 225, 120, 240, 160, 200]
        for dist in np.arange(500, 8000, 100):
            for ang in angles:
                rad = np.radians(ang)
                dx, dy = np.cos(rad), np.sin(rad)
                p = Point(centroid.x + dx*dist, centroid.y + dy*dist)
                if not geom.contains(p):
                    # 尝试默认字号
                    if not text_overlaps_boundary(p, raw_text, FONT_DEFAULT, geom, boundary, margin=min_dist_m):
                        return raw_text, FONT_DEFAULT, p
                    # 尝试缩小字号
                    for fs in range(FONT_DEFAULT-2, FONT_MIN-1, -2):
                        if not text_overlaps_boundary(p, raw_text, fs, geom, boundary, margin=min_dist_m):
                            return raw_text, fs, p
        # 保底
        fallback = Point(centroid.x - 4000, centroid.y)
        return raw_text, FONT_MIN, fallback

    # ---- 正常候选点生成 ----
    candidate_points = [centroid, geom.representative_point()]

    # 临城镇：优先上方
    if simple_text == "临城镇":
        for dy in [500, 1000, 1500]:
            up = Point(centroid.x, centroid.y + dy)
            if geom.contains(up):
                candidate_points.append(up)
        buffer_ratios = [0.9, 0.7, 0.5, 0.3, 0.1]
    else:
        buffer_ratios = [0.8, 0.5, 0.3, 0.1]

    for ratio in buffer_ratios:
        try:
            buffered = geom.buffer(-BASE_BUFFER_M * ratio)
            if buffered.is_valid and not buffered.is_empty and buffered.area > 100:
                candidate_points.append(buffered.centroid)
        except:
            pass

    # 边界远点采样
    if geom.geom_type == "Polygon":
        coords = list(geom.exterior.coords)
        if len(coords) > 10:
            import random
            samples = random.sample(coords, min(30, len(coords)))
            farthest = max(samples, key=lambda p: Point(p).distance(centroid))
            candidate_points.append(Point(farthest))

    # 去重、过滤内部
    unique = []
    for p in candidate_points:
        if not any(p.distance(q) < 1 for q in unique) and (geom.contains(p) or geom.touches(p)):
            unique.append(p)
    if not unique:
        unique = [geom.representative_point()]

    # 排序：临城镇按y降序，其余按到边界距离降序
    if simple_text == "临城镇":
        unique.sort(key=lambda p: p.y, reverse=True)
    else:
        unique.sort(key=lambda p: p.distance(boundary), reverse=True)

    # 平移搜索
    directions = [(np.cos(np.radians(a)), np.sin(np.radians(a))) for a in np.arange(0, 360, 22.5)]
    step = 15
    max_steps = 50

    def translate_search(initial, display_text, font_size):
        if not text_overlaps_boundary(initial, display_text, font_size, geom, boundary):
            return initial
        best = None
        best_dist = -1
        for dx, dy in directions:
            for i in range(1, max_steps+1):
                new_p = Point(initial.x + dx*step*i, initial.y + dy*step*i)
                if geom.contains(new_p):
                    if not text_overlaps_boundary(new_p, display_text, font_size, geom, boundary):
                        return new_p
                    d = new_p.distance(boundary)
                    if d > best_dist:
                        best_dist = d
                        best = new_p
        return best

    # 字号尝试
    font_attempts = sorted(set([FONT_DEFAULT] +
                               list(range(FONT_DEFAULT+2, FONT_MAX+1, 2)) +
                               list(range(FONT_DEFAULT-2, FONT_MIN-1, -2))),
                           key=lambda x: abs(x-FONT_DEFAULT))

    for fs in font_attempts:
        for point in unique:
            display_txt = raw_text
            if not text_overlaps_boundary(point, display_txt, fs, geom, boundary):
                return display_txt, fs, point
            new_p = translate_search(point, display_txt, fs)
            if new_p is not None:
                return display_txt, fs, new_p
            # 若当前文本未换行且长度>=5，尝试自动换行
            if len(raw_text) >= 5 and '\n' not in raw_text:
                wrap_txt = apply_forced_wrap(raw_text)  # 再试一次（但已强制过，这里可能会重复）
                # 若强制换行未改变，尝试通用换行
                if wrap_txt == raw_text:
                    if len(raw_text) >= 8:
                        n = len(raw_text); part = n//3
                        wrap_txt = '\n'.join([raw_text[i*part:(i+1)*part] for i in range(3)])
                    elif len(raw_text) >= 5:
                        n = len(raw_text); half = n//2
                        wrap_txt = raw_text[:half] + '\n' + raw_text[half:]
                if wrap_txt != raw_text:
                    if not text_overlaps_boundary(point, wrap_txt, fs, geom, boundary):
                        return wrap_txt, fs, point
                    new_p = translate_search(point, wrap_txt, fs)
                    if new_p is not None:
                        return wrap_txt, fs, new_p
        continue

    # 保底
    fallback_point = geom.representative_point()
    fallback_txt = raw_text
    if len(raw_text) >= 5 and '\n' not in raw_text:
        n = len(raw_text); half = n//2
        fallback_txt = raw_text[:half] + '\n' + raw_text[half:]
    return fallback_txt, FONT_MIN, fallback_point


# ---------- 生成各县地图 ----------
def generate_county_map(county_data, county_name, out_dir, file_name=None):
    """
    生成单个县的地图
    county_name: 中文名称（用于打印）
    file_name: 输出文件名（不含扩展名），若为None则使用county_name
    """
    if file_name is None:
        file_name = county_name
    if county_data.empty:
        print(f"警告：{county_name} 无数据，跳过")
        return
    total_bounds = county_data.total_bounds
    minx, miny, maxx, maxy = total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    if geo_w <= 0 or geo_h <= 0:
        print(f"警告：{county_name} 范围无效，跳过")
        return

    w_px = int(round(geo_w / PIXEL_TO_METER))
    h_px = int(round(geo_h / PIXEL_TO_METER))
    if w_px < 10 or h_px < 10:
        print(f"警告：{county_name} 范围过小，跳过")
        return
    MAX_PX = 20000
    if w_px > MAX_PX or h_px > MAX_PX:
        scale = min(MAX_PX/w_px, MAX_PX/h_px)
        w_px, h_px = int(round(w_px*scale)), int(round(h_px*scale))
        print(f"{county_name} 缩放至 {w_px}×{h_px}，比例尺变为 {geo_w/w_px:.1f} 米/像素")

    print(f"{county_name} 范围 {geo_w:.0f}×{geo_h:.0f} m，像素 {w_px}×{h_px}")

    fig = plt.figure(figsize=(w_px/TARGET_DPI, h_px/TARGET_DPI), dpi=TARGET_DPI)
    ax = fig.add_axes([0,0,1,1])
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_facecolor("white")
    ax.set_aspect('equal')
    ax.axis("off")

    county_data.plot(ax=ax, edgecolor="black", facecolor="white",
                     linewidth=LINE_WIDTH_PT, antialiased=False, legend=False)

    # 不带地名：不绘制任何乡镇名称标注

    out_file = os.path.join(out_dir, f"{file_name}.png")
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

# 筛选目标县市，并指定英文文件名
target_counties = [
    ("文昌市", "Wenchang"),
    ("临高县", "Lingao"),
    ("澄迈县", "Chengmai"),
    ("儋州市", "Danzhou"),
    ("万宁市", "Wanning"),
    ("琼海市", "Qionghai"),
    ("东方市", "Dongfang"),
    ("乐东黎族自治县", "Ledong"),
    ("屯昌县", "Tunchang"),
    ("五指山市", "Wuzhishan")
]

# 命令行用法：python Hainan.py [县市名] [输出文件名]
# 例：python Hainan.py 文昌市 Wenchang2   只生成文昌市，输出 Wenchang2.png
# 不带参数则生成全部六张地图
import sys


def generate_target(targets, out_file_name):
    for county, eng_name in targets:
        county_subset = township[township["县市_简"] == county].copy()
        if county_subset.empty:
            print(f"警告：未找到 {county} 的数据")
            continue
        print(f"生成 {county} 地图，包含 {len(county_subset)} 个乡镇/街道")
        generate_county_map(county_subset, county, out_dir, file_name=out_file_name or eng_name)


if len(sys.argv) > 1:
    arg = sys.argv[1]
    out_name = sys.argv[2] if len(sys.argv) > 2 else None
    matched_counties = [(c, e) for c, e in target_counties
                        if c == arg or e.lower() == arg.lower()]
    if matched_counties:
        generate_target(matched_counties, out_name)
    else:
        print(f"未找到匹配的县市：{arg}")
else:
    generate_target(target_counties, None)

print("\n✅ 所有地图生成完毕")