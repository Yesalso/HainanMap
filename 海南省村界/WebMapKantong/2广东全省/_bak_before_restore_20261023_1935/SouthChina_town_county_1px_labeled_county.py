# -*- coding: utf-8 -*-
"""
华南乡镇界线 · 分层界线高清图（不标注地名）

数据源：2广东全省/华南乡镇界线.shp
  （含更新后的广西乡镇界线、红+橙合并区 RED_ORANGE、蓝色合并区 BLUE）

绘制要点（参照 Draw_North4_President_2016.py 的线稿/填色规范）：
  ① 像素网格统一：像素 i 中心 = minx+(i+0.5)/sx，线稿一律用 floor() 落格，
     与 rasterio 填色的中心规则一致 → 线与色不错开半像素；
  ② 线稿全部严格 1px：PIL Bresenham 逐段直绘 → skimage.morphology.thin
     取中心线 → deblock_2x2 清除 2×2 粗块。
     2px 地级市界 / 3px 省级界默认关闭（DRAW_HIERARCHY=False）——粗界线与
     乡镇界同轴，叠加会使该段乡镇界变粗，出现"有的粗有的细"；
  ③ 图资清理：退化迴針（remove_ring_slits）+ 微孔洞清理（clean_tiny_holes），
     消除"凭空冒出的死线"与 dissolve 浮点噪声；
  ④ 填色：按地级市分组，各一种柔和色（按 Lab 感知色差最大化相邻对比）；
      线网先 3×3 膨胀焊死共享边错位造成的 1px 断缝，再作堤坝做 4-连通油漆桶
      填色；接缝小白洞按邻近城市回填、分界处补黑，确保色块整块单色且任意
      两色之间必有黑线（不封闭会两城串色，不补缝会出白色麻点/彩色毛边）；
  ⑤ 不画标题、不画图例：输出即纯地图；
  ⑥ 海域留白：数据源把部分「归属某市的海域」（海湾、河口、近岸水面、港澳
     海事区）也划进了辖区面，直接填色会把这些水面涂成辖区颜色。这里先用一张
     陆地掩膜（_ref/gadm41_CHN_2.json）把各市面裁到陆地上，水面不再填色、
     跨水的辖区界线也一并消失；面积 < ISLAND_KEEP_KM2 的孤立面块按岛礁处理，
     整块保留（掩膜精度不足以判断细小岛礁）。

画布上限 10000×10000 px（等比缩放，目标 1px=40m）。
输出：2广东全省/华南乡镇界线_无标注_分层界线.png
      2广东全省/华南乡镇界线_无标注_缩略预览.png
"""
import os
import sys
import math
import colorsys
import warnings
from io import BytesIO

import geopandas as gpd
import numpy as np
from PIL import Image, ImageDraw
from shapely.ops import unary_union
from shapely.geometry import Polygon, MultiPolygon, box, Point
from shapely.strtree import STRtree
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage.morphology import thin
from skimage.color import rgb2lab
from pyproj import Transformer
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cv2

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
out_file = os.path.join(out_dir, "华南乡镇界线_无标注_分层界线.png")
preview_file = os.path.join(out_dir, "华南乡镇界线_无标注_缩略预览.png")

TARGET_PX_TO_METER = 40.0   # 目标 1px = 40m（超上限时等比缩小）
MAX_PX = 10000               # 画布上限 10000×10000 px
SIMPLIFY_TOL_M = 40.0        # 几何简化容差（米）
LINE_TOWN_PX = 1             # ① 乡镇界
LINE_CITY_PX = 2             # ② 地级市界
LINE_PROV_PX = 3             # ② 省级界/海岸线
DRAW_HIERARCHY = False       # 是否叠加 2px/3px 粗界线。
                             # 粗界线与乡镇界同轴，叠加后该段乡镇界会变成 2/3px，
                             # 出现"有的粗有的细"；默认关闭 → 全部界线严格 1px
THRESHOLD_VAL = 40           # ② 二值化阈值
DPI = 100
HOLE_MIN_AREA_M2 = 2000.0    # 小于此面积的内环视为噪声，填平
DESPIKE_TOL = 5.0            # 迴針判定：两点距离上限（米）
DESPIKE_AREA = 5000.0        # 迴針判定：围出面积上限（m²）
DESPIKE_PATH = 100.0         # 迴針判定：子路径长度下限（米）
BG_COLOR = (255, 255, 255)
LINE_COLOR = (0, 0, 0)
PREVIEW_PX = 2400            # 缩略预览最长边

# 海陆掩膜（海域留白）：数据源的市面把「属于该市的海域」也划进了辖区，
# 用这张陆地掩膜裁一遍，只保留陆地部分。掩膜文件随脚本放在 _ref/ 下。
# 掩膜 = GADM 4.1 中国县级陆地（gadm41_CHN_2.json，含港澳）
#        − 高德 z=10 底图提取的水体（GADM 本身把湛江湾这类海湾算成了陆地，
#          必须再挖掉一次，否则湾内水面仍会被涂色）。
# 详见 _ref/README_掩膜.txt。
LAND_MASK_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "_ref", "land_mask.json")
USE_LAND_MASK = True
ISLAND_KEEP_KM2 = 20.0       # 小于此面积的孤立面块视为岛礁，不参与裁切
CLIP_MIN_KM2 = 0.01          # 裁切后小于此面积的碎片丢弃（掩膜噪声）

