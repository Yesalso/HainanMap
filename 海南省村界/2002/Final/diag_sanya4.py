# -*- coding: utf-8 -*-
"""诊断4：放大渲染窄带区域 + 查窄带周边的非三亚邻居是谁"""
import geopandas as gpd
import shapely
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from shapely.ops import unary_union, polygonize
from shapely import STRtree

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"
GRID = 2.0

gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
sanya = gdf[gdf["CITY"].astype(str).str.contains("三亚", na=False)].reset_index(drop=True)
others = gdf[~gdf["CITY"].astype(str).str.contains("三亚", na=False)]
names = sanya["TOWN"].tolist()
polys = list(sanya.geometry)

net = shapely.set_precision(unary_union([g.boundary for g in polys]), GRID)
net = unary_union([g for g in (net.geoms if net.geom_type == "MultiLineString" else [net]) if g.length > 0])
lines = net.geoms if net.geom_type == "MultiLineString" else [net]
faces = [f for f in polygonize(lines) if f.area >= 1.0]
tree = STRtree(polys)

def band_width(f):
    return 2.0 * f.area / f.boundary.length

# 收集所有可疑窄带（宽 20-500m、面积<1e6、被单镇覆盖）
slivers = []
for f in faces:
    w = band_width(f)
    if not (15.0 <= w <= 500.0 and f.area < 1e6):
        continue
    rp = f.representative_point()
    own = [int(i) for i in tree.query(rp) if polys[i].contains(rp)]
    if len(own) == 1:
        slivers.append((names[own[0]], f))
print(f"三亚单镇覆盖的窄带面片: {len(slivers)} 个")
from collections import Counter
print(Counter(n for n, _ in slivers))

# 窄带周边 100m 环内的非三亚邻居
other_polys = list(others.geometry)
other_names = others["TOWN"].tolist()
otree = STRtree(other_polys)
touch = Counter()
for n, f in slivers:
    for j in otree.query(f.buffer(100.0)):
        j = int(j)
        if other_polys[j].intersection(f.buffer(20.0)).length > 5:
            touch[(n, other_names[j])] += 1
print("\n窄带-非三亚邻居接触:")
for (n, o), c in touch.most_common(20):
    print(f"  {n} ↔ {o}: {c} 处")

# ---- 放大渲染天涯簇 ----
ty = sanya[sanya["TOWN"] == "天涯镇"]
ty_slivers = [f for n, f in slivers if n == "天涯镇"]
if ty_slivers:
    region = unary_union(ty_slivers).buffer(800)
    minx, miny, maxx, maxy = region.bounds
    fig, ax = plt.subplots(figsize=(12, 10), dpi=110)
    others.plot(ax=ax, facecolor="#f0f0f0", edgecolor="#888888", linewidth=0.8)
    sanya.plot(ax=ax, facecolor="#e8f4e8", edgecolor="black", linewidth=1.2)
    gpd.GeoSeries(ty_slivers, crs=sanya.crs).plot(ax=ax, facecolor="red", edgecolor="darkred", linewidth=0.8, alpha=0.8)
    ty.plot(ax=ax, facecolor="none", edgecolor="blue", linewidth=1.5)
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect("equal"); ax.set_title("天涯镇窄带部件（红色）与周边关系")
    ax.grid(alpha=0.3)
    fig.savefig("diag_sanya_zoom.png", bbox_inches="tight")
    plt.close(fig)
    print("\n已保存 diag_sanya_zoom.png")
