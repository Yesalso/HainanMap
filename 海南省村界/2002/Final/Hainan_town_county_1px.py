# -*- coding: utf-8 -*-
"""
海南本岛 · 乡镇边界 + 县市边界地图（无地名）—— 乡镇 1px + 县市 2px 版
目标：乡镇界严格 1 像素宽，县市界加粗为 2 像素宽；纯黑(0)/纯白(255)两色，
     无灰色像素。输出 4000×3156（70.2 m/px）。

[方法：双层 Bresenham 直线描绘 + "最近两侧县市"距离场判别]
  1. 乡镇层：Bresenham width=1 逐像素描绘 → skimage.morphology.thin
     （Zhang-Suen 拓扑保持细化）→ 骨架处处恰好 1px、不断线；
  2. 县市层（距离场判别法）：
     对每个像素计算最近两个县市的距离 d1/d2 与归属 c1/c2（各县市面
     fill 后取 EDT）。县市界判据：c1≠c2 且 d2-d1 ≤ 1px。
       - 陆地共享边：两侧县市 d1=d2=0，命中（含 0.2~50m 亚像素缝隙——
         矢量求交会因 length<1m 漏掉，距离场不会漏）；
       - 宽河道/海湾两岸：岸上 d1=0、d2=河宽>1px，不命中 → 不碰河岸；
       - 水面中线：|d1-d2|≤1px 的等距带自动落在河道/海湾中央，
         骨架化后作为县市界（不碰两岸）。
  3. 加粗：对锚点(乡镇骨架∩判别带 ∪ 水面中线 ∪ 骨架缺失补点)做
     np.ones((2,2)) 方形膨胀一圈 → 严格 2px、以可见线条为中心。
  注意：不能用十字结构膨胀（会把 1px 直线扩成 3px）；
        不能直接用 PIL width=2（偶数线宽段交接丢像素成虚线）。
输出：Hainan_town_county_town1_county2.png（不覆盖其他版本）
"""
import os
import warnings
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.ops import unary_union
from PIL import Image, ImageDraw
from skimage.morphology import thin, skeletonize
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo_v3.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002"

# 目标图宽约 4000px：自动反算米/像素比例尺（280.8 km 岛宽 → 约 70 m/px）
TARGET_WIDTH_PX = 4000
MAX_PX = 20000
SIMPLIFY_TOL_M = 20   # 粗比例尺取舍：20m ≈ 0.3px，视觉不可见，
                      # 但可滤掉远小于 1px 的顶点抖动，避免毛刺和退化碎段
TARGET_CRS = "EPSG:32649"
BAND_TOL_PX = 1.0     # 县市界判别容差：|d1-d2| ≤ 1px（容纳 thin 骨架 ±1px 偏移）
MEDIAN_MAX_D_PX = 10  # 水面中线仅在水深 ≤10px(约700m) 的河道/窄湾内生成，
                      # 防止沿海县市交界处向海里拖出长须
MEDIAN_MIN_LEN = 50   # 中线连通段最短像素数（<50px 的碎须剔除，约3.5km 内的
                      # 短段保留——正常河道中线都远长于此）
# 不画水面中线的县市对：
#   琼山市-文昌市：东寨港/铺前湾（海湾中不画县界线）；
#   海口市-琼山市：南渡江府城镇-新埠岛一带（河中不画县界线）。
# 县界只到两岸乡镇界线为止。其余河道（文澜江等）中线照常。
EXCLUDE_MEDIAN_PAIRS = {("琼山市", "文昌市"), ("文昌市", "琼山市"),
                        ("海口市", "琼山市"), ("琼山市", "海口市")}
# ==================================================

warnings.filterwarnings("ignore")
os.makedirs(out_dir, exist_ok=True)

# ---------- 海口市保留名单 ----------
HAIKOU_KEEP = {
    "中山街道", "滨海街道", "金贸街道", "大同街道", "海垦街道", "国兴街道",
    "海府街道", "博爱街道", "白龙街道", "蓝天街道", "和平南街道", "白沙街道",
    "人民路街道", "海甸街道", "新埠街道", "海秀街道", "秀英街道", "金宇街道",
    "西秀镇", "长流镇", "海秀镇", "城西镇", "新海乡",
}

# ---------- 读取 SHP ----------
gdf = gpd.read_file(shp_path, encoding="utf-8")
print(f"原始要素数：{len(gdf)}")
gdf = gdf.to_crs(TARGET_CRS)
gdf["geometry"] = gdf.geometry.buffer(0)

# 粗比例尺取舍：轻度简化（20m ≈ 0.3px）
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

# ---------- 画布尺寸 ----------
minx, miny, maxx, maxy = township.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
PIXEL_TO_METER = geo_w / TARGET_WIDTH_PX
w_px = int(round(geo_w / PIXEL_TO_METER))
h_px = int(round(geo_h / PIXEL_TO_METER))
if w_px > MAX_PX or h_px > MAX_PX:
    scale = min(MAX_PX / w_px, MAX_PX / h_px)
    w_px, h_px = int(round(w_px * scale)), int(round(h_px * scale))
