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
  ⑤ 画布上限 4000×4000 px（等比缩放）；
  ⑥ 海域留白：数据源把部分「归属某县市的海域」（海湾、河口、近岸水面、港澳
     海事区）也划进了辖区面，直接填色会把这些水面涂成辖区颜色。这里先用一张
     陆地掩膜（_ref/gadm41_CHN_2.json）把各县面裁到陆地上，水面不再填色、
     跨水的辖区界线也一并消失；面积 < ISLAND_KEEP_KM2 的孤立面块按岛礁处理，
     整块保留（掩膜精度不足以判断细小岛礁）——但若该面块经水体栅格
     （_ref/water_mask.tif）判定几乎全是水，则不再整块保留，照常裁陆地。
  ⑦ 硬约束：彩色像素必须同时落在某个县级面上、且不在水体栅格上，
     否则刷白。保证「SHP 里没有面 → 白」「水域 → 白」恒成立。

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
from shapely.geometry import MultiLineString, MultiPolygon, box, Point
from scipy import ndimage
from skimage.morphology import thin
from pyproj import Transformer
import cv2
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling

Image.MAX_IMAGE_PIXELS = None
warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2广东全省/华南乡镇界线.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2广东全省"
if not os.path.exists(shp_path):        # 配置路径失效时回退到脚本所在目录
    out_dir = os.path.dirname(os.path.abspath(__file__))
    shp_path = os.path.join(out_dir, "华南乡镇界线.shp")
out_file = os.path.join(out_dir, "华南县界_分省设色_4000.png")

MAX_PX = 4000                 # 画布上限 4000×4000
TARGET_PX_TO_METER = 40.0     # 目标 1px=40m（被上限缩放）
SIMPLIFY_TOL_M = 40.0         # 几何简化容差（米）
LINE_PX = 1                   # 县界严格 1px
BG_COLOR = (255, 255, 255)    # 海面/背景
LINE_COLOR = (0, 0, 0)        # 界线
WHITE_FILL_MAX_PX = 15        # 被线围住的小白点回填阈值（只补接缝级的麻点，
                              # 宁可小：海域留白后，白点里也有真正的成片水面）
SEAM_MAX_HALFWIDTH = 2.0      # 接缝细缝判定：距离变换半宽上限（像素）
DISSOLVE_CLOSE_M = 200.0      # 仅内陆县：闭合同县乡镇间窄缝（≤400m）

# 海陆掩膜（海域留白）：数据源的县面把「属于该县市的海域」也划进了辖区，
# 用这张陆地掩膜裁一遍，只保留陆地部分。掩膜文件随脚本放在 _ref/ 下。
# 掩膜 = GADM 4.1 中国县级陆地（gadm41_CHN_2.json，含港澳）
#        − 高德 z=10 底图提取的水体（GADM 本身把湛江湾这类海湾算成了陆地，
#          必须再挖掉一次，否则湾内水面仍会被涂色）。
# 详见 _ref/README_掩膜.txt。
LAND_MASK_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "_ref", "land_mask.json")
USE_LAND_MASK = True
ISLAND_KEEP_KM2 = 20.0        # 小于此面积的孤立面块视为岛礁，不参与裁切
CLIP_MIN_KM2 = 0.01           # 裁切后小于此面积的碎片丢弃（掩膜噪声）

# 水体栅格（高德 z=11 瓦片提取，255=水）：GADM 陆地掩膜只有 1:100 万级精度，
# 且对 <ISLAND_KEEP_KM2 的面块一律放行，于是「面积小而几乎全是水」的面块
# （湛江湾、钦州湾、珠江口里的水面片区）会被整块涂成辖区色。
# 这张栅格用来判「这块地到底是不是水」：判为水的面块不再整块放行，
# 而且要按陆地裁一遍。生成脚本：_build_water.py
WATER_MASK_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "_ref", "water_mask.tif")
USE_WATER_MASK = True
WATER_ISLAND_MAX_FRAC = 0.5   # 小面块水体占比 ≥ 此值 → 判为水域，不再整块保留

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