# 水体栅格（高德 z=11 瓦片提取，255=水）：GADM 陆地掩膜只有 1:100 万级精度，
# 且对 <ISLAND_KEEP_KM2 的面块一律放行，于是「面积小而几乎全是水」的面块
# （湛江湾、钦州湾、珠江口里的水面片区）会被整块涂成辖区色。
# 这张栅格用来判「这块地到底是不是水」。生成脚本：_build_water.py
WATER_MASK_REF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "_ref", "water_mask.tif")
USE_WATER_MASK = True
WATER_ISLAND_MAX_FRAC = 0.5  # 小面块水体占比 ≥ 此值 → 判为水域，不再整块保留
SEAM_MAX_HALFWIDTH = 2.0     # 接缝细缝判定：距离变换半宽上限（像素）
WHITE_FILL_MAX_PX = 20       # 被线围住的麻点回填阈值（海域留白后宜小）

# 广西要素的"市"字段存的是市级代码，这里补上名称（仅用于分组名与调试输出）
GX_CITY_NAMES = {
    "4501": "南宁市", "4502": "柳州市", "4503": "桂林市", "4504": "梧州市",
    "4505": "北海市", "4506": "防城港市", "4507": "钦州市", "4508": "贵港市",
    "4509": "玉林市", "4510": "百色市", "4511": "贺州市", "4512": "河池市",
    "4513": "来宾市", "4514": "崇左市",
}
# ==================================================

os.makedirs(out_dir, exist_ok=True)


# ===================== ① 图资清理 =====================
def _despike_ring(coords, tol=DESPIKE_TOL, area_thr=DESPIKE_AREA, min_path=DESPIKE_PATH):
    """折疊單一線環中的退化迴針（去而復返的零寬裂縫）。

    村/鄉鎮界圖資在少數邊界有數位化瑕疵：線環從某點出發、沿同一路徑外走
    數百公尺後又原路折返，圍出的面積近乎 0；dissolve 後會殘留成地圖中間
    憑空冒出的「死線」。三道門檻（位移/長度/面積）確保真實半島、岬角、
    細長沙洲不會被誤刪。KD-tree 加速最近點搜尋（取前 24 候選）。
    """
    P = np.asarray(coords, dtype=float)
    n = len(P)
    if n < 5:
        return P
    try:
        tree = cKDTree(P)
        k = min(24, n)
        dist, idx = tree.query(P, k=k)
    except Exception:
        return P
    if dist.ndim == 1:
        dist = dist[:, None]
        idx = idx[:, None]
    dead = np.zeros(n, dtype=bool)
    for i in range(n):
        if dead[i]:
            continue
        for c in range(dist.shape[1]):
            j = int(idx[i, c])
            if j <= i + 1:
                continue
            if i == 0 and j == n - 1:
                continue
            d = float(dist[i, c])
            if d > tol:
                break
            seg = P[i:j + 1]
            path_len = float(np.hypot(np.diff(seg[:, 0]), np.diff(seg[:, 1])).sum())
            _x, _y = seg[:, 0], seg[:, 1]
            area = 0.5 * abs(float(np.dot(_x[:-1], _y[1:]) - np.dot(_x[1:], _y[:-1])))
            if path_len > min_path and area < area_thr:
                dead[i + 1:j] = True
            break
    return P[~dead]


def _fix_polygon(poly, min_hole_area=HOLE_MIN_AREA_M2):
    """單一多邊形：迴針折疊 + 微孔洞填平，回傳修好的 Polygon（無效則 buffer(0)）"""
    ext = _despike_ring(poly.exterior.coords)
    if len(ext) < 4:
        return None
    holes = []
    for r in poly.interiors:
        h = _despike_ring(r.coords)
        if len(h) >= 4:
            try:
                if Polygon(h).area >= min_hole_area:
                    holes.append(h)
            except Exception:
                pass
    try:
        p = Polygon(ext, holes)
        if not p.is_valid:
            p = p.buffer(0)
        return p if (p.geom_type == "Polygon" and not p.is_empty) else None
    except Exception:
        return None


def clean_geometry(geoms, min_hole_area=HOLE_MIN_AREA_M2):
    """逐要素清理：迴針折疊 + 微孔洞填平；回傳新的幾何串列"""
    out = []
    n_fixed = 0
    for g in geoms:
        if g is None or g.is_empty:
            out.append(None)
            continue
        parts = list(g.geoms) if hasattr(g, "geoms") else [g]
        fixed = []
        for poly in parts:
            if poly.geom_type != "Polygon":
                continue
            p = _fix_polygon(poly, min_hole_area)
            if p is None:
                continue
            if len(p.exterior.coords) != len(poly.exterior.coords):
                n_fixed += 1
            fixed.append(p)
        if not fixed:
            out.append(None)
        elif len(fixed) == 1:
            out.append(fixed[0])
        else:
            out.append(MultiPolygon(fixed))
    print(f"  迴針/微孔洞清理：修正 {n_fixed} 個要素外環")
    return out


