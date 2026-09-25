# -*- coding: utf-8 -*-
"""诊断6：偏移段逐段输出 + 位置聚类 + 典型位置放大图（只读）"""
import geopandas as gpd
import shapely
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely import STRtree
from collections import defaultdict

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"
STEP = 10.0
OFF_LO, OFF_HI = 25.0, 100.0
MIN_RUN = 60.0

gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
mask = gdf["CITY"].astype(str).str.contains("三亚", na=False)
sanya = gdf[mask].reset_index(drop=True)
names = sanya["TOWN"].tolist()
polys = list(sanya.geometry)
n = len(polys)
borders = [g.boundary for g in polys]

adj_pairs = []
for i in range(n):
    for j in range(i + 1, n):
        inter = borders[i].intersection(borders[j])
        if inter.length > 50:
            adj_pairs.append((i, j))

def all_runs(bi, bj):
    L = bi.length
    ds = np.arange(0, L, STEP)
    pts = [bi.interpolate(d) for d in ds]
    dists = shapely.distance(np.array(pts, dtype=object), bj)
    runs = []
    k = 0
    while k < len(dists):
        if OFF_LO <= dists[k] <= OFF_HI:
            k0 = k
            while k < len(dists) and OFF_LO <= dists[k] <= OFF_HI:
                k += 1
            run_len = (k - k0) * STEP
            if run_len >= MIN_RUN:
                m = ds[(k0 + k - 1) // 2]
                runs.append((run_len, bi.interpolate(m)))
        else:
            k += 1
    return runs

# 收集所有偏移段
events = []  # (pairkey, len, point)
for i, j in adj_pairs:
    for rl, p in all_runs(borders[i], borders[j]):
        events.append((f"{names[i]}↔{names[j]}", rl, p))

# 按位置聚类（250m 内同簇）
clusters = []  # [point, [events]]
for key, rl, p in events:
    hit = None
    for c in clusters:
        if c[0].distance(p) < 250:
            hit = c
            break
    if hit is None:
        clusters.append([p, [(key, rl, p)]])
    else:
        hit[1].append((key, rl, p))

print(f"偏移段总数 {len(events)}，位置聚类后 {len(clusters)} 处\n")
cl_sorted = sorted(clusters, key=lambda c: -sum(e[1] for e in c[1]))
for p, evs in cl_sorted:
    tot = sum(e[1] for e in evs)
    keys = sorted({e[0] for e in evs})
    print(f"({p.x:7.0f},{p.y:7.0f}) 段数{len(evs):2d} 总长{tot:5.0f}m  {keys[:4]}")

# 渲染最大簇 + 一个中等簇
def render(center, fname, half=600):
    cx, cy = center.x, center.y
    fig, ax = plt.subplots(figsize=(10, 9), dpi=110)
    colors = plt.cm.tab10(range(n))
    for i in range(n):
        if polys[i].distance(center) < 3000:
            gpd.GeoSeries([polys[i]], crs=gdf.crs).plot(
                ax=ax, facecolor=colors[i % 10] + (0.15,),
                edgecolor=colors[i % 10], linewidth=1.2)
            b = polys[i].bounds
            gpd.GeoSeries([polys[i]].__class__(polys[i].representative_point()), crs=gdf.crs).plot(
                ax=ax, color=colors[i % 10], marker="$" + names[i] + "$", markersize=9)
    ax.set_xlim(cx - half, cx + half)
    ax.set_ylim(cy - half, cy + half)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    ax.plot(cx, cy, "r+", markersize=14)
    fig.savefig(fname, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存 {fname}")

render(cl_sorted[0][0], "diag_junction_1.png")
render(cl_sorted[len(cl_sorted) // 2][0], "diag_junction_2.png")
