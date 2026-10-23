# -*- coding: utf-8 -*-
"""
华南县级界线图 · 每省一种填色（画布上限 4000×4000）

数据源：2广东全省/华南乡镇界线.shp（省 / 市 / 县 / 乡镇 …）
  · 按「省|市|县」融合为县级单元，只画县界；
  · 县名为空的地级市（东莞、中山这类不设区县）回退用市名，各算一个单元；
  · 广东、广西、海南、香港、澳门全部生成，每省/特区一种填色；
  · 香港、澳门各为单一单元，不拆分，维持现况。

绘制：
  ① 县界严格 1px：Bresenham 逐段直绘 → skimage.thin → deblock_2x2；
  ② 按省填色（PROVINCE_COLORS），县界仍为 1px 黑线；
  ③ 离岸小岛用 all_touched 补色；接缝小洞/封闭白点按邻近省份颜色补齐；
  ④ 窄缝闭合只对内陆县：临海县、离岛、海岸线一律保持原样，不做合并/平滑；
  ⑤ 画布上限 4000×4000 px（等比缩放）。

特殊合并区（乡镇码）：
  RED_ORANGE（红+橙合并区）→ 灵山县；
  BLUE（蓝色合并区）      → 450108（良庆区）。

输出：2广东全省/华南县界_分省设色_4000.png
"""
import os
import sys
import math
import warnings

import geopandas as gpd
import numpy as np
from PIL import Image, ImageDraw
from shapely.ops import unary_union
from shapely.geometry import MultiLineString
from scipy import ndimage
from skimage.morphology import thin
import cv2
from rasterio.features import rasterize
from rasterio.transform import from_bounds

Image.MAX_IMAGE_PIXELS = None
warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2广东全省/华南乡镇界线.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2广东全省"
out_file = os.path.join(out_dir, "华南县界_分省设色_4000.png")

MAX_PX = 4000                 # 画布上限 4000×4000
TARGET_PX_TO_METER = 40.0     # 目标 1px=40m（被上限缩放）
SIMPLIFY_TOL_M = 40.0         # 几何简化容差（米）
LINE_PX = 1                   # 县界严格 1px
BG_COLOR = (255, 255, 255)    # 海面/背景
LINE_COLOR = (0, 0, 0)        # 界线
WHITE_FILL_MAX_PX = 200       # 被线围住的小白点回填阈值
DISSOLVE_CLOSE_M = 200.0      # 仅内陆县：闭合同县乡镇间窄缝（≤400m）

# 每省/特区一种柔和色（相邻省份尽量拉开色差）
PROVINCE_COLORS = {
    "广东省": (223, 235, 247),          # 淡蓝
    "广西壮族自治区": (232, 238, 214),  # 淡绿
    "海南省": (247, 231, 214),          # 淡橙
    "香港特别行政区": (240, 220, 232),  # 淡粉
    "澳门特别行政区": (230, 224, 245),  # 淡紫
}
# 兜底色（数据里出现未知省名时循环使用）
FALLBACK_COLORS = [
    (235, 230, 216), (216, 235, 232), (238, 224, 216),
    (228, 228, 245), (222, 238, 222), (245, 228, 238),
]
# ==================================================

os.makedirs(out_dir, exist_ok=True)


# ===================== 线稿工具 =====================
def to_px(coords, minx, maxy, sx, sy):
    """像素网格约定：像素 i 覆盖 [i, i+1)，与 rasterio 中心规则一致。"""
    return [(int(math.floor((x - minx) * sx)), int(math.floor((maxy - y) * sy)))
            for x, y in coords]


def exteriors(g):
    """返回几何的所有外环（县界只取外环，忽略内部小孔，避免画出自造环线）。"""
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [list(g.exterior.coords)]
    if g.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for p in g.geoms:
            out.extend(exteriors(p))
        return out
    return []


def draw_layer(lines, w_px, h_px, minx, maxy, sx, sy):
    """Bresenham 逐段描绘 1px 纯黑线，返回 bool 数组。"""
    im = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(im)
    for coords in lines:
        pts = to_px(coords, minx, maxy, sx, sy)
        if len(pts) >= 2:
            d.line(pts, fill=0, width=LINE_PX)
    return np.asarray(im) < 128