# ===================== ② 海陸掩膜（海域留白） =====================
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
    print(f"  陸地掩膜：{os.path.basename(LAND_MASK_REF)}，圖幅內陸地部件 {n_part}")
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
    print(f"  水體柵格：{os.path.basename(WATER_MASK_REF)}，畫布內水面 "
          f"{int(m.sum())} px")
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
    """把單個轄區面裁到陸地上：水域（及跨水的轄區界線）整塊去掉。

    面積 < ISLAND_KEEP_KM2 的孤立面塊默認整塊保留——掩膜（1:100 萬級）
    分辨不出幾百米級的小島，硬裁會把圖上成片的小島一起抹掉。
    但若該面塊經水體柵格判定幾乎全是水（海灣/河口裡劃給某市的水面片區），
    就照常按陸地裁，否則整片海會被塗成轄區色。
    """
    if land is None or geom is None or geom.is_empty:
        return geom
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    out = []
    for p in parts:
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        if p.area / 1e6 < ISLAND_KEEP_KM2:      # 小面塊：先看是不是水
            if probe is None or probe.frac(p) < WATER_ISLAND_MAX_FRAC:
                out.append(p)                   # 島礁：原樣保留
                continue
        q = p.intersection(land)                # 陸地部分
        if q.is_empty:
            continue
        for r in (q.geoms if hasattr(q, "geoms") else [q]):
            if r.geom_type == "Polygon" and r.area / 1e6 >= CLIP_MIN_KM2:
                out.append(r)
    if not out:
        return None
    return out[0] if len(out) == 1 else MultiPolygon(out)


# ===================== ③ 線稿工具 =====================
def to_px(coords, minx, maxy, sx, sy):
    """像素網格約定：像素 i 中心 = minx+(i+0.5)/sx（floor，與 rasterio 一致）"""
    return [(int(math.floor((x - minx) * sx)), int(math.floor((maxy - y) * sy)))
            for x, y in coords]


def extract_lines(gs):
    if isinstance(gs, (list, tuple)):
        out = []
        for g in gs:
            out.extend(extract_lines(g))
        return out
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


def draw_layer(lines, width, w_px, h_px, minx, maxy, sx, sy):
    """Bresenham 逐段描繪 width 像素寬的純黑白線，返回 bool 陣列"""
    im = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(im)
    for coords in lines:
        pts = to_px(coords, minx, maxy, sx, sy)
        if len(pts) >= 2:
            d.line(pts, fill=0, width=width)
    return np.asarray(im) < 128


def deblock_2x2(mask, passes=4):
    """清除 2×2 全黑粗块，保证线网严格 1px。

    thin() 在十字/丁字路口与对角阶梯处仍可能留下少量 2×2 块；
    每轮清除这些块的右下像素（8-连通下不破坏连通性），直至无 2×2 块。
    """
    m = mask.copy()
    for _ in range(passes):
        blk = m[:-1, :-1] & m[:-1, 1:] & m[1:, :-1] & m[1:, 1:]
        if not blk.any():
            break
        m[1:, 1:][blk] = False
    return m


def render_line_layer(layers, w_px, h_px, minx, maxx, miny, maxy,
                      dpi=DPI, threshold=THRESHOLD_VAL):
    """matplotlib 繪粗線層 → 二值化 → 純黑線層。

    layers: [(geometries, 線寬px), ...]，依序繪製（後者疊在前者之上）。
    回傳 bool 陣列。失敗時退回 PIL 直接畫粗線。
    """
    try:
        fig = plt.figure(figsize=(w_px / dpi, h_px / dpi), dpi=dpi)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_facecolor("white")
        px2pt = 72.0 / dpi
        for geoms, w in layers:
            lw = w * px2pt
            for g in geoms:
                gpd.GeoSeries([g]).plot(ax=ax, edgecolor="black", facecolor="none",
                                       linewidth=lw)
        ax.axis("off")
        buf = BytesIO()
        plt.savefig(buf, format="png", dpi=dpi, pad_inches=0, bbox_inches=None,
                    facecolor="white")
        plt.close(fig)
        buf.seek(0)
        img = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
        buf.close()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        del img
        _, tmask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
        del gray
        return tmask > 0
    except Exception as e:
        print(f"  ⚠️ matplotlib 粗線層失敗（{e}），改用 PIL 直繪")
        mask = np.zeros((h_px, w_px), dtype=bool)
        for geoms, w in layers:
            lines = extract_lines([g.boundary if hasattr(g, "boundary") else g
                                   for g in geoms])
            mask |= draw_layer(lines, w, w_px, h_px, minx, maxy,
                               w_px / (maxx - minx), h_px / (maxy - miny))
        return mask


# ===================== 主流程 =====================
print("=" * 60)
print("  华南乡镇界线 · 分层界线图（无地名标注）")
print("=" * 60)

print("读取:", shp_path)
gdf = gpd.read_file(shp_path, encoding="UTF-8")
print(f"原始要素数：{len(gdf)}")
gdf = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
gdf["geometry"] = gdf.geometry.buffer(0)
gdf = gdf[~gdf.geometry.is_empty].copy()
print(f"面状要素：{len(gdf)}，有效 {int(gdf.geometry.is_valid.sum())}/{len(gdf)}")

if SIMPLIFY_TOL_M > 0:
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_TOL_M, preserve_topology=True)
print(f"几何简化 {SIMPLIFY_TOL_M}m")