def build_land_mask(crs, bounds):
    """读入陆地掩膜（GADM 县级面 = 只有陆地），投影、并集、裁到图幅范围。"""
    if not USE_LAND_MASK:
        return None
    if not os.path.exists(LAND_MASK_REF):
        print(f"  ⚠️ 未找到陆地掩膜 {LAND_MASK_REF}，海域不会被裁掉")
        return None
    ref = gpd.read_file(LAND_MASK_REF)
    if ref.crs is None:
        ref = ref.set_crs("EPSG:4326")
    ref = ref.to_crs(crs)
    land = unary_union([g.buffer(0) for g in ref.geometry
                        if g is not None and not g.is_empty])
    minx, miny, maxx, maxy = bounds
    pad = 5000.0
    land = land.intersection(box(minx - pad, miny - pad, maxx + pad, maxy + pad))
    n_part = len(land.geoms) if hasattr(land, "geoms") else 1
    print(f"  陆地掩膜：{os.path.basename(LAND_MASK_REF)}，图幅内陆地部件 {n_part}")
    return land


def build_water_pixels(crs, transform, w_px, h_px):
    """把水体栅格重投影到画布网格，返回 bool 数组（True=水面）。"""
    if not USE_WATER_MASK:
        return None
    if not os.path.exists(WATER_MASK_REF):
        print(f"  ⚠️ 未找到水体栅格 {WATER_MASK_REF}，水域可能被涂色")
        return None
    with rasterio.open(WATER_MASK_REF) as src:
        dst = np.zeros((h_px, w_px), dtype="uint8")
        reproject(source=rasterio.band(src, 1), destination=dst,
                  src_transform=src.transform, src_crs=src.crs,
                  dst_transform=transform, dst_crs=crs,
                  resampling=Resampling.nearest)
    m = dst > 127
    print(f"  水体栅格：{os.path.basename(WATER_MASK_REF)}，画布内水面 {int(m.sum())} px")
    return m


class WaterProbe:
    """判断「面块是不是水面」——按水体栅格采样，不看面积。

    GADM 陆地掩膜对几百米级的小岛根本描不出来，所以小面块只能靠水体栅格
    区分：真岛礁在底图上不是水色，海湾/河口片区是。
    """

    def __init__(self, crs):
        self.ok = USE_WATER_MASK and os.path.exists(WATER_MASK_REF)
        if not self.ok:
            return
        self.src = rasterio.open(WATER_MASK_REF)
        self.tr = Transformer.from_crs(crs, "EPSG:3857", always_xy=True)

    def frac(self, poly):
        """返回该面块内部采样点中落在水面的比例（0~1）。"""
        if not self.ok:
            return 0.0
        pts = []
        c = poly.centroid
        if poly.covers(c):
            pts.append((c.x, c.y))
        rp = poly.representative_point()
        pts.append((rp.x, rp.y))
        minx_, miny_, maxx_, maxy_ = poly.bounds
        for fx, fy in ((0.3, 0.3), (0.7, 0.7)):
            px, py = minx_ + fx * (maxx_ - minx_), miny_ + fy * (maxy_ - miny_)
            if poly.covers(Point(px, py)):
                pts.append((px, py))
        if not pts:
            return 0.0
        xy = [self.tr.transform(x, y) for x, y in pts]
        vals = [v[0] for v in self.src.sample(xy)]
        return sum(1 for v in vals if v > 127) / len(vals)

    def close(self):
        if self.ok:
            self.src.close()


