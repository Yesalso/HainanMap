# -*- coding: utf-8 -*-
"""
诊断5：严谨检测三亚区域真偏移双线（30-80m）
方法：沿每个镇的边界每 10m 采样一个点，计算采样点到相邻镇边界的距离，
      距离落在 [25, 100]m 的连续段即为"偏移双线段"（真正画了两遍且错开）。
      同时检查用户数字 198,861 / 180,492 的可能来源。
只读，不改任何文件。
"""
import geopandas as gpd
import shapely
import numpy as np
from shapely.ops import unary_union
from shapely import STRtree

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"
GRID = 2.0
STEP = 10.0          # 采样步长 m
OFF_LO, OFF_HI = 25.0, 100.0
MIN_RUN = 60.0       # 偏移段最短长度 m

gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
mask = gdf["CITY"].astype(str).str.contains("三亚", na=False)
sanya = gdf[mask].reset_index(drop=True)
others = gdf[~mask].reset_index(drop=True)
names = sanya["TOWN"].tolist()
polys = list(sanya.geometry)

# ---- 用户数字来源排查 ----
print("=== 各镇边界长度 ===")
for i, g in enumerate(polys):
    L = g.boundary.length
    if 150_000 < L < 260_000 or i < 3:
        print(f"  {names[i]}: {L:,.0f} m")
s_impl = sanya.copy()
s_impl["geometry"] = sanya.geometry.simplify(5, preserve_topology=True)
per = sum(g.boundary.length for g in s_impl.geometry)
net = unary_union([g.boundary for g in s_impl.geometry])
print(f"三亚 simplify(5m) 后: 逐面总长 {per:,.0f} m | 线网 {net.length:,.0f} m "
      f"({(per-net.length)/per*100:.1f}%)")

# ---- 偏移双线检测 ----
# 邻接关系：共享边 >50m
n = len(polys)
borders = [g.boundary for g in polys]
adj_pairs = []
for i in range(n):
    for j in range(i + 1, n):
        inter = borders[i].intersection(borders[j])
        if inter.length > 50:
            adj_pairs.append((i, j, inter.length))
# 三亚镇与邻市
o_polys = list(others.geometry)
o_names = others["TOWN"].tolist()
o_borders = [g.boundary for g in o_polys]
otree = STRtree(o_borders)
ext_pairs = []
for i in range(n):
    for j in otree.query(borders[i]):
        j = int(j)
        inter = borders[i].intersection(o_borders[j])
        if inter.length > 50:
            ext_pairs.append((i, j, inter.length))

def offset_sections(bi, bj):
    """bi 上距离 bj 为 [25,100]m 的连续段，返回 [(len_m, midpoint)]"""
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
                mid = bi.interpolate(ds[(k0 + k - 1) // 2])
                runs.append((run_len, mid))
        else:
            k += 1
    return runs

print(f"\n=== 三亚内部镇对偏移双线检测（25-100m，段长≥{MIN_RUN:.0f}m） ===")
tot_in = 0.0
pairs_hit = 0
for i, j, sl in sorted(adj_pairs, key=lambda x: -x[2]):
    secs = offset_sections(borders[i], borders[j])
    secs += offset_sections(borders[j], borders[i])
    if secs:
        L = sum(s[0] for s in secs)
        tot_in += L
        pairs_hit += 1
        mid = max(secs, key=lambda s: s[0])[1]
        print(f"  {names[i]}↔{names[j]}(共享边{sl:,.0f}m): {len(secs)}段 共{L:,.0f}m "
              f"中心点≈({mid.x:.0f},{mid.y:.0f})")
print(f"内部镇对合计 {tot_in:,.0f} m，涉及 {pairs_hit} 对")

print(f"\n=== 三亚镇↔邻市偏移双线检测 ===")
tot_ex = 0.0
for i, j, sl in sorted(ext_pairs, key=lambda x: -x[2]):
    secs = offset_sections(borders[i], o_borders[j])
    secs += offset_sections(o_borders[j], borders[i])
    if secs:
        L = sum(s[0] for s in secs)
        tot_ex += L
        mid = max(secs, key=lambda s: s[0])[1]
        print(f"  {names[i]}↔{o_names[j]}(接边{sl:,.0f}m): {len(secs)}段 共{L:,.0f}m "
              f"中心点≈({mid.x:.0f},{mid.y:.0f})")
print(f"与邻市合计 {tot_ex:,.0f} m")
