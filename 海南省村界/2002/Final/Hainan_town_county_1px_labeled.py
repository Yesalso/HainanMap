# -*- coding: utf-8 -*-
"""
海南本岛 · 乡镇边界 + 县市边界地图（地名标注版）—— 乡镇 1px + 县市 2px
底图与 Hainan_town_county_1px.py 完全一致（双层 Bresenham 描绘 +
"最近两侧县市"距离场判别：乡镇界 1px、县市界 2px，界线纯黑白），
在其上参考 Hainan.py 的方式叠加地名标注：
  1. 智能标注（像素空间）：质心/代表点/内缩质心候选 → 文字框压线检测 →
     16 方向平移搜索 → 字号逐级缩小 → 长名自动换行（后缀断行）→
     保底落在避让搜索的最优点并加白边晕圈；
  2. 跨乡镇标签互斥：已放置标签登记占用框，后续标签候选点自动避开；
  3. 海口市密集核心街道（13 个，面积 1.1~8.5 km²，放不下地名）按参考图
     Haikou_coastline.png 的方式改用圆圈数字标注，图幅右侧列图例
     （1. 中山街道 … 13. 金宇街道；1-12 与参考图一致，13 为补缺）；
  4. 其余乡镇（含三亚市城区小区）用小字号直接标注；
  5. 字体：MingLiU（細明體，用户指定，取自用户字体目录）；
  6. 画布：4000×4000（右侧 420px 图例区，地图 3580px 宽、垂直居中）；
  7. 文字 Matplotlib(DPI=100) 渲染 + 阈值 200 二值化（仿 PrintWord.py）→ 全图仅纯黑 0 / 纯白 255
     两级像素（无抗锯齿灰阶）。
输出：2002/Hainan_town_county_town1_county2_labeled.png（右侧带图例）
诊断：Final/diag_label_haikou.png / diag_label_sanya.png / diag_label_legend.png
"""
import os
import math
import warnings
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.ops import unary_union
from shapely.geometry import Point, Polygon as ShPolygon, box as ShBox
from PIL import Image, ImageDraw, ImageFont
from skimage.morphology import thin, skeletonize
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo_v3.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002"
final_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final"

# 目标：1px = 40m（用户指定分辨率），地图部分按地理范围推出，右侧为编号图例区
PIXEL_TO_METER = 40.0
LEGEND_W = 480
MAX_PX = 20000
SIMPLIFY_TOL_M = 20   # 粗比例尺取舍：20m ≈ 0.3px，视觉不可见
TARGET_CRS = "EPSG:32649"
BAND_TOL_PX = 1.0     # 县市界判别容差：|d1-d2| ≤ 1px
MEDIAN_MAX_D_PX = 10  # 水面中线仅在水深 ≤10px 的河道/窄湾内生成
MEDIAN_MIN_LEN = 50   # 中线连通段最短像素数
# 不画水面中线的县市对：琼山-文昌（东寨港/铺前湾）、海口-琼山（南渡江河段）
EXCLUDE_MEDIAN_PAIRS = {("琼山市", "文昌市"), ("文昌市", "琼山市"),
                        ("海口市", "琼山市"), ("琼山市", "海口市")}

# ---------- 标注配置（字号按 1px=40m 分辨率设定，约为旧 4000px 版的 1.96 倍） ----------
FONT_MIN = 31         # 最小字号（px）
FONT_TINY = 35        # 微小乡镇初始字号
FONT_SMALL = 39       # 小乡镇初始字号
FONT_LARGE = 47       # 一般乡镇初始字号
FONT_XLARGE = 55      # 特大乡镇/县市初始字号（按行政区大小分档，大区大字）
TINY_AREA_PX = 10000    # ≈13 km²：低于此用微小字号
SMALL_AREA_PX = 34600   # ≈44 km²：低于此用小字号
XLARGE_AREA_PX = 185000  # ≈296 km²：高于此用特大字号
TEXT_MARGIN_PX = 4.0  # 文字框距本乡镇边界最小间隙（px）
LINE_STEP_PX = 6      # 平移搜索步长（px）
LINE_MAX_STEPS = 30   # 平移搜索最大步数
BASE_BUFFER_PX = 78   # 内缩质心基准距离（px）
HALO_MAX_FS = 39      # 字号 ≤ 此值的地图标签加白边晕圈（与细线隔离）

LEGEND_FONT = 44
LEGEND_X_PAD = 60
LEGEND_DY = 76
LEGEND_BOTTOM_PAD = 80  # 图例块距画布底边距离（右下角放置）

# 文字渲染：仿 PrintWord.py —— Matplotlib(DPI=100) 渲染 MingLiU + 阈值 200 二值化，
# 行距 1.5 倍字号，全图只保留纯黑/纯白两色
MPL_DPI = 100
BIN_THRESHOLD = 200

# 海口市密集核心街道 → 圆圈编号（顺序=图例顺序；1-12 与参考图一致，13 补金宇街道）
NUMBERED_STREETS = ["中山街道", "滨海街道", "金贸街道", "大同街道", "海垦街道", "国兴街道",
                    "海府街道", "博爱街道", "白龙街道", "蓝天街道", "和平南街道", "白沙街道",
                    "金宇街道"]
CIRCLE_R = 22         # 编号圆圈半径（px），放不下时逐级缩小