def clip_geom_to_land(geom, land, probe=None):
    """把单个辖区面裁到陆地上：水域（及跨水的辖区界线）整块去掉。

    面积 < ISLAND_KEEP_KM2 的孤立面块默认整块保留——掩膜（1:100 万级）
    分辨不出几百米级的小岛，硬裁会把图上成片的小岛一起抹掉。
    但如果这个小面块本身几乎全是水（海湾/河口里被划给某县的水面片区），
    就照常按陆地裁，否则整片海会被涂成辖区色。
    """
    if land is None or geom is None or geom.is_empty:
        return geom
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    out = []
    for p in parts:
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        if p.area / 1e6 < ISLAND_KEEP_KM2:      # 小面块：先看是不是水
            if probe is None or probe.frac(p) < WATER_ISLAND_MAX_FRAC:
                out.append(p)                   # 岛礁：原样保留
                continue
        q = p.intersection(land)                # 陆地部分
        if q.is_empty:
            continue
        for r in (q.geoms if hasattr(q, "geoms") else [q]):
            if r.geom_type == "Polygon" and r.area / 1e6 >= CLIP_MIN_KM2:
                out.append(r)
    if not out:
        return None
    return out[0] if len(out) == 1 else MultiPolygon(out)


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

# ---------- 画布尺寸（上限 4000×4000，等比） ----------
# 先算画布：裁切要用画布网格上的水体栅格来判「小面块是不是水」。
geo_bounds = tuple(gdf.total_bounds)     # 取裁切前的范围，裁掉海域后画幅不变
minx, miny, maxx, maxy = geo_bounds
geo_w, geo_h = maxx - minx, maxy - miny
k = min(MAX_PX / geo_w, MAX_PX / geo_h)
w_px = min(MAX_PX, max(1, int(round(geo_w * k))))
h_px = min(MAX_PX, max(1, int(round(geo_h * k))))
sx, sy = w_px / geo_w, h_px / geo_h
print(f"地理范围 {geo_w:.0f}×{geo_h:.0f} m，画布 {w_px}×{h_px}（上限 {MAX_PX}×{MAX_PX}）")
transform = from_bounds(minx, miny, maxx, maxy, w_px, h_px)
water_px = build_water_pixels(gdf.crs, transform, w_px, h_px)

# ---------- 海域留白：把辖区面裁到陆地 ----------
# 画布范围取裁切前的范围，保证裁掉海域后画幅不跟着变。
print("海域留白（按陆地掩膜裁掉辖区内的水面）...")
land_mask = build_land_mask(gdf.crs, geo_bounds)
probe = WaterProbe(gdf.crs)
if land_mask is not None:
    _a0 = gdf.geometry.area.sum() / 1e6
    gdf["geometry"] = [clip_geom_to_land(g, land_mask, probe) for g in gdf.geometry]
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy().reset_index(drop=True)
    _a1 = gdf.geometry.area.sum() / 1e6
    print(f"  去掉水面 {_a0 - _a1:,.0f} km²（{_a0:,.0f} → {_a1:,.0f} km²）")

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
# 把被陆地包围的「细缝」填平，用邻近省份颜色补上，避免出现白色裂缝。
# 注意：只补细缝（半宽 ≤ SEAM_MAX_HALFWIDTH 像素），不能笼统 fill_holes——
# 海湾、潟湖这些成片水面现在也是"内部孔洞"，一并填平就把海域又涂回颜色了。
# 另一条同样重要：**水体栅格上的像素一律不补**。河道、窄湾被陆地掩膜挖掉后
# 会变成又长又窄的缝，距离变换半宽同样 ≤2px，只按半宽判断会把整条河涂成
# 邻县颜色——那些地方 SHP 里本来就没有面，必须留白。
land = pidx > 0
land_filled = ndimage.binary_fill_holes(land)
gap = land_filled & ~land
if gap.any():
    _ng, _glab = cv2.connectedComponents(gap.astype(np.uint8))
    _gdt = ndimage.distance_transform_edt(gap)
    _gmax = ndimage.maximum(_gdt, _glab, index=np.arange(_ng))
    _gkeep = _gmax <= SEAM_MAX_HALFWIDTH
    _gkeep[0] = False
    gap = _gkeep[_glab]
    del _ng, _glab, _gdt, _gmax, _gkeep
    if water_px is not None:
        _n_wg = int((gap & water_px).sum())
        gap &= ~water_px
        if _n_wg:
            print(f"  接缝细缝里剔除水面 {_n_wg} px（河道/窄湾不补色）")
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
    print(f"  接缝细缝填平 {filled} px（只补 ≤{SEAM_MAX_HALFWIDTH}px 半宽的缝，海湾不补）")
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
# 同理：水体栅格上的白点不回填（那是水，本来就该白）。
_white = np.all(arr == BG_COLOR, axis=2)
_nw, _wlab, _wstat, _ = cv2.connectedComponentsWithStats(
    _white.astype(np.uint8), connectivity=4)