print("图资清理（退化迴針 + 微孔洞）...")
gdf["geometry"] = clean_geometry(list(gdf.geometry))
gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty].copy().reset_index(drop=True)
n_vert = sum(len(r.exterior.coords) for p in gdf.geometry
             for r in (p.geoms if hasattr(p, "geoms") else [p]))
print(f"清理后要素 {len(gdf)}，顶点总数 {n_vert}")

# ---------- 画布尺寸 ----------
# 先算画布：裁切要用画布网格上的水体栅格来判「小面块是不是水」。
geo_bounds = tuple(gdf.total_bounds)     # 取裁切前的范围，裁掉海域后画幅不变
minx, miny, maxx, maxy = geo_bounds
geo_w, geo_h = maxx - minx, maxy - miny
scale = min(MAX_PX / (geo_w / TARGET_PX_TO_METER), MAX_PX / (geo_h / TARGET_PX_TO_METER))
PIXEL_TO_METER = TARGET_PX_TO_METER / scale       # 米 / 像素
w_px = min(int(math.floor(geo_w / PIXEL_TO_METER)), MAX_PX)
h_px = min(int(math.floor(geo_h / PIXEL_TO_METER)), MAX_PX)
sx, sy = w_px / geo_w, h_px / geo_h
print(f"比例尺 {PIXEL_TO_METER:.1f} m/px，范围 {geo_w:.0f}×{geo_h:.0f} m，"
      f"画布 {w_px}×{h_px}（上限 {MAX_PX}×{MAX_PX}）")
transform = from_bounds(minx, miny, maxx, maxy, w_px, h_px)
water_px = build_water_pixels(gdf.crs, transform, w_px, h_px)

# ---------- 海域留白：把辖区面裁到陆地 ----------
print("海域留白（按陆地掩膜裁掉辖区内的水面）...")
land_mask = build_land_mask(gdf.crs, geo_bounds)
probe = WaterProbe(gdf.crs)
if land_mask is not None:
    _a0 = gdf.geometry.area.sum() / 1e6
    gdf["geometry"] = [clip_geom_to_land(g, land_mask, probe) for g in gdf.geometry]
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy().reset_index(drop=True)
    _a1 = gdf.geometry.area.sum() / 1e6
    print(f"  去掉水面 {_a0 - _a1:,.0f} km²（{_a0:,.0f} → {_a1:,.0f} km²）")

# ---------- 分组键（地级市 / 省级） ----------
gdf["省名"] = gdf["省"].astype(str).str.strip()
gdf["市名"] = gdf["市"].astype(str).str.strip().replace({"nan": "", "None": ""})
_m = gdf["市名"].isin(GX_CITY_NAMES)
gdf.loc[_m, "市名"] = gdf.loc[_m, "市名"].map(GX_CITY_NAMES)
gdf["市键"] = gdf["省名"] + "|" + gdf["市名"]
gdf.loc[gdf["市名"] == "", "市键"] = gdf.loc[gdf["市名"] == "", "省名"] + "|__省直辖__"

city_list = list(dict.fromkeys(gdf["市键"].tolist()))
prov_list = list(dict.fromkeys(gdf["省名"].tolist()))
print(f"地级市单元 {len(city_list)}，省级单元 {len(prov_list)}")

# ---------- 配色（每个地级市一种柔和色；按感知色差最大化相邻城市对比） ----------
def _soft_color_candidates():
    """柔和色候选集：HSV 网格（多档色相/饱和度/明度），转 RGB。"""
    cand = []
    for hi in range(72):
        h = hi / 72.0
        for s in (0.22, 0.32, 0.42, 0.52):
            for v in (0.82, 0.88, 0.94):
                r, g, b = colorsys.hsv_to_rgb(h, s, v)
                cand.append((r * 255.0, g * 255.0, b * 255.0))
    return np.asarray(cand, dtype=float)


def _lab_of(rgb_arr):
    arr = np.asarray(rgb_arr, dtype=float).reshape(-1, 1, 3) / 255.0
    return rgb2lab(arr).reshape(-1, 3)


def make_palette(n):
    """在 CIE Lab 空间对柔和色候选做最远点采样，得到 n 种感知差异最大的颜色。

    旧版黄金角色相序列在 n=58 时会必然出现两个色相几乎相同的浅色（北海与
    湛江就是 0.618 与 0.666，肉眼几乎同色）。最远点采样会同时拉开色相、
    明度与饱和度，使任意两色的最小感知色差显著变大。
    """
    cand_rgb = _soft_color_candidates()
    cand_lab = _lab_of(cand_rgb)
    center = cand_lab.mean(axis=0)
    chosen = [int(np.argmax(((cand_lab - center) ** 2).sum(axis=1)))]
    min_d = np.linalg.norm(cand_lab - cand_lab[chosen[0]], axis=1)
    while len(chosen) < n:
        nxt = int(np.argmax(min_d))
        chosen.append(nxt)
        min_d = np.minimum(min_d, np.linalg.norm(cand_lab - cand_lab[nxt], axis=1))
    return [tuple(int(round(x)) for x in cand_rgb[c]) for c in chosen[:n]]


unit_geoms = {}
for key, grp in gdf.groupby("市键", sort=False):
    unit_geoms[key] = unary_union(list(grp.geometry))

keys = list(unit_geoms.keys())