FORCED_WRAP = {
    "兴隆华侨农场": "兴隆华侨\n农场", "興隆華僑農場": "興隆華僑\n農場",
    "洋浦经济开发区": "洋浦经济\n开发区", "洋浦經濟開發區": "洋浦經濟\n開發區",
}

# ---------- 海口市保留名单 ----------
HAIKOU_KEEP = {
    "中山街道", "滨海街道", "金贸街道", "大同街道", "海垦街道", "国兴街道",
    "海府街道", "博爱街道", "白龙街道", "蓝天街道", "和平南街道", "白沙街道",
    "人民路街道", "海甸街道", "新埠街道", "海秀街道", "秀英街道", "金宇街道",
    "西秀镇", "长流镇", "海秀镇", "城西镇", "新海乡",
}
# ==================================================

warnings.filterwarnings("ignore")
os.makedirs(out_dir, exist_ok=True)

# ---------- 简→繁转换（与 Hainan.py / 参考图一致的繁体标注） ----------
try:
    from opencc import OpenCC
    _cc_s2t = OpenCC("s2t")
    def to_trad(s):
        return _cc_s2t.convert(s)
    print("✓ OpenCC 已启用（简→繁标注）")
except Exception:
    def to_trad(s):
        return s
    print("⚠️ OpenCC 未安装，使用简体标注")

# ---------- 字体（用户指定 MingLiU 細明體，取自用户字体目录） ----------
def find_font_file():
    for p in ["C:/Users/Windows/AppData/Local/Microsoft/Windows/Fonts/mingliu.TTF",
              "C:/Users/Windows/AppData/Local/Microsoft/Windows/Fonts/mingliu_0.TTF",
              "C:/Windows/Fonts/mingliu.ttc",
              "C:/Windows/Fonts/simsun.ttc"]:     # 兜底
        if os.path.exists(p):
            return p, 0
    return None, 0

FONT_PATH, FONT_IDX = find_font_file()
print("使用字体：", FONT_PATH)

# ---------- 生成"纯轮廓版"MingLiU（删嵌入点阵表）供 Matplotlib 使用 ----------
# mingliu.TTF 内嵌小字号点阵（EBDT/EBLC），Matplotlib/FreeType 渲染位图字形会输出
# 空白或实心色块（实测 11.5pt 圈号数字丢失）；删表后强制走 glyf 矢量轮廓，小字正常。
from fontTools import ttLib as _ttLib
MPL_FONT_PATH = FONT_PATH
try:
    _outline_cache = os.path.join(final_dir, "_mingliu_outline.ttf")
    if not os.path.exists(_outline_cache):
        _f = _ttLib.TTFont(FONT_PATH, fontNumber=FONT_IDX)
        for _tag in ("EBDT", "EBLC", "EBSC", "bdat", "bloc"):
            if _tag in _f:
                del _f[_tag]
        _f.save(_outline_cache)
    MPL_FONT_PATH = _outline_cache
    print("Matplotlib 用纯轮廓字体：", MPL_FONT_PATH)
except Exception as _e:
    print("⚠️ 点阵表删除失败，Matplotlib 直接用原字体：", _e)
_fonts = {}
def get_font(fs):
    if fs not in _fonts:
        _fonts[fs] = ImageFont.truetype(FONT_PATH, fs, index=FONT_IDX)
    return _fonts[fs]

_scratch = ImageDraw.Draw(Image.new("L", (8, 8)))
LH = 1.5  # 多行文字行距系数（与 PrintWord.py 一致）

def measure(lines, font):
    """多行文字块外包尺寸（px）"""
    w = 0
    for ln in lines:
        l, t, r, b = _scratch.textbbox((0, 0), ln, font=font)
        w = max(w, r - l)
    h = (len(lines) - 1) * font.size * LH + font.size
    return w, h

# ---------- 读取 SHP ----------
gdf = gpd.read_file(shp_path, encoding="utf-8")
print(f"原始要素数：{len(gdf)}")
gdf = gdf.to_crs(TARGET_CRS)
gdf["geometry"] = gdf.geometry.buffer(0)

if SIMPLIFY_TOL_M > 0:
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_TOL_M, preserve_topology=True)
print(f"几何清理完成（简化 {SIMPLIFY_TOL_M}m），有效 {int(gdf.geometry.is_valid.sum())}/{len(gdf)}")

# ---------- 海口市重划 · 县市归属 ----------
haikou = gdf[gdf["CITY"] == "海口市"].copy()
haikou["县市"] = haikou["TOWN"].apply(
    lambda t: "海口市" if t in HAIKOU_KEEP else "琼山市")
others = gdf[gdf["CITY"] != "海口市"].copy()
others["县市"] = others["CITY"]

county_df = gpd.GeoDataFrame(
    pd.concat([haikou[["县市", "geometry"]], others[["县市", "geometry"]]], ignore_index=True),
    crs=TARGET_CRS)
kept = (haikou["县市"] == "海口市").sum()
moved = (haikou["县市"] == "琼山市").sum()
print(f"海口市保留 {kept} 个乡镇，划入琼山市 {moved} 个乡镇")

township = county_df.copy()
counties = county_df.dissolve(by="县市").reset_index()
print(f"县市单元数：{len(counties)}")

