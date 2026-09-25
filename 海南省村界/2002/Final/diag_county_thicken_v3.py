# -*- coding: utf-8 -*-
"""
诊断 v3：把走廊法加粗的骨架分解成三类，量化各类占比并裁图取证。
  A_on   = 选中骨架恰好落在"陆地县市共享边"1px 光栅上（正确）
  A_off  = 距陆地县界 1px（thin 骨架偏移的合法容差 或 邻近平行乡镇线）
  B      = 远离陆地县界、只因"县市 dissolve 外缘包含海岸"而被选中的海岸/岛屿骨架
并输出三县交界点附近成图裁剪，检查交界处乡镇线末梢被误加粗的情况。
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
real_out = "D:/Windows/Documents/海南省村界/海南省村界/2002/Hainan_town_county_town1_county2.png"

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
w_px = TARGET_WIDTH_PX
h_px = int(round(geo_h / (geo_w / w_px)))
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

# ---- 与原脚本一致的两层 ----
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

# ---- 矢量基准：陆地县市共享边 ----
geoms = county_df.geometry.values
cnty = county_df["县市"].values
tree = STRtree(geoms)
shared_lines = []
edge_endpoints = []
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
            for ln in extract_lines(inter):
                edge_endpoints.append(ln[0])
                edge_endpoints.append(ln[-1])
shared_r = draw_lines(shared_lines)

# ---- 三县交界点：共享边端点聚类（>=3 条不同边汇聚） ----
from shapely.geometry import Point
pts = [Point(p) for p in edge_endpoints]
ptree = STRtree(pts)
clusters = []
used = set()
for k, p in enumerate(pts):
    if k in used:
        continue
    idxs = [int(x) for x in ptree.query(p.buffer(30))]
    used.update(idxs)
    if len(idxs) >= 6:  # >=3 条边（每边2端点）
        clusters.append((p.x, p.y, len(idxs) // 2))

# ---- 距离分解 ----
d_shared = ndimage.distance_transform_edt(~shared_r)
sel_d = d_shared[county_skel]
n_on = int((sel_d == 0).sum())
n_1 = int((sel_d == 1).sum())
n_far = int((sel_d >= 2).sum())
total = int(county_skel.sum())
print(f"走廊法选中骨架总数: {total}")
print(f"  A_on  恰在陆地县界光栅上: {n_on} ({n_on/total*100:.1f}%)")
print(f"  A_off 距陆地县界 1px    : {n_1} ({n_1/total*100:.1f}%)  <- thin偏移容差 或 平行乡镇线误选")
print(f"  B     距陆地县界 >=2px  : {n_far} ({n_far/total*100:.1f}%)  <- 海岸/岛屿(县市外缘含海岸) + 远处误选")

# B 中多少其实靠近 county_1px 的海岸部分
coast_only = county_1px & ~ndimage.binary_dilation(shared_r, structure=S3)
d_coast = ndimage.distance_transform_edt(~coast_only)
b_mask = county_skel & (d_shared >= 2)
b_near_coast = int((d_coast[b_mask] <= 1).sum())
print(f"  B 中距县市海岸线 <=1px: {b_near_coast}（其余 {int(b_mask.sum())-b_near_coast} 为真正远离县界的误选）")

far_mask = county_skel & (d_shared >= 2) & (d_coast > 1)
print(f"  真正远离任何县市外缘的误选骨架: {int(far_mask.sum())}")
lbl, nseg = ndimage.label(far_mask, structure=np.ones((3, 3), bool))
if nseg:
    sizes = ndimage.sum(far_mask, lbl, range(1, nseg + 1))
    order = np.argsort(sizes)[::-1]
    print(f"  误选段 {nseg} 个，最大10段像素: {sizes[order[:10]].astype(int).tolist()}")
    np.save(out_dir + "/diag_far_lbl.npy", lbl)
    np.save(out_dir + "/diag_far_sizes.npy", sizes[order])

# ---- 裁图：三县交界点附近的真实成图 ----
real = Image.open(real_out).convert("RGB")
def crop_real(px, py, name, r=120, zoom=4):
    box = (max(0, px - r), max(0, py - r), min(w_px, px + r), min(h_px, py + r))
    c = real.crop(box)
    c = c.resize((c.width * zoom, c.height * zoom), Image.NEAREST)
    c.save(f"{out_dir}/{name}.png")

def geo2px(x, y):
    return int(round((x - minx) * sx)), int(round((maxy - y) * sy))

clusters.sort(key=lambda c: -c[2])
for k, (x, y, nedge) in enumerate(clusters[:6]):
    px, py = geo2px(x, y)
    crop_real(px, py, f"diag_junction_{k+1}")
    print(f"交界点{k+1}: {nedge} 条共享边汇聚, px=({px},{py}) -> diag_junction_{k+1}.png")

# ---- 裁图：最大误选段 ----
if nseg:
    for k in range(min(3, nseg)):
        seg_id = order[k] + 1
        ys, xs = np.where(lbl == seg_id)
        crop_real(int(xs.mean()), int(ys.mean()), f"diag_far_{k+1}")
        print(f"误选段{k+1}: px=({int(xs.mean())},{int(ys.mean())}) 像素={int(sizes[order[k]])} -> diag_far_{k+1}.png")