PALETTE = make_palette(len(keys))
PALETTE_LAB = _lab_of(PALETTE)
PALETTE_LAB_DIST = np.linalg.norm(
    PALETTE_LAB[:, None, :] - PALETTE_LAB[None, :, :], axis=2)

ukeys = [unit_geoms[k] for k in keys]
tree = STRtree(ukeys)

# 邻接判定：共享边界、互相重叠，或间隙小于容差。
# 旧版只看 boundary∩boundary 的长度，遇到"两省图资重叠/错位"时会漏判——
# 北海与湛江就是重叠（交线长 0、距离 0），于是从未被要求取不同色。
ADJ_TOL_M = 100.0
adj = {k: set() for k in keys}
for i, k in enumerate(keys):
    gi = ukeys[i]
    for j in tree.query(gi.buffer(ADJ_TOL_M)):
        j = int(j)
        if j == i:
            continue
        if gi.distance(ukeys[j]) <= ADJ_TOL_M:
            adj[k].add(keys[j])
            adj[keys[j]].add(k)

kidx = {k: i for i, k in enumerate(keys)}
edges = [(kidx[k], kidx[n]) for k in keys for n in adj[k] if kidx[k] < kidx[n]]


def _score(color_idx):
    """优化目标：先最大化最小相邻色差，再最大化相邻色差总和。"""
    if not edges:
        return (0.0, 0.0)
    ds = [PALETTE_LAB_DIST[color_idx[i], color_idx[j]] for i, j in edges]
    return (min(ds), float(sum(ds)))


def _assign_colors():
    """贪心初值：按度数从高到低，避开已分配邻居，同时尽量远离已用颜色。"""
    order = sorted(range(len(keys)), key=lambda i: -len(adj[keys[i]]))
    color_idx = [-1] * len(keys)
    for i in order:
        nb = [color_idx[kidx[n]] for n in adj[keys[i]] if color_idx[kidx[n]] >= 0]
        cand = [c for c in range(len(PALETTE)) if c not in color_idx]
        if not cand:
            cand = list(range(len(PALETTE)))
        if nb:
            best = max(cand, key=lambda c: (min(PALETTE_LAB_DIST[c, x] for x in nb),
                                            float(PALETTE_LAB_DIST[c, nb].sum())))
        else:
            used = [c for c in color_idx if c >= 0]
            best = (max(cand, key=lambda c: min(PALETTE_LAB_DIST[c, u] for u in used))
                    if used else cand[0])
        color_idx[i] = best
    return color_idx


def _optimize_colors(color_idx, rounds=80, iters=400, seed=0):
    """局部搜索：随机交换两城颜色，按 (最小相邻色差, 相邻色差总和) 改进。"""
    rng = np.random.default_rng(seed)
    cur = list(color_idx)
    cur_score = _score(cur)
    n = len(cur)
    for _ in range(rounds):
        improved = False
        for _ in range(iters):
            a, b = (int(x) for x in rng.integers(0, n, size=2))
            if a == b:
                continue
            cur[a], cur[b] = cur[b], cur[a]
            s = _score(cur)
            if s > cur_score:
                cur_score = s
                improved = True
            else:
                cur[a], cur[b] = cur[b], cur[a]
        if not improved:
            break
    return cur, cur_score


color_idx, _best_score = _optimize_colors(_assign_colors())
city_color = {keys[i]: PALETTE[color_idx[i]] for i in range(len(keys))}
conf = sum(1 for i, j in edges if color_idx[i] == color_idx[j])
print(f"相邻地级市对 {len(edges)}，同色冲突 {conf}（应为 0），"
      f"相邻最小感知色差 ΔE={_best_score[0]:.1f}（越大越易分辨）")

# ---------- 底色填充（rasterio 中心规则一次色化） ----------
fill_shapes = []
for i, key in enumerate(keys):
    geom = unit_geoms[key]
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    for p in parts:
        if p.is_empty:
            continue
        fill_shapes.append((p, i + 1))
print(f"填充面 {len(fill_shapes)} 个…")
fill_idx = rasterize(fill_shapes, out_shape=(h_px, w_px), transform=transform,
                     fill=0, dtype="uint8")
if water_px is not None:
    _n_wf = int(((fill_idx > 0) & water_px).sum())
    fill_idx[water_px] = 0
    if _n_wf:
        print(f"  水域踢出填色 {_n_wf} px（水面一律留白）")