sx, sy = w_px / geo_w, h_px / geo_h
print(f"比例尺 {PIXEL_TO_METER:.1f} m/px，范围 {geo_w:.0f}×{geo_h:.0f} m，像素 {w_px}×{h_px}")

# ---------- 收集矢量边界线 ----------
def extract_lines(gs):
    """GeoSeries/Geometry -> 坐标序列列表（LineString 的 coords）"""
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

# ---------- Bresenham 逐段描绘函数（width=1 时严格 1px）----------
def draw_layer(lines, fill_polys=None):
    im = Image.new("L", (w_px, h_px), 255)
    d = ImageDraw.Draw(im)
    if fill_polys:
        for coords in fill_polys:
            pts = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
                   for x, y in coords]
            if len(pts) >= 3:
                d.polygon(pts, fill=0)
    n_pts = 0
    for coords in lines:
        pts = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
               for x, y in coords]
        if len(pts) >= 2:
            d.line(pts, fill=0, width=1)
            n_pts += len(pts)
    return np.asarray(im) < 128

# ---------- 乡镇层：1px 描绘 + 拓扑保持细化（Zhang-Suen）----------
print("乡镇层 1px 描绘：")
town_lines = extract_lines(unary_union(township.geometry.boundary.tolist()))
town_arr = draw_layer(town_lines)
n_comp_before = ndimage.label(town_arr, structure=np.ones((3, 3)))[1]
town_skel = thin(town_arr)
n_comp_after = ndimage.label(town_skel, structure=np.ones((3, 3)))[1]
sq = (town_skel[:-1, :-1] & town_skel[1:, :-1] & town_skel[:-1, 1:] & town_skel[1:, 1:]).sum()
print(f"细化前连通分量 {n_comp_before} → 细化后 {n_comp_after}；2x2 实心块残余 {int(sq)}")

# ---------- 县市层：最近两侧县市距离场判别 ----------
print("县市层：距离场判别（最近两侧县市）...")
county_names = counties["县市"].tolist()
county_geoms = counties.geometry.values
n_c = len(county_names)

# 各县市 fill 后取 EDT，维护每个像素最近的两个县市 d1/c1、d2/c2
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
    lt = dist < d1                       # 新县市更近：旧 (d1,c1) 下放为 (d2,c2)
    take2 = (~lt) & (dist < d2)          # c1 不变，dist 进入 c2
    d2 = np.where(lt, d1, np.where(take2, dist, d2))
    c2 = np.where(lt, c1, np.where(take2, k, c2))
    d1 = np.where(lt, dist, d1)
    c1 = np.where(lt, k, c1)

water = ~land
band = (c1 != c2) & (c1 >= 0) & (c2 >= 0) & ((d2 - d1) <= BAND_TOL_PX)
print(f"判别带像素：{int(band.sum())}（陆地部分 {int((band & land).sum())}，水面部分 {int((band & water).sum())}）")

# 锚点 1：乡镇骨架落在判别带内（陆地县市界，含亚像素缝隙两侧岸线）
d_to_skel = ndimage.distance_transform_edt(~town_skel)
anchor = town_skel & band & land
n_sk = int(anchor.sum())

# 锚点 2：骨架缺失补点——判别带内陆地像素，距任何骨架 >1.5px
#（thin 在个别交会处削掉的 1-2px，保证 2px 线连续）
fallback = band & land & (d_to_skel > 1.5)
anchor |= fallback
print(f"锚点：骨架命中 {n_sk}，骨架缺失补点 {int(fallback.sum())}")

# 锚点 3：水面中线（等距带骨架化）——河道/海湾中央，不碰两岸
# 县市对用索引编码（c1*n_c+c2），避免字符串比较的歧义
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
    print(f"水面中线骨架 {int(med_skel.sum())} px（剔除碎须 {dropped} px，{n} 段）")
    anchor = anchor | med_skel
else:
    print("水面中线：无")

# ---------- 加粗为 2px（2x2 方形结构，以锚点为中心）----------
S2 = np.ones((2, 2), dtype=bool)
county_arr = ndimage.binary_dilation(anchor, structure=S2)

# ---------- 合并：乡镇 1px 骨架 ∪ 县市 2px 加粗线 ----------
out_arr = np.where(town_skel | county_arr, 0, 255).astype(np.uint8)
img = Image.fromarray(out_arr, mode="L")

out_file = os.path.join(out_dir, "Hainan_town_county_town1_county2.png")
img.convert("RGB").save(out_file, format="png")
print(f"已保存 {out_file}（{w_px}×{h_px}）")
print("\n✅ 乡镇 1px + 县市 2px 边界地图生成完毕")
