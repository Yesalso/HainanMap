# -*- coding: utf-8 -*-
"""
诊断 v4：找出"没被识别到的陆地县市界"。
  检查1：共享边 1px 光栅中，走廊法骨架未覆盖（±1px 内无骨架）的断点段。
  检查2：不同县市乡镇隔水相望（不接触但距离近）的边界段——当前方案完全没有
         2px 线的位置；列出宽度/长度/位置并裁图。
"""
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.geometry import MultiPoint
from PIL import Image, ImageDraw
from skimage.morphology import thin
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None

shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo_v3.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final"

TARGET_WIDTH_PX = 4000
SIMPLIFY_TOL_M = 20
TARGET_CRS = "EPSG:32649"
PX_M = None  # 运行时算

HAIKOU_KEEP = {
    "中山街道", "滨海街道", "金贸街道", "大同街道", "海垦街道", "国兴街道",
    "海府街道", "博爱街道", "白龙街道", "蓝天街道", "和平南街道", "白沙街道",
    "人民路街道", "海甸街道", "新埠街道", "海秀街道", "秀英街道", "金宇街道",
    "西秀镇", "长流镇", "海秀镇", "城西镇", "新海乡",
}

gdf = gpd.read_file(shp_path, encoding="utf-8").to_crs(TARGET_CRS)
gdf["geometry"] = gdf.geometry.buffer(0)
if SIMPLIFY_TOL_M > 0:
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_TOL_M, preserve_topology=True)

haikou = gdf[gdf["CITY"] == "海口市"].copy()
haikou["县市"] = haikou["TOWN"].apply(lambda t: "海口市" if t in HAIKOU_KEEP else "琼山市")
others = gdf[gdf["CITY"] != "海口市"].copy()
others["县市"] = others["CITY"]
county_df = gpd.GeoDataFrame(
    pd.concat([haikou[["县市", "TOWN", "geometry"]], others[["县市", "TOWN", "geometry"]]],
              ignore_index=True),
    crs=TARGET_CRS)

minx, miny, maxx, maxy = county_df.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
w_px = TARGET_WIDTH_PX
h_px = int(round(geo_h / (geo_w / w_px)))
PX_M = geo_w / w_px
sx, sy = w_px / geo_w, h_px / geo_h
print(f"比例尺 {PX_M:.1f} m/px")

def to_px(coords):
    return [(int(round((x - minx) * sx)), int(round((maxy - y) * sy))) for x, y in coords]

def draw_lines(lines):
    im = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(im)
    for coords in lines:
        pts = to_px(coords)
        if len(pts) >= 2:
            d.line(pts, fill=0, width=1)
    return np.asarray(im) < 128

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

town_arr = draw_lines(extract_lines(unary_union(county_df.geometry.boundary.tolist())))
town_skel = thin(town_arr)

# ---- 共享边（当前方案） ----
geoms = county_df.geometry.values
cnty = county_df["县市"].values
tnames = county_df["TOWN"].values
tree = STRtree(geoms)
shared_lines = []
pairs = []
for i in range(len(geoms)):
    for j in tree.query(geoms[i]):
        j = int(j)
        if j <= i or not geoms[i].intersects(geoms[j]):
            continue
        if cnty[i] == cnty[j]:
            continue
        inter = geoms[i].intersection(geoms[j])
        if inter.length >= 1.0:
            shared_lines.extend(extract_lines(inter))
            pairs.append((i, j, inter.length, 0.0))
shared_r = draw_lines(shared_lines)
S2 = np.ones((2, 2), dtype=bool)
S3 = np.ones((3, 3), dtype=bool)
corridor = ndimage.binary_dilation(shared_r, structure=S3)
county_skel = town_skel & corridor

# ===== 检查1：共享边像素 ±1px 内无骨架的（当前 2px 会漏的）=====
d_to_skel = ndimage.distance_transform_edt(~county_skel)
miss = shared_r & (d_to_skel > 1)
print(f"\n[检查1] 共享边像素总数 {int(shared_r.sum())}，骨架未覆盖(>1px): {int(miss.sum())}")
if miss.sum():
    lbl, n = ndimage.label(miss, structure=np.ones((3, 3), bool))
    sizes = ndimage.sum(miss, lbl, range(1, n + 1))
    order = np.argsort(sizes)[::-1]
    print(f"  断点段 {n} 个，最大10段: {sizes[order[:10]].astype(int).tolist()}")

# ===== 检查2：隔水相望的不同县市乡镇对 =====
print("\n[检查2] 不同县市、不接触但距离 <=5km 的乡镇对（隔水边界）:")
gaps = []
for i in range(len(geoms)):
    if cnty[i] == "": continue
    for j in tree.query(geoms[i].buffer(5000)):
        j = int(j)
        if j <= i or cnty[i] == cnty[j]:
            continue
        d = geoms[i].distance(geoms[j])
        if d <= 0 or d > 5000:
            continue
        # 最近点对
        from shapely.ops import nearest_points
        p1, p2 = nearest_points(geoms[i], geoms[j])
        gaps.append((d, cnty[i], tnames[i], cnty[j], tnames[j], p1.x, p1.y, p2.x, p2.y))

gaps.sort(key=lambda g: g[0])
seen = set()
for d, ca, ta, cb, tb, x1, y1, x2, y2 in gaps:
    key = (ca, cb, round(x1/2000), round(y1/2000))
    if key in seen:
        continue
    seen.add(key)
    print(f"  {d:7.1f}m  {ca}:{ta} <-> {cb}:{tb}  近点=({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})")
print(f"共 {len(gaps)} 对（去重前）")