# ---------- 画布尺寸（1px = 40m，用户指定） ----------
minx, miny, maxx, maxy = township.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
MAP_W = int(math.ceil(geo_w / PIXEL_TO_METER))
w_px = MAP_W
h_px = int(math.ceil(geo_h / PIXEL_TO_METER))
if w_px > MAX_PX or h_px > MAX_PX:
    scale = min(MAX_PX / w_px, MAX_PX / h_px)
    w_px, h_px = int(round(w_px * scale)), int(round(h_px * scale))
    PIXEL_TO_METER = geo_w / w_px
sx, sy = w_px / geo_w, h_px / geo_h
CANVAS_W = w_px + LEGEND_W
CANVAS_H = h_px
Y_OFF = 0                            # 画布高度贴合地图，无额外留白
LEGEND_Y0 = (CANVAS_H - LEGEND_BOTTOM_PAD
             - (len(NUMBERED_STREETS) - 1) * LEGEND_DY)  # 图例块置于画布右下角
print(f"比例尺 {PIXEL_TO_METER:.1f} m/px，范围 {geo_w:.0f}×{geo_h:.0f} m，"
      f"地图 {w_px}×{h_px}，画布 {CANVAS_W}×{CANVAS_H}（图例右下角 y0={LEGEND_Y0}）")

# ---------- 行政区着色（相邻不同色，图着色 · 贪心 5 色） ----------
print("\n行政区着色（相邻不同色）...")
PALETTE = [(0xc7, 0xcd, 0xe7),   # 浅紫蓝
           (0xf8, 0xc7, 0xdc),   # 浅粉
           (0xfa, 0xe0, 0xbf),   # 浅橙
           (0xff, 0xfd, 0xd7),   # 浅黄
           (0xc5, 0xe4, 0xd4)]   # 浅绿
N_COLORS = len(PALETTE)

_adj_geoms = list(township.geometry)
_n_t = len(_adj_geoms)
from shapely.strtree import STRtree
_tree = STRtree(_adj_geoms)
adj = [set() for _ in range(_n_t)]
for _i, _g in enumerate(_adj_geoms):
    _gb = _g.boundary
    for _j in _tree.query(_g.buffer(100.0)):      # 100m 缓冲取候选
        _j = int(_j)
        if _j == _i:
            continue
        # 共享边界 >50m 才算相邻（排除仅角落相触）
        if _gb.intersection(_adj_geoms[_j].boundary).length > 50.0:
            adj[_i].add(_j)
            adj[_j].add(_i)

color_of = [-1] * _n_t
for _i in sorted(range(_n_t), key=lambda k: -len(adj[k])):   # 度数降序（难点先着色）
    used = {color_of[j] for j in adj[_i] if color_of[j] >= 0}
    color_of[_i] = next((c for c in range(N_COLORS) if c not in used),
                        min(range(N_COLORS),
                            key=lambda c: sum(1 for j in adj[_i] if color_of[j] == c)))
_conf = sum(1 for i in range(_n_t) for j in adj[i]
            if j > i and color_of[i] == color_of[j])
print(f"乡镇数 {_n_t}，邻接对 {sum(len(a) for a in adj) // 2}，"
      f"着色冲突 {int(_conf)}（应为 0），色数 {N_COLORS}")

# ---------- 收集矢量边界线 ----------
def extract_lines(gs):
    if hasattr(gs, "geoms"):
        parts = list(gs.geoms)
    else:
        parts = [gs]
    out = []
    for g in parts:
        if g is None or g.is_empty:
            continue
        if g.geom_type == "LineString":
            out.append(list(g.coords))
        elif g.geom_type == "MultiLineString":
            out.extend(list(ls.coords) for ls in g.geoms)
        elif g.geom_type == "GeometryCollection":
            out.extend(extract_lines(g))
    return out

# ---------- Bresenham 逐段描绘函数 ----------
def draw_layer(lines, fill_polys=None):
    im = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(im)
    if fill_polys:
        for coords in fill_polys:
            pts = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
                   for x, y in coords]
            if len(pts) >= 3:
                d.polygon(pts, fill=0)
    for coords in lines:
        pts = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
               for x, y in coords]
        if len(pts) >= 2:
            d.line(pts, fill=0, width=1)
    return np.asarray(im) < 128

# ---------- 乡镇层：1px 描绘 + 拓扑保持细化 ----------
print("乡镇层 1px 描绘：")
town_lines = extract_lines(unary_union(township.geometry.boundary.tolist()))
town_arr = draw_layer(town_lines)
town_skel = thin(town_arr)
print(f"乡镇骨架像素：{int(town_skel.sum())}")

# ---------- 县市层：最近两侧县市距离场判别 ----------
print("县市层：距离场判别（最近两侧县市）...")
county_names = counties["县市"].tolist()
county_geoms = counties.geometry.values
n_c = len(county_names)

d1 = np.full((h_px, w_px), np.inf, dtype=np.float32)
c1 = np.full((h_px, w_px), -1, dtype=np.int8)
d2 = np.full((h_px, w_px), np.inf, dtype=np.float32)
c2 = np.full((h_px, w_px), -1, dtype=np.int8)
land = np.zeros((h_px, w_px), dtype=bool)