def deblock_2x2(mask, passes=4):
    """清除 2×2 全黑粗块，保证线网严格 1px。"""
    m = mask.copy()
    for _ in range(passes):
        blk = m[:-1, :-1] & m[:-1, 1:] & m[1:, :-1] & m[1:, 1:]
        if not blk.any():
            break
        m[1:, 1:][blk] = False
    return m


def save_png(img, path):
    """保存 PNG；目标被占用时自动改存 *_新.png。"""
    try:
        img.save(path, format="png")
        return path
    except OSError as e:
        alt = os.path.splitext(path)[0] + "_新.png"
        img.save(alt, format="png")
        print(f"  ⚠️ 目标被占用（{e}），改存 {alt}")
        return alt


# ===================== 主流程 =====================
print("=" * 60)
print("  华南县级界线图 · 每省一种填色（上限 4000×4000）")
print("=" * 60)

print("读取:", shp_path)
gdf = gpd.read_file(shp_path, encoding="UTF-8")
print(f"原始要素数：{len(gdf)}")
gdf = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
gdf["geometry"] = gdf.geometry.buffer(0)
gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty].copy()
print(f"面状要素：{len(gdf)}")

if SIMPLIFY_TOL_M > 0:
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_TOL_M, preserve_topology=True)

# ---------- 县级单元（县名空则用市名；港澳保持单一单元） ----------
c0, c1, c2 = gdf.columns[0], gdf.columns[1], gdf.columns[2]
# 用可空字符串并显式 fillna("")：海南的“县”字段实际是 NaN，县级名称在“市”字段；
# 若用 astype(str) + replace("nan")，在当前 pandas 字符串类型下会残留 NA，
# 导致县键为 NaN、groupby 直接丢掉整个海南（只填省色、没有县界）。
gdf["省名"] = gdf[c0].astype("string").str.strip().fillna("")
gdf["市名"] = gdf[c1].astype("string").str.strip().fillna("")
gdf["县名"] = gdf[c2].astype("string").str.strip().fillna("")

# 特殊合并区归属（用“乡镇码”标记）：
#   红+橙合并区 RED_ORANGE → 灵山县（广东省|钦州市）
#   蓝色合并区 BLUE      → 450108（广西|4501，与已有 450108 单元同市键才能合并）
c4 = gdf.columns[4]  # 乡镇码
_code = gdf[c4].astype("string").str.strip().fillna("")
_red = _code == "RED_ORANGE"
_blue = _code == "BLUE"
gdf.loc[_red, "省名"] = "广东省"
gdf.loc[_red, "市名"] = "钦州市"
gdf.loc[_red, "县名"] = "灵山县"
gdf.loc[_blue, "省名"] = "广西壮族自治区"
gdf.loc[_blue, "市名"] = "4501"
gdf.loc[_blue, "县名"] = "450108"
if _red.any() or _blue.any():
    print(f"合并区归属：RED_ORANGE→灵山县 {int(_red.sum())} 个，"
          f"BLUE→450108 {int(_blue.sum())} 个")

gdf["县级"] = gdf["县名"].where(gdf["县名"] != "", gdf["市名"])
gdf["县键"] = (gdf["省名"] + "|" + gdf["市名"] + "|" + gdf["县级"]).fillna("")
assert gdf["县键"].notna().all() and (gdf["县键"] != "").all()

county_geoms = {}
for key, grp in gdf.groupby("县键", sort=False):
    geom = unary_union(list(grp.geometry))
    if geom is not None and not geom.is_empty:
        county_geoms[key] = geom
print(f"县级单元 {len(county_geoms)}（东莞/中山回退用市名；港澳为单一单元）")

# ---------- 省级单元与配色 ----------
prov_list = list(dict.fromkeys(gdf["省名"].tolist()))
prov_geoms_orig = {}
for p, grp in gdf.groupby("省名", sort=False):
    prov_geoms_orig[p] = unary_union(list(grp.geometry))

# 只对“不临海”的县做窄缝闭合（≤2×DISSOLVE_CLOSE_M），修掉同县乡镇窄缝
# 被画成区内部黑线的问题（佛山禅城区）。临海县（含离岛）保持原样，
# 不对海岸线和离岛界线做任何合并/平滑。
def _close_slits(geom):
    if geom is None or geom.is_empty:
        return geom
    return geom.buffer(DISSOLVE_CLOSE_M).buffer(-DISSOLVE_CLOSE_M)

