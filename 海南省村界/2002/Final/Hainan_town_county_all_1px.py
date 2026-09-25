# -*- coding: utf-8 -*-
"""
海南本岛 · 乡镇边界 + 县市边界地图（无地名）——全图严格 1px 版
目标：所有界线（乡镇界+县市界）严格 1 像素宽、纯黑(0)/纯白(255)两色，
     不夹杂 2px 粗段，也不含任何灰色像素。输出 4000×3156（70.2 m/px）。

[方法：Bresenham 直线描绘 + Zhang-Suen 拓扑保持细化]
  光栅化+阈值路线（超采样/LANCZOS/二值化）原理上做不到严格 1px：
  矢量线落在两像素列之间时两列都被判黑，1px 线里必然夹杂 2px 粗段。
  本版不经 matplotlib 渲染，直接把矢量边界逐段映射到最终分辨率画布，
  用 Bresenham 算法逐像素描绘（width=1），再用 skimage.morphology.thin
  做拓扑保持细化，清掉斜率过渡点的残余 2x2 实心块：
  输出骨架处处恰好 1px、8-连通拓扑不变（不断线）。
  （县市界如需 2px 加粗版，见 Hainan_town_county_1px.py / _town1_county2 路线）
输出：Hainan_town_county_all_1px.png（不覆盖其他版本）
"""
import os
import warnings
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.ops import unary_union
from PIL import Image, ImageDraw
from skimage.morphology import thin
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

# 粗比例尺取舍：轻度简化（20m ≈ 0.3px）。在 dissolve 之前做，
# 乡镇边界与县市边界派生自同一套简化后几何，共享边保持逐点一致。
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

lines = []
# 乡镇边界：合并共享边只描一次
lines.extend(extract_lines(unary_union(township.geometry.boundary.tolist())))
# 县市边界：叠加其上（同为 1px，不过滤小岛，海岸线权重连续）
lines.extend(extract_lines(counties.geometry.boundary.union_all()
                          if hasattr(counties.geometry, "union_all")
                          else counties.geometry.unary_union))
print(f"矢量线段条数：{len(lines)}")

# ---------- Bresenham 逐段描绘（1px 基底，纯黑）----------
img = Image.new("L", (w_px, h_px), 255)
draw = ImageDraw.Draw(img)
n_pts = 0
for coords in lines:
    pts = [(int(round((x - minx) * sx)), int(round((maxy - y) * sy)))
           for x, y in coords]
    if len(pts) >= 2:
        draw.line(pts, fill=0, width=1)
        n_pts += len(pts)
print(f"描绘顶点数：{n_pts}")

# ---------- 拓扑保持细化（Zhang-Suen）→ 严格 1px ----------
arr = np.asarray(img) < 128
n_comp_before = ndimage.label(arr, structure=np.ones((3, 3)))[1]
skel = thin(arr)
n_comp_after = ndimage.label(skel, structure=np.ones((3, 3)))[1]
sq = (skel[:-1, :-1] & skel[1:, :-1] & skel[:-1, 1:] & skel[1:, 1:]).sum()
print(f"细化前连通分量 {n_comp_before} → 细化后 {n_comp_after}；"
      f"2x2 实心块残余 {int(sq)}")
out_arr = np.where(skel, 0, 255).astype(np.uint8)
img = Image.fromarray(out_arr, mode="L")

out_file = os.path.join(out_dir, "Hainan_town_county_all_1px.png")
img.convert("RGB").save(out_file, format="png")
print(f"已保存 {out_file}（{w_px}×{h_px}）")
print("\n✅ 全图严格 1px 版乡镇+县市边界地图生成完毕")
