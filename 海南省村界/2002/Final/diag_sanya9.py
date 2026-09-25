# -*- coding: utf-8 -*-
"""诊断9：单个坏簇的全要素最近顶点对比"""
import geopandas as gpd
import shapely
import numpy as np

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"

def load(p):
    g = gpd.read_file(p, encoding="utf-8").to_crs("EPSG:32649")
    g["geometry"] = g.geometry.buffer(0)
    return g

a = load(BASE + r"\Hainan_town_topo.shp")
b = load(BASE + r"\Hainan_town_topo_v3.shp")
names = a["TOWN"].tolist()

def verts(geom):
    out = []
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    for pt in parts:
        out.append(np.array(pt.exterior.coords))
        for r in pt.interiors:
            out.append(np.array(r.coords))
    return np.vstack(out)

va = [verts(g) for g in a.geometry]
vb = [verts(g) for g in b.geometry]

for cx, cy in [(342003, 2017010), (341919, 2023467)]:
    c = shapely.Point(cx, cy)
    print(f"\n=== 簇 ({cx},{cy}) 全要素最近顶点（<1000m 内有边界的） ===")
    for i in range(len(a)):
        if a.geometry[i].distance(c) > 1000:
            continue
        da = np.hypot(va[i][:, 0] - cx, va[i][:, 1] - cy)
        ka = int(np.argmin(da))
        db = np.hypot(vb[i][:, 0] - cx, vb[i][:, 1] - cy)
        kb = int(np.argmin(db))
        mark = "  <-- 变化" if abs(da[ka] - db[kb]) > 5 else ""
        print(f"  {names[i]:6s} 前 {da[ka]:6.0f}m {tuple(np.round(va[i][ka]))} | "
              f"后 {db[kb]:6.0f}m {tuple(np.round(vb[i][kb]))}{mark}")