# 相邻地级市多边形各自 simplify 后，共享边会有 ≤1px 错位，rasterize 会留下
# 未覆盖的接缝空洞（fill_idx==0），在密集边界处表现为白色麻点。这里只补「细缝」
# （距离变换半宽 ≤ SEAM_MAX_HALFWIDTH），海湾、潟湖等成片水面保持留白。
# 另一条同样重要：**水体栅格上的像素一律不补**。河道、窄湾被陆地掩膜挖掉后
# 会变成又长又窄的缝，只按半宽判断会把整条河涂成邻市颜色——那些地方 SHP 里
# 本来就没有面，必须留白。
_land = fill_idx > 0
_gap = ndimage.binary_fill_holes(_land) & ~_land
if _gap.any():
    _ng, _glab = cv2.connectedComponents(_gap.astype(np.uint8))
    _gdt = ndimage.distance_transform_edt(_gap)
    _gmax = ndimage.maximum(_gdt, _glab, index=np.arange(_ng))
    _keep = _gmax <= SEAM_MAX_HALFWIDTH
    _keep[0] = False
    _fill_gap = _keep[_glab]
    del _ng, _glab, _gdt, _gmax, _keep
    if water_px is not None:
        _n_wg = int((_fill_gap & water_px).sum())
        _fill_gap &= ~water_px
        if _n_wg:
            print(f"  接缝细缝里剔除水面 {_n_wg} px（河道/窄湾不补色）")
    _f = fill_idx.copy()
    _gd = _m = _take = None
    for _ in range(12):
        _m = _fill_gap & (_f == 0)
        if not _m.any():
            break
        _gd = ndimage.grey_dilation(_f, size=3)   # 取邻域最大城市号填充
        _take = _m & (_gd > 0)
        if not _take.any():
            break
        _f[_take] = _gd[_take]
    fill_idx = _f
    print(f"  接缝空洞填充：{int(_fill_gap.sum())} px（≤200px 的封闭小洞）")
    del _f, _fill_gap, _gd, _m, _take
del _land, _gap

lut = np.zeros((len(keys) + 1, 3), dtype=np.uint8)
lut[0] = BG_COLOR
for i, key in enumerate(keys):
    lut[i + 1] = city_color[key]
arr = lut[fill_idx]

# ---------- ① 乡镇界 1px（直绘 → thin 中心线） ----------
print(f"① 乡镇界 {LINE_TOWN_PX}px（Bresenham 直绘 → thin 中心线）...")
town_lines = []
for g in gdf.geometry:
    town_lines.extend(extract_lines(g.boundary))
town_arr = draw_layer(town_lines, LINE_TOWN_PX, w_px, h_px, minx, maxy, sx, sy)
print(f"  原始 1px 像素 {int(town_arr.sum())}")
town_skel = thin(town_arr)
del town_arr
town_skel = deblock_2x2(town_skel)
n_blk = int(ndimage.binary_erosion(town_skel,
                                   structure=np.ones((2, 2), dtype=bool)).sum())
print(f"  乡镇层 1px 中心线 {int(town_skel.sum())} px，2×2 粗块 {n_blk}（应为 0）")

# ---------- ② 粗界线（可选，默认关闭） ----------
if DRAW_HIERARCHY:
    print(f"② 地级市界 {LINE_CITY_PX}px + 省级界/海岸线 {LINE_PROV_PX}px"
          f"（matplotlib + 阈值二值化）...")
    city_geoms = [unit_geoms[k] for k in keys]
    prov_geoms = []
    for name, grp in gdf.groupby("省名", sort=False):
        prov_geoms.append(unary_union(list(grp.geometry)))
    coast = unary_union(list(gdf.geometry))
    layers = [(city_geoms, LINE_CITY_PX), (prov_geoms, LINE_PROV_PX),
              ([coast], LINE_PROV_PX)]
    thick_layer = render_line_layer(layers, w_px, h_px, minx, maxx, miny, maxy)
    print(f"  粗线层：{int(thick_layer.sum())} px")

    # 对齐自检：粗线层必须覆盖城市界/省界的 1px 原始线
    _city_1px = draw_layer(extract_lines([g.boundary for g in city_geoms]), 1,
                           w_px, h_px, minx, maxy, sx, sy)
    _miss = int((_city_1px & ~ndimage.binary_dilation(
        thick_layer, structure=np.ones((3, 3), bool))).sum())
    print(f"  地级市界 1px 原始线 {int(_city_1px.sum())} px，"
          f"未被粗线层覆盖 {_miss} px（应 ≈0）")
    del _city_1px
    lines_mask = town_skel | thick_layer
else:
    print("② 粗界线：已关闭（全部界线严格 1px）")
    lines_mask = town_skel
print(f"  黑像素合计 {int(lines_mask.sum())}")

# ---------- 油漆桶填色（膨胀封闭线网作堤坝，4-连通区域整块单色） ----------
print("填色（膨胀封闭线网作堤坝，4-连通区域整块单色）...")
# 相邻城市共享边各自 simplify 后会留下 1px 断缝；直接拿 1px 线网做 4-连通
# 堤坝会漏色，把两座城市并成一块。这里对线网做 3×3 膨胀焊死断缝，并逐次
# 加大直到不再出现跨城市漏色（膨胀环最后由“分界补黑”还原成 1px 黑线）。
def _region_mode(wall):
    nreg, lab = cv2.connectedComponents((~wall).astype(np.uint8), connectivity=4)
    cid = fill_idx.copy()
    cid[wall] = 0
    key = lab.astype(np.int32) * len(lut) + cid.astype(np.int32)
    cnt = np.bincount(key.ravel(), minlength=nreg * len(lut)).reshape(nreg, len(lut))
    del key, cid
    mode = cnt.argmax(axis=1).astype(np.uint8)
    ncity = (cnt[:, 1:] > 0).sum(axis=1)
    return nreg, lab, cnt, mode, np.where(ncity > 1)[0]


for _dil in range(1, 5):
    _wall = ndimage.binary_dilation(lines_mask, structure=np.ones((3, 3), dtype=bool),
                                    iterations=_dil)
    _nreg, _lab, _cnt, _mode, _bleed = _region_mode(_wall)
    if len(_bleed) == 0 or _dil == 4:
        break
    del _lab, _cnt, _mode

