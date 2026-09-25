# -*- coding: utf-8 -*-
"""诊断10：精确量化关键簇的连接性（顶点到各参与镇边界的距离）"""
import geopandas as gpd
import shapely
import numpy as np

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
gdf = gpd.read_file(BASE + r"\Hainan_town_topo.shp", encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
names = gdf["TOWN"].tolist()

CASES = [
    (342003, 2017010, ["河西区", "河东区", "牛岭乡"]),
    (301714, 2036588, ["崖城镇", "梅山镇", "保港镇"]),
    (342945, 2016191, ["南海区", "河东区", "鹿回头区"]),
    (367234, 2017674, ["林旺镇", "田独镇"]),
]
G = {n: gdf[gdf["TOWN"] == n].geometry.iloc[0] for n in set(sum([c[2] for c in CASES], []))}
B = {n: G[n].boundary for n in G}

def tip(geom, c):
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    best, bv = None, 1e18
    for pt in parts:
        v = np.array(pt.exterior.coords)
        d = np.hypot(v[:, 0] - c.x, v[:, 1] - c.y)
        k = int(np.argmin(d))
        if d[k] < bv:
            bv, best = d[k], (v[k][0], v[k][1])
    return best, bv

for cx, cy, towns in CASES:
    c = shapely.Point(cx, cy)
    print(f"\n=== ({cx},{cy}) {towns} ===")
    for n in towns:
        v, dv = tip(G[n], c)
        line = f"  {n}: 最近顶点({v[0]:.0f},{v[1]:.0f}) 距簇心{dv:.0f}m"
        for m in towns:
            if m != n:
                line += f" | 到{m}边界线 {B[m].distance(shapely.Point(v)):.1f}m"
        print(line)
