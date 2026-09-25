# -*- coding: utf-8 -*-
"""诊断8：对比 topo 与 v3 在坏节点处各要素最近顶点，定位吸附 bug"""
import geopandas as gpd
import shapely
import numpy as np

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
BAD = [(341919,2023467),(342003,2017010),(343289,2015368),(345665,2016389),
       (348544,2018292),(349056,2017333),(341820,2022617)]

def load(p):
    g = gpd.read_file(p, encoding="utf-8").to_crs("EPSG:32649")
    g["geometry"] = g.geometry.buffer(0)
    return g

a = load(BASE + r"\Hainan_town_topo.shp")
b = load(BASE + r"\Hainan_town_topo_v3.shp")

def verts(geom):
    out = []
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    for pt in parts:
        out.append(np.array(pt.exterior.coords))
        for r in pt.interiors:
            out.append(np.array(r.coords))
    return np.vstack(out)

va = {i: verts(g) for i, g in enumerate(a.geometry)}
vb = {i: verts(g) for i, g in enumerate(b.geometry)}

for cx, cy in BAD:
    c = shapely.Point(cx, cy)
    print(f"\n=== 簇 ({cx},{cy}) ===")
    for i in range(len(a)):
        if a.geometry[i].distance(c) > 800:
            continue
        da = np.hypot(va[i][:,0]-cx, va[i][:,1]-cy)
        ka = int(np.argmin(da))
        db = np.hypot(vb[i][:,0]-cx, vb[i][:,1]-cy)
        kb = int(np.argmin(db))
        if da[ka] < 250 or db[kb] < 250:
            nchg = int((va[i][:,0] != vb[i][:,0]).sum() + (va[i][:,1] != vb[i][:,1]).sum())
            print(f"  {a.iloc[i]['TOWN']}: 前最近顶点 {da[ka]:.0f}m {tuple(va[i][ka])} | "
                  f"后最近顶点 {db[kb]:.0f}m {tuple(vb[i][kb])} | 顶点改动数 {nchg}")