_border = np.unique(np.concatenate([_wlab[0, :], _wlab[-1, :],
                                    _wlab[:, 0], _wlab[:, -1]]))
_comp = np.arange(_nw)
_small = ((_wstat[:, cv2.CC_STAT_AREA] <= WHITE_FILL_MAX_PX)
          & ~np.isin(_comp, _border))
_small[0] = False
if water_px is not None and _nw > 1:
    _wcnt = ndimage.sum(water_px, _wlab, index=np.arange(_nw))
    _small &= (_wcnt * 2 <= _wstat[:, cv2.CC_STAT_AREA])
    del _wcnt
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

# ---------- ③.5 硬约束：没有面的地方、水面，一律留白 ----------
# 上面几步都是"补色"，补错了会把没有面/水面的地方涂成辖区色。
# 这里按最终几何再核对一遍：彩色像素必须同时满足
#   ① 落在某个县级面上（all_touched，兼顾亚像素小岛）
#   ② 不在水体栅格上
# 不满足的一律刷白。这条是兜底，保证「SHP 里没有面 → 白」「水 → 白」恒成立。
_paint_any = rasterize([(g, 1) for g in county_geoms.values()],
                       out_shape=(h_px, w_px), transform=transform,
                       fill=0, dtype="uint8", all_touched=True) > 0
_colored = ~np.all(arr == BG_COLOR, axis=2) & ~np.all(arr == LINE_COLOR, axis=2)
_n_noface = int((_colored & ~_paint_any).sum())
if _n_noface:
    arr[_colored & ~_paint_any] = BG_COLOR
    print(f"  无面区域刷白 {_n_noface} px（SHP 里没有面的地方必须是白的）")
if water_px is not None:
    _n_wcol = int((_colored & water_px).sum())
    if _n_wcol:
        arr[_colored & water_px] = BG_COLOR
        print(f"  水面刷白 {_n_wcol} px（水域一律留白）")
    # 跨水的辖区界线：只擦「左右（或上下）两侧都是水」的黑像素——海岸线一侧
    # 是陆地，不会被误擦。
    _blk = np.all(arr == LINE_COLOR, axis=2)
    _both = np.zeros_like(_blk)
    _both[:, 1:-1] = water_px[:, :-2] & water_px[:, 2:]
    _both[1:-1, :] |= water_px[:-2, :] & water_px[2:, :]
    _n_wline = int((_blk & _both).sum())
    if _n_wline:
        arr[_blk & _both] = BG_COLOR
        print(f"  跨水界线清除 {_n_wline} px（两侧皆水的黑像素）")
    del _blk, _both
# 事后自检：这两项必须为 0
_chk = ~np.all(arr == BG_COLOR, axis=2) & ~np.all(arr == LINE_COLOR, axis=2)
_c_noface = int((_chk & ~_paint_any).sum())
_c_water = int((_chk & water_px).sum()) if water_px is not None else 0
print(f"  自检：彩色 {int(_chk.sum())} px，其中无面 {_c_noface}、水面 {_c_water}（均应为 0）")
del _chk, _colored, _paint_any

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