# 自检（重上色之前）：一个开放区块内只应出现一种“几何城市”填色。
# 出现多种说明堤坝仍有漏缝，该区块会被主色整块覆盖 → 两座城市串色。
_wrong = int(sum(_cnt[r].sum() - _cnt[r][_mode[r]] for r in _bleed))
print(f"  4-连通区块 {_nreg - 1}（堤坝膨胀 {_dil} 次），"
      f"跨城市漏色区块 {len(_bleed)}（应为 0），错色像素 {_wrong}（应为 0）")
if len(_bleed):
    for _r in _bleed[np.argsort(-_cnt[_bleed].sum(axis=1))][:5]:
        _cs = [keys[v - 1] for v in np.where(_cnt[_r, 1:] > 0)[0] + 1]
        print(f"    ⚠️ 区块 {int(_r)}：{_cs}")

arr = lut[_mode[_lab]]
del _mode, _cnt, _lab
# 膨胀环内非黑线像素先按几何填色（保持线两侧颜色贴到线）；
# 环上若出现“两色直接相邻”，由后面的“分界补黑”压成黑线。
_wall_only = _wall & ~lines_mask
if _wall_only.any():
    arr[_wall_only] = lut[fill_idx[_wall_only]]
del _wall_only
arr[lines_mask] = 0
arr[(fill_idx == 0) & ~lines_mask] = 255

# 自检 B：村外非白——填色区外不得出现填色像素（只允许线像素）
_outside = (fill_idx == 0) & np.any(arr != 255, axis=2) & ~lines_mask
print(f"  村外非白填色像素：{int(_outside.sum())}（应为 0）")
del _outside

# 收尾：被黑线围住、但未被任何城市覆盖的小白洞（≤200px）用邻近城市色补上。
# 这些洞来自两省图资错位/浮点缝，密集边界处会显示为白色麻点。
# 水体栅格上的白洞不补——那是水面，本来就该是白的。
_white = np.all(arr == 255, axis=2)
_nw, _wlab, _wstat, _ = cv2.connectedComponentsWithStats(
    _white.astype(np.uint8), connectivity=4)
_border = np.unique(np.concatenate([_wlab[0, :], _wlab[-1, :],
                                    _wlab[:, 0], _wlab[:, -1]]))
_comp = np.arange(_nw)
_small = (_wstat[:, cv2.CC_STAT_AREA] <= WHITE_FILL_MAX_PX) & ~np.isin(_comp, _border)
_small[0] = False
if water_px is not None and _nw > 1:
    _wcnt = ndimage.sum(water_px, _wlab, index=np.arange(_nw))
    _small &= (_wcnt * 2 <= _wstat[:, cv2.CC_STAT_AREA])
    del _wcnt
_filled = 0
for _i in np.where(_small)[0]:
    _x, _y, _w, _h = (int(v) for v in _wstat[_i, :4])
    _x0, _y0 = max(0, _x - 1), max(0, _y - 1)
    _x1, _y1 = min(w_px, _x + _w + 1), min(h_px, _y + _h + 1)
    _sub = _wlab[_y0:_y1, _x0:_x1] == _i
    _ring = ndimage.binary_dilation(_sub, structure=np.ones((3, 3), bool)) & ~_sub
    _cols = arr[_y0:_y1, _x0:_x1][_ring]
    _cols = _cols[(_cols != 0).any(axis=1) & (_cols != 255).any(axis=1)]
    if len(_cols) == 0:
        continue
    _u, _c = np.unique(_cols, axis=0, return_counts=True)
    arr[_y0:_y1, _x0:_x1][_sub] = _u[_c.argmax()]
    _filled += int(_sub.sum())
print(f"  封闭白点回填：{_filled} px（≤200px 空洞）")
del _white, _nw, _wlab, _wstat, _border, _comp, _small

# 最终收尾：彻底消除“两种城市色直接 4-相邻”。在分界处把膨胀环上的一侧压成
# 黑线（优先选膨胀环像素，尽量贴住原线），补黑点落在线旁，视觉上仍是细线。
_packed = ((arr[:, :, 0].astype(np.uint32) << 16) |
           (arr[:, :, 1].astype(np.uint32) << 8) | arr[:, :, 2].astype(np.uint32))
def _color_pair(a, b):
    return (a != b) & (a != 0) & (b != 0) & (a != 0xFFFFFF) & (b != 0xFFFFFF)
_h = _color_pair(_packed[:, :-1], _packed[:, 1:])
_v = _color_pair(_packed[:-1, :], _packed[1:, :])
_paint = np.zeros(arr.shape[:2], dtype=bool)
_hwr, _hwl = _wall[:, 1:], _wall[:, :-1]
_paint[:, 1:] |= _h & _hwr
_paint[:, :-1] |= _h & ~_hwr & _hwl
_paint[:, 1:] |= _h & ~_hwr & ~_hwl
_vwd, _vwu = _wall[1:, :], _wall[:-1, :]
_paint[1:, :] |= _v & _vwd
_paint[:-1, :] |= _v & ~_vwd & _vwu
_paint[1:, :] |= _v & ~_vwd & ~_vwu
_n_paint = int(_paint.sum())
arr[_paint] = 0
_packed[_paint] = 0
_left = int(_color_pair(_packed[:, :-1], _packed[:, 1:]).sum() +
            _color_pair(_packed[:-1, :], _packed[1:, :]).sum())