for k in range(n_c):
    im = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(im)
    for poly in (county_geoms[k].geoms if hasattr(county_geoms[k], "geoms")
                 else [county_geoms[k]]):
        if poly.is_empty:
            continue
        for ring in [poly.exterior] + list(poly.interiors):
            pts = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
                   for x, y in ring.coords]
            if len(pts) >= 3:
                d.polygon(pts, fill=0)
    m = np.asarray(im) < 128
    land |= m
    dist = ndimage.distance_transform_edt(~m).astype(np.float32)
    lt = dist < d1
    take2 = (~lt) & (dist < d2)
    d2 = np.where(lt, d1, np.where(take2, dist, d2))
    c2 = np.where(lt, c1, np.where(take2, k, c2))
    d1 = np.where(lt, dist, d1)
    c1 = np.where(lt, k, c1)

water = ~land
band = (c1 != c2) & (c1 >= 0) & (c2 >= 0) & ((d2 - d1) <= BAND_TOL_PX)
print(f"判别带像素：{int(band.sum())}（陆地部分 {int((band & land).sum())}，水面部分 {int((band & water).sum())}）")

d_to_skel = ndimage.distance_transform_edt(~town_skel)
anchor = town_skel & band & land
n_sk = int(anchor.sum())
fallback_n = band & land & (d_to_skel > 1.5)
anchor |= fallback_n
print(f"锚点：骨架命中 {n_sk}，骨架缺失补点 {int(fallback_n.sum())}")

pair_id = c1.astype(np.int32) * n_c + c2
excl = np.zeros((h_px, w_px), dtype=bool)
name_idx = {n: i for i, n in enumerate(county_names)}
for a, b in EXCLUDE_MEDIAN_PAIRS:
    excl |= (pair_id == name_idx[a] * n_c + name_idx[b]) | \
            (pair_id == name_idx[b] * n_c + name_idx[a])
median_zone = band & water & (d1 <= MEDIAN_MAX_D_PX) & ~excl
if median_zone.sum() > 0:
    med_skel = skeletonize(median_zone)
    lbl, n = ndimage.label(med_skel, structure=np.ones((3, 3), bool))
    if n:
        sizes = ndimage.sum(med_skel, lbl, range(1, n + 1))
        keep = np.isin(lbl, np.where(sizes >= MEDIAN_MIN_LEN)[0] + 1)
        dropped = int(med_skel.sum() - keep.sum())
        med_skel = keep
    else:
        dropped = 0
    print(f"水面中线骨架 {int(med_skel.sum())} px（剔除碎须 {dropped} px）")
    anchor = anchor | med_skel
else:
    print("水面中线：无")

# ---------- 加粗为 2px ----------
S2 = np.ones((2, 2), dtype=bool)
county_arr = ndimage.binary_dilation(anchor, structure=S2)

# ---------- 合并底图（乡镇按色光栅化填充 + 黑色界线叠加） ----------
lines_mask = town_skel | county_arr
fill_arr = np.full((h_px, w_px, 3), 255, dtype=np.uint8)
for _i in range(_n_t):
    _g = _adj_geoms[_i]
    _parts = _g.geoms if hasattr(_g, "geoms") else [_g]
    _col = PALETTE[color_of[_i]]
    for _poly in _parts:
        if _poly is None or _poly.is_empty or _poly.geom_type != "Polygon":
            continue
        _ext = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
                for x, y in _poly.exterior.coords]
        _xs = [p[0] for p in _ext]
        _ys = [p[1] for p in _ext]
        _x0 = max(0, min(_xs)); _x1 = min(w_px, max(_xs) + 1)
        _y0 = max(0, min(_ys)); _y1 = min(h_px, max(_ys) + 1)
        if _x1 <= _x0 or _y1 <= _y0:
            continue
        _im = Image.new("L", (_x1 - _x0, _y1 - _y0), 0)
        _dd = ImageDraw.Draw(_im)
        _dd.polygon([(px - _x0, py - _y0) for px, py in _ext], fill=1)
        for _ring in _poly.interiors:      # 内环（洞）保持底色
            _rin = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
                    for x, y in _ring.coords]
            _dd.polygon([(px - _x0, py - _y0) for px, py in _rin], fill=0)
        _m = np.asarray(_im, dtype=bool)
        fill_arr[_y0:_y1, _x0:_x1][_m] = _col

rgb_map = fill_arr.copy()
rgb_map[lines_mask] = 0     # 界线（乡镇 1px + 县市 2px）以纯黑叠加在彩填上

# ===================== 地名标注 =====================
map_img = Image.fromarray(rgb_map, mode="RGB")
canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (255, 255, 255))
canvas.paste(map_img, (0, Y_OFF))
d = ImageDraw.Draw(canvas)
text_jobs = []   # (lines, fs, cx, cy_canvas, anchor, halo) —— 文字统一 Matplotlib 渲染后二值化

placed = []          # 已放置标签/圆圈的占用框（shapely box，px）
circle_info = []     # (编号, 街道名, 半径, x, y) —— 用于叠压自检
DIRS = [(math.cos(math.radians(a)), math.sin(math.radians(a)))
        for a in np.arange(0, 360, 22.5)]