# 海岸线 = 全部陆地并集的“外环”（丢弃内部孔洞/乡镇缝隙），
# 否则 union.boundary 会把所有乡镇缝隙都算进来，导致每个县都被判成临海。
_all_land = unary_union(list(prov_geoms_orig.values()))
_parts = list(_all_land.geoms) if hasattr(_all_land, "geoms") else [_all_land]
_coast = MultiLineString([list(p.exterior.coords) for p in _parts
                          if p.geom_type == "Polygon"])
print(f"  海岸线长度 {_coast.length / 1000:.0f} km，陆地部件 {len(_parts)}")
county_prov = gdf.groupby("县键")["省名"].first().to_dict()
_n_inland = 0
for k in list(county_geoms.keys()):
    g = county_geoms[k]
    if g is None or g.is_empty:
        continue
    if g.boundary.intersection(_coast).length <= 1000.0:   # 不挨海岸线 = 内陆
        county_geoms[k] = _close_slits(g)
        _n_inland += 1
print(f"  内陆县窄缝闭合 {_n_inland} 个（临海县保持原样）")

# 省级填色用处理后的县重新合并，保证填色与县界一致。
prov_geoms = {p: unary_union([county_geoms[k] for k in county_geoms
                              if county_prov.get(k) == p])
              for p in prov_list}

prov_idx = {p: i + 1 for i, p in enumerate(prov_list)}
prov_color = {}
for i, p in enumerate(prov_list):
    prov_color[p] = PROVINCE_COLORS.get(p, FALLBACK_COLORS[i % len(FALLBACK_COLORS)])
print(f"省级单元 {len(prov_list)}：")
for p in prov_list:
    print(f"  {p}  {prov_color[p]}")

# ---------- 画布尺寸（上限 4000×4000，等比） ----------
minx, miny, maxx, maxy = gdf.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
k = min(MAX_PX / geo_w, MAX_PX / geo_h)
w_px = min(MAX_PX, max(1, int(round(geo_w * k))))
h_px = min(MAX_PX, max(1, int(round(geo_h * k))))
sx, sy = w_px / geo_w, h_px / geo_h
print(f"地理范围 {geo_w:.0f}×{geo_h:.0f} m，画布 {w_px}×{h_px}（上限 {MAX_PX}×{MAX_PX}）")

# ---------- ① 县界 1px ----------
print(f"① 县界 {LINE_PX}px（直绘 → thin 中心线）...")
county_lines = []
for geom in county_geoms.values():
    county_lines.extend(exteriors(geom))
lines_mask = draw_layer(county_lines, w_px, h_px, minx, maxy, sx, sy)
print(f"  原始 1px 像素 {int(lines_mask.sum())}")
lines_mask = deblock_2x2(thin(lines_mask))
n_blk = int(ndimage.binary_erosion(lines_mask,
                                   structure=np.ones((2, 2), dtype=bool)).sum())
print(f"  1px 中心线 {int(lines_mask.sum())} px，2×2 粗块 {n_blk}（应为 0）")

# ---------- ② 每省一种填色 ----------
print("② 省级填色（每省一种颜色）...")
transform = from_bounds(minx, miny, maxx, maxy, w_px, h_px)
prov_shapes = [(prov_geoms[p], prov_idx[p]) for p in prov_list]
pidx = rasterize(prov_shapes, out_shape=(h_px, w_px), transform=transform,
                 fill=0, dtype="uint8")

# 亚像素小岛：中心规则填不到（与海连通，图上呈白色）。再用 all_touched=True
# 栅格化一次，只补“与已填陆地不相邻”的 touched 像素（离岸小岛），
# 避免给大陆海岸加一圈 1px 光晕。
pidx_touch = rasterize(prov_shapes, out_shape=(h_px, w_px), transform=transform,
                       fill=0, dtype="uint8", all_touched=True)
_land0 = pidx > 0
_island = ((pidx == 0) & (pidx_touch > 0)
           & ~ndimage.binary_dilation(
               _land0, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]],
                                          dtype=bool)))
if _island.any():
    pidx[_island] = pidx_touch[_island]
    print(f"  离岸小岛补色 {int(_island.sum())} px")