_black = ~np.any(arr, axis=2)
_n2x2 = int(ndimage.binary_erosion(_black, structure=np.ones((2, 2), dtype=bool)).sum())
print(f"  分界补黑：{_n_paint} px，剩余两色直接相邻 {_left}（应为 0），"
      f"2×2 黑块 {_n2x2}（应力求很小）")
del _packed, _paint, _h, _v, _hwr, _hwl, _vwd, _vwu, _color_pair, _black, _wall

# 跨水的辖区界线清除：只擦「左右（或上下）两侧都是水」的黑像素——那是横穿
# 海湾的辖区界线；海岸线一侧是陆地，不会被误擦。
# 必须放在填色之后：若在油漆桶之前擦线，海岸线一断，海和陆地就连成同一个
# 4-连通区块，区块主色（白）会把整片陆地盖掉。
if water_px is not None:
    _blk = ~np.any(arr, axis=2)
    _both = np.zeros_like(_blk)
    _both[:, 1:-1] = water_px[:, :-2] & water_px[:, 2:]
    _both[1:-1, :] |= water_px[:-2, :] & water_px[2:, :]
    _n_wline = int((_blk & _both).sum())
    if _n_wline:
        arr[_blk & _both] = 255
        print(f"  跨水界线清除 {_n_wline} px（两侧皆水的黑像素）")
    del _blk, _both

# ---------- 硬约束：没有面的地方、水面，一律留白 ----------
# 上面几步都是"补色"，补错了会把没有面/水面的地方涂成辖区色。这里按最终几何
# 再核对一遍：彩色像素必须同时满足
#   ① 落在某个地级市面上（all_touched，兼顾亚像素小岛）
#   ② 不在水体栅格上
# 不满足的一律刷白。这条是兜底，保证「SHP 里没有面 → 白」「水 → 白」恒成立。
_paint_any = rasterize([(g, 1) for g in unit_geoms.values()],
                       out_shape=(h_px, w_px), transform=transform,
                       fill=0, dtype="uint8", all_touched=True) > 0
_colored = np.any(arr != 255, axis=2) & np.any(arr != 0, axis=2)
_n_noface = int((_colored & ~_paint_any).sum())
if _n_noface:
    arr[_colored & ~_paint_any] = 255
    print(f"  无面区域刷白 {_n_noface} px（SHP 里没有面的地方必须是白的）")
if water_px is not None:
    _n_wcol = int((_colored & water_px).sum())
    if _n_wcol:
        arr[_colored & water_px] = 255
        print(f"  水面刷白 {_n_wcol} px（水域一律留白）")
# 事后自检：这两项必须为 0
_chk = np.any(arr != 255, axis=2) & np.any(arr != 0, axis=2)
_c_noface = int((_chk & ~_paint_any).sum())
_c_water = int((_chk & water_px).sum()) if water_px is not None else 0
print(f"  自检：彩色 {int(_chk.sum())} px，其中无面 {_c_noface}、水面 {_c_water}（均应为 0）")
del _chk, _colored, _paint_any

# ---------- 颜色验证 ----------
_uniq, _ucnt = np.unique(arr.reshape(-1, 3), axis=0, return_counts=True)
_allowed = {(255, 255, 255), (0, 0, 0)} | {city_color[k] for k in keys}
_bad = [(tuple(int(x) for x in c), int(n)) for c, n in zip(_uniq, _ucnt)
        if tuple(int(x) for x in c) not in _allowed]
print(f"  全图颜色种类：{len(_uniq)}（允许 {len(_allowed)}）；非允许色像素 "
      f"{sum(n for _, n in _bad)} px（应为 0）")
if _bad:
    print(f"  ⚠️ 非允许色：{_bad[:5]}")

# ---------- 保存 ----------
def save_png(img, path):
    """保存 PNG；目标被占用时自动改存 *_新.png（Windows 常见 Errno 22）。"""
    try:
        img.save(path, format="png")
        return path
    except OSError as e:
        alt = os.path.splitext(path)[0] + "_新.png"
        img.save(alt, format="png")
        print(f"  ⚠️ 目标被占用（{e}），改存 {alt}")
        return alt


canvas = Image.fromarray(arr, mode="RGB")
del arr
out_path = save_png(canvas, out_file)
print(f"已保存 {out_path}（{w_px}×{h_px}）")

k = PREVIEW_PX / max(w_px, h_px)
pw, ph = max(1, int(w_px * k)), max(1, int(h_px * k))
prev_path = save_png(canvas.resize((pw, ph), Image.LANCZOS), preview_file)
print(f"已保存 {prev_path}（{pw}×{ph}）")

# 存档自检
_re = np.asarray(Image.open(out_path).convert("RGB"))
_u2, _c2 = np.unique(_re.reshape(-1, 3), axis=0, return_counts=True)
_bad2 = sum(n for c, n in zip(_u2, _c2)
            if tuple(int(x) for x in c) not in _allowed)
print(f"  存档自检：非允许色 {_bad2} px（应为 0），尺寸 {_re.shape[1]}×{_re.shape[0]}")
print("\n✅ 华南乡镇界线图生成完毕（无地名标注）")