def spot_ok(p, w, h, boundary, margin=TEXT_MARGIN_PX):
    """文字框是否既不压本乡镇边界、也不与已放置标签冲突"""
    bx = ShBox(p.x - w / 2 - margin, p.y - h / 2 - margin,
               p.x + w / 2 + margin, p.y + h / 2 + margin)
    if bx.intersects(boundary):
        return None
    for pb in placed:
        if bx.intersects(pb):
            return None
    return bx

def translate_search(geom, boundary, p0, w, h, step=LINE_STEP_PX, max_steps=LINE_MAX_STEPS):
    """16 方向平移搜索可放置位置"""
    best, best_d = None, -1.0
    for ddx, ddy in DIRS:
        for i in range(1, max_steps + 1):
            p = Point(p0.x + ddx * step * i, p0.y + ddy * step * i)
            if not geom.contains(p):
                break
            bx = spot_ok(p, w, h, boundary)
            if bx is not None:
                return p, bx
            dd = p.distance(boundary)
            if dd > best_d:
                best_d, best = dd, p
    return best, None

def candidate_points(geom):
    cands = [geom.centroid, geom.representative_point()]
    for r in (0.8, 0.5, 0.3, 0.1):
        try:
            b = geom.buffer(-BASE_BUFFER_PX * r)
            if b.is_valid and not b.is_empty and b.area > 50:
                cands.append(b.centroid)
        except Exception:
            pass
    out = []
    for p in cands:
        try:
            if not (geom.contains(p) or geom.touches(p)):
                continue
        except Exception:
            continue
        if any(p.distance(q) < 0.5 for q in out):
            continue
        out.append(p)
    return out

def _draw_lines(p, lines, font, halo=False):
    """登记文字任务（地图坐标 → 画布坐标），稍后统一 Matplotlib 渲染 + 二值化"""
    text_jobs.append((list(lines), font.size, p.x, p.y + Y_OFF, "mm", halo))

def build_text_variants(name):
    """原样 → 后缀断行（XX街道/镇/乡/区）→ 平衡断行"""
    variants = [name]
    def add(t):
        if t and t not in variants:
            variants.append(t)
    for suf in ("街道", "镇", "乡", "区"):
        if name.endswith(suf) and len(name) - len(suf) >= 2:
            add(name[:-len(suf)] + "\n" + suf)
            break
    if len(name) >= 5 and "\n" not in name:
        half = (len(name) + 1) // 2
        add(name[:half] + "\n" + name[half:])
    return variants

def _block(p, w, h):
    return ShBox(p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2)

def _sep(bx, pb):
    """带符号分离度：不相交返回框间距（越大越好），
    相交返回负的交叠深度（两框沿最浅轴推开即可分离的最小平移量，
    叠得越深越负）。注意不能用 -质心距：同心深叠质心距反而小，
    会得到比浅叠更高的分数，方向完全颠倒。"""
    if bx.intersects(pb):
        ax0, ay0, ax1, ay1 = bx.bounds
        bx0, by0, bx1, by1 = pb.bounds
        ow = min(ax1, bx1) - max(ax0, bx0)
        oh = min(ay1, by1) - max(ay0, by0)
        return -min(ow, oh)
    return bx.distance(pb)

def _blocked(p, w, h, boundary):
    bx = _block(p, w, h)
    if bx.intersects(boundary):
        return True
    return any(bx.intersects(pb) for pb in placed)

