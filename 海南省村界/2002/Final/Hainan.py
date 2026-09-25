# -*- coding: utf-8 -*-
"""
海南本岛 · 全省乡镇地图（繁体标注）
读取 Final/Hainan_town_codefix.shp 绘制全省乡镇地图
强制换行：兴隆华侨农场、洋浦经济开发区（简繁均支持）
跳过标注：海口市和三亚市所有乡镇（不显示任何地名）
北峙岛：强制左侧外部，距离边界≥100像素
临城镇：优先上方
输出文件名：Hainan_town_codefix.png
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
from shapely.ops import unary_union
import shapely

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_codefix.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final"

TARGET_DPI = 100
SIMPLIFY_TOL_M = 5      # 读取后统一简化容差（米）：修自交、去针刺抖动
GENERATE_LABELED = False  # 是否生成含地名的标注版大图（当前只出 1:60 无地名底图）
ANTIALIAS_LINES = True    # 线条抗锯齿：消除硬像素对齐造成的粗细跳变
MERGE_SHARED_EDGES = True # 合并相邻乡镇共享边，只描边一次，消除叠加双线
SNAP_GRID_M = 10          # 坐标吸附栅格（米，0=关闭）：消除亚像素抖动、帮助重合共享边
TOWN_LINE_PX = 0.5      # 乡镇边界线宽（像素）
COUNTY_LINE_PX = 2      # 县市边界线宽（像素）
COUNTY_MIN_AREA_KM2 = 2 # 绘制县市边界的最小面积阈值，过滤沿海小岛碎片
LINE_WIDTH_PT = TOWN_LINE_PX * 72.0 / TARGET_DPI
TARGET_CRS = "EPSG:32649"
BINARY_THRESHOLD = 200

PIXEL_TO_METER = 30
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

# ---------- 乡镇地名 ----------
# Hainan_town_codefix.shp 已自带 TOWN/CITY/EN 字段，直接使用，不再依赖外部 Excel 映射

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
name_col = next((c for c in ["XZQMC", "NAME", "name", "名称", "TOWN", "Town", "town"] if c in cols), None)
code_col = next((c for c in ["XZQDM", "CODE", "code", "行政区码"] if c in cols), None)
if name_col is None or code_col is None:
    raise ValueError(f"未找到名称或代码字段，现有：{cols}")
print(f"名称字段：{name_col}，代码字段：{code_col}")

# 投影
gdf = gdf.to_crs(TARGET_CRS)
print(f"原始要素数：{len(gdf)}")

# 几何清理：buffer(0) 修复自交/无效，simplify 去除针刺抖动与冗余顶点
def _npts(gs):
    n = 0
    for go in gs:
        for g0 in (go.geoms if go.geom_type == "MultiPolygon" else [go]):
            n += len(g0.exterior.coords) + sum(len(r.coords) for r in g0.interiors)
    return n

before_n = _npts(gdf.geometry)
gdf["geometry"] = gdf.geometry.buffer(0).simplify(
    SIMPLIFY_TOL_M, preserve_topology=True)
if SNAP_GRID_M > 0:
    gdf["geometry"] = gdf.geometry.apply(lambda g0: shapely.set_precision(g0, SNAP_GRID_M))
after_n = _npts(gdf.geometry)
print(f"几何清理：简化容差 {SIMPLIFY_TOL_M}m，有效 {int(gdf.geometry.is_valid.sum())}/{len(gdf)}，顶点 {before_n}->{after_n}")

# 排除三沙市
gdf = gdf[~gdf[name_col].astype(str).str.startswith("三沙市")].copy()
print(f"排除三沙市后：{len(gdf)}")

# 提取乡镇码
gdf["乡镇码"] = gdf[code_col].astype(str).str[:9]

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

# 乡镇数据（Hainan_town_codefix.shp 中乡镇码唯一，无需 dissolve 合并）
township = gdf.reset_index(drop=True).copy()
township["原始全名"] = township[name_col].astype(str)
township["提取乡镇名"] = township["原始全名"].apply(extract_township)

# 地名/县市/英文名直接取自 SHP（避免外部 Excel 映射）
township["显示地名"] = township[name_col].astype(str)
township["县市"] = township["CITY"].astype(str)
township["英文名"] = township["EN"].astype(str)
township["县市_简"] = township["县市"].apply(to_simple)

township = township[township["显示地名"].isin(["", "nan"]) == False].copy()
print(f"共 {len(township)} 个乡镇/街道")

# ---------- 跳过标注：海口市和三亚市所有乡镇均不标注 ----------
def should_skip(row):
    city = row["县市_简"]
    if city in ["海口市", "三亚市"]:
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
def generate_county_map(county_data, county_name, out_dir, file_name=None, draw_labels=True):
    """
    生成单个县的地图
    county_name: 中文名称（用于打印）
    file_name: 输出文件名（不含扩展名），若为None则使用county_name
    draw_labels: 是否绘制地名标注
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

    # 乡镇边界：合并相邻乡镇共享边后只描边一次，避免重复描边造成的双线/粗细不均
    if MERGE_SHARED_EDGES:
        boundary_line = unary_union([geom.boundary for geom in county_data.geometry])
        gpd.GeoSeries([boundary_line], crs=county_data.crs).plot(
            ax=ax, color="black", linewidth=LINE_WIDTH_PT,
            antialiased=ANTIALIAS_LINES)
    else:
        county_data.plot(ax=ax, edgecolor="black", facecolor="white",
                         linewidth=LINE_WIDTH_PT, antialiased=ANTIALIAS_LINES, legend=False)

    # 县市边界：按县市溶解后绘制，线宽 2px（叠加在乡镇边界之上）
    # 过滤面积小于阈值的小岛碎片，避免沿海区域出现大量粗线小圈
    if "县市" in county_data.columns and county_data["县市"].nunique() > 1:
        try:
            merged = county_data.copy()
            merged["geometry"] = merged.geometry.buffer(0)
            counties = merged.dissolve(by="县市", aggfunc="first")
            min_area = COUNTY_MIN_AREA_KM2 * 1e6
            big_parts, filtered = [], 0
            for g0 in counties.geometry:
                polys = list(g0.geoms) if g0.geom_type == "MultiPolygon" else [g0]
                for p in polys:
                    if p.area >= min_area:
                        big_parts.append(p)
                    else:
                        filtered += 1
            county_pt = COUNTY_LINE_PX * 72.0 / TARGET_DPI
            if big_parts:
                gpd.GeoSeries(big_parts, crs=counties.crs).boundary.plot(
                    ax=ax, color="black", linewidth=county_pt, antialiased=ANTIALIAS_LINES, zorder=2)
            if filtered:
                print(f"县市边界过滤小岛碎片 {filtered} 块（<{COUNTY_MIN_AREA_KM2}km²）")
        except Exception as e:
            print(f"⚠️ 县市边界绘制失败：{e}")

    if draw_labels:
        for idx, row in county_data.iterrows():
            if row["skip_label"]:
                continue
            raw_txt = str(row["显示地名"])
            trad_txt = to_trad(raw_txt)
            display_txt, font_size, point = optimize_label(row.geometry, trad_txt)
            ax.text(point.x, point.y, display_txt,
                    fontsize=font_size, ha="center", va="center",
                    fontfamily=font_name, color="black", zorder=3)

    out_file = os.path.join(out_dir, f"{file_name}.png")
    print(f"保存 {out_file} ...")
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=TARGET_DPI, pad_inches=0, bbox_inches=None, facecolor="white")
    plt.close(fig)
    buf.seek(0)

    # 直接保存 matplotlib 输出（保留抗锯齿均匀线条，不做硬二值化）
    with open(out_file, "wb") as f:
        f.write(buf.getvalue())
    print(f"已保存 {out_file}")

# ---------- 生成全省地图（标注版，默认关闭） ----------
if GENERATE_LABELED:
    print(f"生成 海南全岛 地图，包含 {len(township)} 个乡镇/街道")
    generate_county_map(township, "海南全岛", out_dir, file_name="Hainan_town_codefix")

# ---------- 60米/像素、无地名底图 ----------
PIXEL_TO_METER = 60
print(f"生成 海南全岛 无地名底图（{PIXEL_TO_METER} 米/像素）")
generate_county_map(township, "海南全岛(无地名)", out_dir,
                    file_name="Hainan_town_codefix_no_label", draw_labels=False)

print("\n✅ 所有地图生成完毕")