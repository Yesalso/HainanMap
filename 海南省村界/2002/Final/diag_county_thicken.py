# -*- coding: utf-8 -*-
"""
诊断 v2：直接在最终成图上量化"被加粗到 2px、但远离真县市界"的像素。
真县市界(矢量基准) = 跨县市乡镇共享边 ∪ 各县市 dissolve 外缘的海岸段。
输出统计 + 标红诊断图。
"""
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from shapely.strtree import STRtree
from PIL import Image, ImageDraw
from skimage.morphology import thin
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None

shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo_v3.shp"
out_png = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/diag_county_thicken.png"

TARGET_WIDTH_PX = 4000
SIMPLIFY_TOL_M = 20
TARGET_CRS = "EPSG:32649"

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
    pd.concat([haikou[["县市", "geometry"]], others[["县市", "geometry"]]], ignore_index=True),
    crs=TARGET_CRS)

minx, miny, maxx, maxy = county_df.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
w_px = int(round(geo_w / (geo_w / TARGET_WIDTH_PX)))
h_px = int(round(geo_h / (geo_w / TARGET_WIDTH_PX)))
sx, sy = w_px / geo_w, h_px / geo_h

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

# --- 与原脚本一致 ---
town_lines = extract_lines(unary_union(county_df.geometry.boundary.tolist()))
town_arr = draw_lines(town_lines)
town_skel = thin(town_arr)

counties = county_df.dissolve(by="县市").reset_index()
county_lines = extract_lines(counties.geometry.boundary.union_all())
county_1px = draw_lines(county_lines)
S2 = np.ones((2, 2), dtype=bool)
S3 = np.ones((3, 3), dtype=bool)
corridor = ndimage.binary_dilation(county_1px, structure=S3)
county_skel = town_skel & corridor
county_arr = ndimage.binary_dilation(county_skel, structure=S2)
out_black = town_skel | county_arr

# --- 矢量基准 1：跨县市共享边（陆地县市界） ---
geoms = county_df.geometry.values
cnty = county_df["县市"].values
tree = STRtree(geoms)
shared_lines = []
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
shared_r = draw_lines(shared_lines)

# --- 矢量基准 2：县市 dissolve 外缘中的海岸段（县市界在海上的部分） ---
coast_r = county_1px & ~ndimage.binary_dilation(shared_r, structure=S3)
county_true = shared_r | coast_r
print(f"陆地县市界像素: {int(shared_r.sum())}，县市海岸段像素: {int(coast_r.sum())}，合计 {int(county_true.sum())}")

# --- 在成图上找"2px 宽"的黑色像素（水平或垂直方向连续 2px 线的成员） ---
b = out_black
run_h = b[:, :-1] & b[:, 1:]          # 水平相邻对
run_v = b[:-1, :] & b[1:, :]          # 垂直相邻对
thick = np.zeros_like(b)
thick[:, :-1] |= run_h; thick[:, 1:] |= run_h
thick[:-1, :] |= run_v; thick[1:, :] |= run_v
thick &= b

# --- 远离任何真县市界(>1px) 却呈 2px 的像素 = 误加粗 ---
far_thick = thick & ~ndimage.binary_dilation(county_true, structure=S3)
print(f"成图 2px 宽像素总数: {int(thick.sum())}")
print(f"其中距真县市界 >1px 的误加粗像素: {int(far_thick.sum())}")

lbl, n = ndimage.label(far_thick, structure=np.ones((3, 3), bool))
if n:
    sizes = ndimage.sum(far_thick, lbl, range(1, n + 1))
    order = np.argsort(sizes)[::-1]
    print(f"误加粗连通段: {n} 段；最大 10 段像素数: {sizes[order[:10]].astype(int).tolist()}")
    # 最大段位置
    for k in order[:5]:
        ys, xs = np.where(lbl == k + 1)
        print(f"  段中心 px=({int(xs.mean())},{int(ys.mean())}) 像素数={int(sizes[k])}")

# --- 诊断图：黑=成图线条，红=误加粗像素，蓝=矢量真县市界 ---
base = np.where(out_black, 0, 255).astype(np.uint8)
rgb = np.stack([base]*3, axis=-1)
ys, xs = np.where(far_thick)
rgb[ys, xs] = [255, 0, 0]
ys2, xs2 = np.where(county_true & ~far_thick)
w = rgb[ys2, xs2, 0] == 255
rgb[ys2[w], xs2[w]] = [60, 90, 255]
Image.fromarray(rgb, "RGB").save(out_png)
print(f"诊断图: {out_png}")
