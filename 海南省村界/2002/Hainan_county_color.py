# -*- coding: utf-8 -*-
"""
海南本岛 · 县市地图（纯色，无名称）
读取 Final/Hainan_town_topo_v3.shp
海口市仅保留指定街道/镇/乡，其余原海口市乡镇划入 琼山市
县域溶解后整岛填充单一纯色，线条仅一种颜色（黑色），不标注任何地名
输出文件名：Hainan_county_color.png
"""
import os
import warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo_v3.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final"

TARGET_DPI = 100
PIXEL_TO_METER = 50
SIMPLIFY_TOL_M = 5
SNAP_GRID_M = 10
COUNTY_LINE_PX = 1.0
MAX_PX = 20000
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
cols = gdf.columns.tolist()
name_col = "TOWN"
city_col = "CITY"
print(f"字段：{cols}")

gdf = gdf.to_crs(TARGET_CRS)
gdf["geometry"] = gdf.geometry.buffer(0).simplify(
    SIMPLIFY_TOL_M, preserve_topology=True)
if SNAP_GRID_M > 0:
    import shapely
    gdf["geometry"] = gdf.geometry.apply(lambda g0: shapely.set_precision(g0, SNAP_GRID_M))
print(f"几何清理完成，有效 {int(gdf.geometry.is_valid.sum())}/{len(gdf)}")

# ---------- 海口市重划 · 县市归属 ----------
haikou = gdf[gdf[city_col] == "海口市"].copy()
haikou["县市"] = haikou[name_col].apply(
    lambda t: "海口市" if t in HAIKOU_KEEP else "琼山市")

others = gdf[gdf[city_col] != "海口市"].copy()
others["县市"] = others[city_col]

county_df = gpd.GeoDataFrame(
    pd.concat([haikou[["县市", "geometry"]], others[["县市", "geometry"]]], ignore_index=True),
    crs=TARGET_CRS)

kept = (haikou["县市"] == "海口市").sum()
moved = (haikou["县市"] == "琼山市").sum()
print(f"海口市保留 {kept} 个乡镇，划入琼山市 {moved} 个乡镇")

# ---------- 按县市溶解 ----------
counties = county_df.dissolve(by="县市").reset_index()
counties = counties.sort_values("县市").reset_index(drop=True)
print(f"县市单元数：{len(counties)}")
print("县市列表：", counties["县市"].tolist())

# ---------- 配色 ----------
ISLAND_FILL = "#F1F1F1"   # 整岛单一纯色填充
LINE_COLOR = "black"      # 线条仅一种颜色
LINE_WIDTH_PX = 1.0
LINE_WIDTH_PT = LINE_WIDTH_PX * 72.0 / TARGET_DPI

# ---------- 绘图 ----------
total_bounds = counties.total_bounds
minx, miny, maxx, maxy = total_bounds
geo_w, geo_h = maxx - minx, maxy - miny

w_px = int(round(geo_w / PIXEL_TO_METER))
h_px = int(round(geo_h / PIXEL_TO_METER))
if w_px > MAX_PX or h_px > MAX_PX:
    scale = min(MAX_PX / w_px, MAX_PX / h_px)
    w_px, h_px = int(round(w_px * scale)), int(round(h_px * scale))
print(f"范围 {geo_w:.0f}×{geo_h:.0f} m，像素 {w_px}×{h_px}")

fig = plt.figure(figsize=(w_px / TARGET_DPI, h_px / TARGET_DPI), dpi=TARGET_DPI)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(minx, maxx)
ax.set_ylim(miny, maxy)
ax.set_facecolor("white")
ax.set_aspect("equal")
ax.axis("off")

# 整岛单一纯色填充（关闭抗锯齿，防止杂色边缘）
for i, row in counties.iterrows():
    polys = list(row.geometry.geoms) if row.geometry.geom_type == "MultiPolygon" else [row.geometry]
    for g0 in polys:
        ax.add_patch(plt.Polygon(
            list(g0.exterior.coords), color=ISLAND_FILL, closed=True,
            edgecolor="none", antialiased=False))
        for interior in g0.interiors:
            ax.add_patch(plt.Polygon(
                list(interior.coords), facecolor="white", edgecolor="none",
                closed=True, antialiased=False))

# 县界线（合并共享边，只描一次）：黑色单一线条
if counties.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
    boundary_line = unary_union(counties.geometry.boundary.tolist())
    gpd.GeoSeries([boundary_line], crs=TARGET_CRS).plot(
        ax=ax, color=LINE_COLOR, linewidth=LINE_WIDTH_PT, antialiased=False)

out_file = os.path.join(out_dir, "Hainan_county_color.png")
plt.savefig(out_file, format="png", dpi=TARGET_DPI, pad_inches=0,
            bbox_inches=None, facecolor="white")
plt.close(fig)
print(f"已保存 {out_file}")
print("\n✅ 县市纯色地图生成完毕")