del pidx_touch, _land0, _island
print(f"  省级填色像素 {int((pidx > 0).sum())}")

# 相邻县共享边各自 simplify 后会有 ≤1px 错位，接缝会留下未覆盖小洞；
# 把被陆地包围的内部孔洞填平，并用邻近省份颜色补上，避免出现白色裂缝。
land = pidx > 0
land_filled = ndimage.binary_fill_holes(land)
gap = land_filled & ~land
if gap.any():
    _f = pidx.copy()
    for _ in range(6):
        _m = gap & (_f == 0)
        if not _m.any():
            break
        _gd = ndimage.grey_dilation(_f, size=3)
        _take = _m & (_gd > 0)
        if not _take.any():
            break
        _f[_take] = _gd[_take]
    filled = int((gap & (_f > 0)).sum())
    pidx = _f
    print(f"  接缝/内部孔洞填平 {filled} px")
    del _f, _m, _gd, _take
del land, land_filled, gap

lut = np.zeros((len(prov_list) + 1, 3), dtype=np.uint8)
lut[0] = BG_COLOR
for p in prov_list:
    lut[prov_idx[p]] = prov_color[p]
arr = lut[pidx]
del pidx
arr[lines_mask] = LINE_COLOR

# ---------- ③ 被线围住的小白点按邻近省份颜色回填 ----------
_white = np.all(arr == BG_COLOR, axis=2)
_nw, _wlab, _wstat, _ = cv2.connectedComponentsWithStats(
    _white.astype(np.uint8), connectivity=4)
_border = np.unique(np.concatenate([_wlab[0, :], _wlab[-1, :],
                                    _wlab[:, 0], _wlab[:, -1]]))
_comp = np.arange(_nw)
_small = ((_wstat[:, cv2.CC_STAT_AREA] <= WHITE_FILL_MAX_PX)
          & ~np.isin(_comp, _border))
_small[0] = False
_nfill = 0
for _i in np.where(_small)[0]:
    _x, _y, _w, _h = (int(v) for v in _wstat[_i, :4])
    _x0, _y0 = max(0, _x - 1), max(0, _y - 1)
    _x1, _y1 = min(w_px, _x + _w + 1), min(h_px, _y + _h + 1)
    _sub = _wlab[_y0:_y1, _x0:_x1] == _i
    _ring = ndimage.binary_dilation(_sub, structure=np.ones((3, 3), bool)) & ~_sub
    _cols = arr[_y0:_y1, _x0:_x1][_ring]
    _cols = _cols[(_cols != BG_COLOR).any(axis=1) & (_cols != LINE_COLOR).any(axis=1)]
    if len(_cols) == 0:
        continue
    _u, _c = np.unique(_cols, axis=0, return_counts=True)
    arr[_y0:_y1, _x0:_x1][_sub] = _u[_c.argmax()]
    _nfill += int(_sub.sum())
if _nfill:
    print(f"  封闭小白点回填 {_nfill} px")
del _white, _nw, _wlab, _wstat, _border, _comp, _small

# ---------- 颜色验证 ----------
_uniq, _ucnt = np.unique(arr.reshape(-1, 3), axis=0, return_counts=True)
_allowed = {BG_COLOR, LINE_COLOR} | {prov_color[p] for p in prov_list}
_bad = [(tuple(int(x) for x in c), int(n)) for c, n in zip(_uniq, _ucnt)
        if tuple(int(x) for x in c) not in _allowed]
print(f"  全图颜色种类：{len(_uniq)}（允许 {len(_allowed)}）；非允许色 "
      f"{sum(n for _, n in _bad)} px（应为 0）")

# ---------- 保存 ----------
canvas = Image.fromarray(arr, mode="RGB")
del arr
out_path = save_png(canvas, out_file)
print(f"已保存 {out_path}（{w_px}×{h_px}）")

_re = np.asarray(Image.open(out_path).convert("RGB"))
_u2, _c2 = np.unique(_re.reshape(-1, 3), axis=0, return_counts=True)
_bad2 = sum(n for c, n in zip(_u2, _c2)
            if tuple(int(x) for x in c) not in _allowed)
print(f"  存档自检：非允许色 {_bad2} px（应为 0），尺寸 {_re.shape[1]}×{_re.shape[0]}")
print("\n✅ 华南县级分省设色图生成完毕")