def _avoid_spot(geom, boundary, w, h, radius=24, step=2):
    """保底位置：代表点被占用时，就近挪到不压边界、不撞已放置标签的净地；
    实在无处可挪则取离边界最远的点（配合描边直放）"""
    p0 = geom.representative_point()
    best, best_s = None, -1.0
    for ddx, ddy in DIRS:
        for i in range(1, radius // step + 1):
            p = Point(p0.x + ddx * step * i, p0.y + ddy * step * i)
            if not geom.contains(p):
                break
            if _blocked(p, w, h, boundary):
                continue
            bx = _block(p, w, h)
            s = bx.distance(boundary)
            for pb in placed:
                s = min(s, bx.distance(pb))
            if s > best_s:
                best_s, best = s, p
    if best is not None:
        return best
    # 无净地：取"与已放置标签分离度 + 离边界距离"综合最优的点（描边直放）
    best, best_s = p0, -1e18
    for ddx, ddy in DIRS:
        for i in range(1, radius // step + 1):
            p = Point(p0.x + ddx * step * i, p0.y + ddy * step * i)
            if not geom.contains(p):
                break
            bx = _block(p, w, h)
            s = min((_sep(bx, pb) for pb in placed), default=0.0) + p.distance(boundary)
            if s > best_s:
                best_s, best = s, p
    return best

def draw_text_label(geom, name):
    """返回 True=常规放置成功；False=保底描边放置"""
    boundary = geom.boundary
    area_px = geom.area
    if area_px < TINY_AREA_PX:
        f_def = FONT_TINY
    elif area_px < SMALL_AREA_PX:
        f_def = FONT_SMALL
    elif area_px < XLARGE_AREA_PX:
        f_def = FONT_LARGE
    else:
        f_def = FONT_XLARGE  # 特大行政区用大字（按行政区大小调整字体大小）
    variants = build_text_variants(name)
    cands = candidate_points(geom) or [geom.representative_point()]
    for fs in range(f_def, FONT_MIN - 1, -1):
        font = get_font(fs)
        for txt in variants:
            lines = txt.split("\n")
            w, h = measure(lines, font)
            for p0 in cands:
                bx = spot_ok(p0, w, h, boundary)
                if bx is not None:
                    _draw_lines(p0, lines, font, halo=(fs <= HALO_MAX_FS))
                    placed.append(ShBox(p0.x - w / 2, p0.y - h / 2, p0.x + w / 2, p0.y + h / 2))
                    return True
                if geom.area < w * h:
                    continue  # 多边形根本装不下，平移无意义
                p2, bx2 = translate_search(geom, boundary, p0, w, h)
                if p2 is not None and bx2 is not None:
                    _draw_lines(p2, lines, font, halo=(fs <= HALO_MAX_FS))
                    placed.append(ShBox(p2.x - w / 2, p2.y - h / 2, p2.x + w / 2, p2.y + h / 2))
                    return True
    # 保底：全变体中选最紧凑者，先做避让搜索（避开已放置标签），仍压线则描边直放
    font = get_font(FONT_MIN)
    best_txt, best_w, best_h = None, None, None
    for txt in variants:
        lines = txt.split("\n")
        w, h = measure(lines, font)
        if best_txt is None or w + h < best_w + best_h:
            best_txt, best_w, best_h = txt, w, h
    p = _avoid_spot(geom, boundary, best_w, best_h)
    lines = best_txt.split("\n")
    halo = _blocked(p, best_w, best_h, boundary)
    _draw_lines(p, lines, font, halo=halo)
    placed.append(_block(p, best_w, best_h))
    return False

def _fit_number_font(snum, r):
    fs = int(r * 1.5) if len(snum) == 1 else int(r * 1.15)
    while fs > 8:
        bb = _scratch.textbbox((0, 0), snum, font=get_font(fs))
        if bb[2] - bb[0] <= 2 * r - 5:
            break
        fs -= 1
    return get_font(fs)

def _draw_circle(p, r, snum, fnt):
    """圆圈用 PIL 硬边直接绘制（纯黑白），圈内数字走文字任务（不加晕圈）"""
    cy = p.y + Y_OFF
    d.ellipse([p.x - r, cy - r, p.x + r, cy + r],
              fill=(255, 255, 255), outline=(0, 0, 0), width=(4 if r >= 20 else 2))
    text_jobs.append(([snum], fnt.size, p.x, cy, "mm", False))

def draw_number_label(geom, num, name):
    boundary = geom.boundary
    snum = str(num)
    # 半径上限随多边形内切尺寸自适应：微小街道用小圈，避免圈远大于图斑
    if geom.area >= math.pi * CIRCLE_R ** 2:
        rmax = CIRCLE_R
    else:
        rmax = min(CIRCLE_R, max(14, int(math.sqrt(geom.area / math.pi) * 0.75)))
    # 主循环半径严格不超过 rmax：从 min(rmax, CIRCLE_R) 逐级降到 14
    #（14 为圈数字可读下限：fs=int(14×1.15)=16px）
    radii = list(range(min(rmax, CIRCLE_R), 13, -1))
    for r in radii:
        fnt = _fit_number_font(snum, r)
        w = h = 2.0 * r
        for p0 in candidate_points(geom) or [geom.representative_point()]:
            bx = spot_ok(p0, w, h, boundary, margin=1.2)
            if bx is None and geom.area > w * h:
                p0b, bxb = translate_search(geom, boundary, p0, w, h, step=2, max_steps=12)
                if p0b is not None and bxb is not None:
                    p0, bx = p0b, bxb
            if bx is not None:
                _draw_circle(p0, r, snum, fnt)
                placed.append(ShBox(p0.x - r, p0.y - r, p0.x + r, p0.y + r))
                circle_info.append((num, name, r, p0.x, p0.y))
                print(f"  [圈{num} {name}] 主循环 r={r} @({p0.x:.1f},{p0.y:.1f}) 面积{geom.area:.0f}px²")
                return
    # 保底：圈心始终留在自家多边形内部（防止闯入邻街引发连锁叠压），
    # 在此前提下小步挪动、以带符号分离度打分尽量远离已放置的圈/标签；
    # 大半径找不到净地时逐级缩小圆圈再试（更小的圈腾出分离空间）；
    # 相邻微小街道的圈允许轻微相切（与参考图一致）
    p0 = geom.representative_point()
    best_s, best_r, best_p, best_fnt = -1e18, None, p0, None
    for r in range(min(rmax, 18), 13, -1):
        fnt = _fit_number_font(snum, r)
        for ddx, ddy in DIRS:
            for i in range(0, 8):
                p = Point(p0.x + ddx * 3.0 * i, p0.y + ddy * 3.0 * i)
                if i > 0 and not geom.contains(p):
                    continue  # 圈心必须留在自家图斑内
                bx = _block(p, 2 * r, 2 * r)
                s = min((_sep(bx, pb) for pb in placed), default=0.0)
                s += 0.05 * min(p.distance(boundary), 10.0)  # 偏好靠内
                if s > best_s:
                    best_s, best_r, best_p, best_fnt = s, r, p, fnt
        if best_s >= 0:
            break  # 当前半径已有净地，不必更小
    # 二轮精修：负分（浅叠）时以 1px 小步长在 best_p 附近微调，
    # 粗搜索 3px 步长常差 2~4px 才能分离，细网格可把浅叠修成零叠；
    # 打分口径必须与粗搜索一致（含偏内项），否则系统性偏低永远赢不了 best_s
    if best_s < 0 and best_r is not None:
        for ddx in range(-16, 17):
            for ddy in range(-16, 17):
                p = Point(best_p.x + ddx * 1.0, best_p.y + ddy * 1.0)
                if not geom.contains(p):
                    continue  # 圈心必须留在自家图斑内
                bx = _block(p, 2 * best_r, 2 * best_r)
                s = min((_sep(bx, pb) for pb in placed), default=0.0)
                s += 0.05 * min(p.distance(boundary), 10.0)
                if s > best_s:
                    best_s, best_p = s, p
    r = best_r
    _draw_circle(best_p, r, snum, best_fnt)
    placed.append(_block(best_p, 2 * r, 2 * r))
    circle_info.append((num, name, r, best_p.x, best_p.y))
    print(f"  [圈{num} {name}] 保底 r={r} @({best_p.x:.1f},{best_p.y:.1f}) "
          f"rep=({p0.x:.1f},{p0.y:.1f}) 面积{geom.area:.0f}px² score={best_s:.2f}")

def to_px_geom(g):
    """米制几何 → 像素几何（取最大图斑用于标注）"""
    parts = list(g.geoms) if hasattr(g, "geoms") else [g]
    best = None
    for poly in parts:
        if poly is None or poly.is_empty or poly.geom_type != "Polygon":
            continue
        ext = [((x - minx) * sx, (maxy - y) * sy) for x, y in poly.exterior.coords]
        ints = [[((x - minx) * sx, (maxy - y) * sy) for x, y in ring.coords]
                for ring in poly.interiors]
        try:
            sp = ShPolygon(ext, ints)
        except Exception:
            continue
        if best is None or sp.area > best.area:
            best = sp
    return best

def trad_of(name):
    t = to_trad(str(name).strip())
    return FORCED_WRAP.get(t, t)

print("\n地名标注 ...")
lab = pd.concat([haikou, others], ignore_index=True)
lab["area_m2"] = lab.geometry.area
num_of = {n: i + 1 for i, n in enumerate(NUMBERED_STREETS)}
n_fallback = 0
n_numbered = 0

# 编号圈按图斑面积升序（最难放的最先占位），文字标注按面积降序（大而灵活的先放）
rows_num, rows_txt = [], []
for _, row in lab.iterrows():
    name = str(row["TOWN"]).strip()
    city = str(row["CITY"]).strip()
    if city == "海口市" and name in num_of:
        rows_num.append((row["area_m2"], name, row.geometry))
    else:
        rows_txt.append((row["area_m2"], name, row.geometry))

for area_m2, name, gm in sorted(rows_num, key=lambda t: t[0]):
    gpx = to_px_geom(gm)
    if gpx is None or gpx.is_empty:
        continue
    draw_number_label(gpx, num_of[name], name)
    n_numbered += 1

for area_m2, name, gm in sorted(rows_txt, key=lambda t: -t[0]):
    gpx = to_px_geom(gm)
    if gpx is None or gpx.is_empty:
        continue
    if not draw_text_label(gpx, trad_of(name)):
        n_fallback += 1

print(f"编号圆圈：{n_numbered} 个；普通标注 {len(lab) - n_numbered} 个（其中保底描边 {n_fallback} 个）")
for i in range(len(circle_info)):
    for j in range(i + 1, len(circle_info)):
        n1, nm1, r1, x1, y1 = circle_info[i]
        n2, nm2, r2, x2, y2 = circle_info[j]
        dist = math.hypot(x1 - x2, y1 - y2)
        if dist < r1 + r2:
            print(f"  ⚠️ 圈号叠压：圈{n1}{nm1}(r{r1}) 与 圈{n2}{nm2}(r{r2}) 中心距 {dist:.1f}px")

# ---------- 图例（画布右下角） ----------
for i, nm in enumerate(NUMBERED_STREETS, 1):
    text_jobs.append(([f"{i}. {to_trad(nm)}"], LEGEND_FONT,
                      w_px + LEGEND_X_PAD, LEGEND_Y0 + (i - 1) * LEGEND_DY, "lm", False))

# ---------- 文字渲染：仿 PrintWord.py —— Matplotlib(DPI=100) 渲染 MingLiU → 阈值 200 二值化 ----------
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib import font_manager as _fm

# 注意：必须用 FontProperties(fname=...) 精确锁定轮廓版文件——若按家族名
# "MingLiU" 解析，fontManager 会选中系统扫描到的原版 mingliu.TTF（带点阵），
# addfont 的轮廓版形同虚设，小字号仍会渲染成空白/色块
MPL_FP = _fm.FontProperties(fname=MPL_FONT_PATH)
MPL_FAMILY = MPL_FP.get_name()
print(f"Matplotlib 文字渲染：family={MPL_FAMILY}，DPI={MPL_DPI}，阈值={BIN_THRESHOLD}，行距={LH}")

m_halo = np.zeros((CANVAS_H, CANVAS_W), dtype=bool)
m_plain = np.zeros((CANVAS_H, CANVAS_W), dtype=bool)

def _mpl_text_mask(lines, fs, anchor):
    """Matplotlib 渲染一个文字任务（黑字白底，DPI=100，行距 LH），
    返回 (bool 掩膜, 掩膜应贴到画布的左上角坐标 ox, oy)。
    阈值 200 会把抗锯齿灰边一并归入文字（与 PrintWord.py 效果一致，笔画饱满）"""
    fs_pt = fs * 72.0 / MPL_DPI            # px → pt（1pt @100dpi = 1.389px）
    pad = max(4, fs // 4)
    font_p = get_font(fs)
    w_est = max(_scratch.textbbox((0, 0), ln, font=font_p)[2] for ln in lines)
    h_est = (len(lines) - 1) * fs * LH + fs * 1.35
    W = int(math.ceil(w_est * 1.25)) + 2 * pad
    H = int(math.ceil(h_est * 1.25)) + 2 * pad
    fig = Figure(figsize=(W / MPL_DPI, H / MPL_DPI), dpi=MPL_DPI, facecolor="white")
    cvs = FigureCanvasAgg(fig)             # 直接实例化的 Figure 必须显式挂 Agg canvas
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    tx = ax.text(W / 2, H / 2, "\n".join(lines), fontsize=fs_pt,
                 fontproperties=MPL_FP, linespacing=LH,
                 ha="center", va="center", color="black")
    cvs.draw()
    bb = tx.get_window_extent(renderer=cvs.get_renderer())
    buf = np.asarray(cvs.buffer_rgba())
    mask = buf[:, :, 0] < BIN_THRESHOLD
    # 文字 bbox 中心在 tile 内的位置（窗口坐标 y 向上 → 画布 y 向下翻转）
    bcx = (bb.x0 + bb.x1) / 2.0
    bcy = H - (bb.y0 + bb.y1) / 2.0
    if anchor == "mm":
        ox, oy = cx - bcx, cy - bcy
    else:  # "lm" 左对齐、垂直居中（图例）
        ox, oy = cx - bb.x0, cy - bcy
    return mask, ox, oy

for lines, fs, cx, cy, anchor, halo in text_jobs:
    small, ox, oy = _mpl_text_mask(lines, fs, anchor)
    th, tw = small.shape
    tgt = m_halo if halo else m_plain
    x0i, y0i = int(round(ox)), int(round(oy))
    xs0, ys0 = max(0, x0i), max(0, y0i)
    xs1, ys1 = min(CANVAS_W, x0i + tw), min(CANVAS_H, y0i + th)
    if xs1 > xs0 and ys1 > ys0:
        tgt[ys0:ys1, xs0:xs1] |= small[ys0 - y0i:ys1 - y0i, xs0 - x0i:xs1 - x0i]

m_all = m_halo | m_plain
halo_arr = ndimage.binary_dilation(m_halo, iterations=4) & ~m_all  # 白边晕圈（避开界线）
arr = np.array(canvas)
arr[halo_arr] = 255
arr[m_all] = 0
canvas = Image.fromarray(arr, mode="RGB")
# 颜色验证：全图应只含 黑界线/文字、白底、5 种填充色，无抗锯齿中间色
_packed = ((arr[:, :, 0].astype(np.uint32) << 16)
           | (arr[:, :, 1].astype(np.uint32) << 8) | arr[:, :, 2])
_uniq = np.unique(_packed)
_allowed = {(0x000000,), (0xFFFFFF,)} | {
    (c[0] << 16 | c[1] << 8 | c[2],) for c in PALETTE}
_unexpected = [v for v in _uniq.tolist() if (v,) not in _allowed]
print(f"全图颜色种类：{len(_uniq)}（应 ≤7：黑/白/5 填充色），未预期颜色 {len(_unexpected)} 种")

# ---------- 保存 ----------
out_file = os.path.join(out_dir, "Hainan_town_county_town1_county2_labeled.png")
canvas.save(out_file, format="png")
print(f"已保存 {out_file}（{CANVAS_W}×{CANVAS_H}）")

# ---------- 诊断裁片 ----------
def crop_save(mask, path, pad=120):
    sub = lab[mask]
    if len(sub) == 0:
        return
    a, b, c, e = sub.total_bounds  # 米制 minx,miny,maxx,maxy
    x0 = int(max(0, (a - minx) * sx - pad))
    y0 = int(max(0, Y_OFF + (maxy - e) * sy - pad))
    x1 = int(min(w_px, (c - minx) * sx + pad))
    y1 = int(min(CANVAS_H, Y_OFF + (maxy - b) * sy + pad))
    canvas.crop((x0, y0, x1, y1)).save(path)
    print("诊断裁片:", path, (x1 - x0, y1 - y0))

num_set = set(NUMBERED_STREETS)
crop_save((lab["CITY"] == "海口市") & (lab["TOWN"].isin(num_set) | (lab["area_m2"] < 10e6)),
          os.path.join(final_dir, "diag_label_haikou.png"))
crop_save((lab["CITY"] == "三亚市") & (lab["area_m2"] < 30e6),
          os.path.join(final_dir, "diag_label_sanya.png"))
canvas.crop((w_px + 20, LEGEND_Y0 - 40, CANVAS_W,
             LEGEND_Y0 + len(NUMBERED_STREETS) * LEGEND_DY + 40)).save(
    os.path.join(final_dir, "diag_label_legend.png"))

print("\n✅ 标注版地图生成完毕")
