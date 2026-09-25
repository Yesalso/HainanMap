# -*- coding: utf-8 -*-
"""诊断6：S 是否闭合环 / 凹口区域归属 / S端点连接什么。"""
import numpy as np, cv2, geopandas as gpd
from shapely.ops import unary_union, linemerge
from shapely.geometry import Point, LineString

SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"

g = gpd.read_file(SHP, encoding="utf-8")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy()
utm = s.to_crs("EPSG:32649")
T = {t: geom.buffer(0) for t, geom in zip(utm["TOWN"], utm.geometry)}
A, B = T["荔枝沟区"], T["牛岭乡"]
S = A.boundary.intersection(B.boundary)
merged = linemerge(S)
print(f"S linemerge 类型: {merged.geom_type}")
chains = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
for k, c in enumerate(chains):
    print(f"  链{k}: 长 {c.length:.0f} m, 端点 "
          f"{tuple(round(v) for v in c.coords[0])} -> {tuple(round(v) for v in c.coords[-1])}, "
          f"闭合={c.is_ring}")

# S 各链端点处连接什么（300m 内其他边界）
def near_bounds(pt, r=600):
    names = sorted(T)
    out = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            sh = T[names[i]].boundary.intersection(T[names[j]].boundary)
            if sh.is_empty:
                continue
            dd = sh.distance(pt)
            if dd <= r:
                out.append((f"{names[i]}|{names[j]}", round(dd)))
    return sorted(out, key=lambda t: t[1])

for k, c in enumerate(chains):
    if c.is_ring:
        print(f"链{k} 闭合环")
        continue
    for tag, pt in [("头", Point(c.coords[0])), ("尾", Point(c.coords[-1]))]:
        print(f"链{k}{tag} {tuple(round(v) for v in pt.coords[0])}:")
        for b, dd in near_bounds(pt, 300):
            print(f"    {b:24s} {dd:5d} m")

# 凹口与蓝弧东端下方区域的归属
minx, miny, maxx, maxy = utm.total_bounds
pts_probe = {
    "blob中心": (339560, 2025614),
    "蓝弧e2下方": (347120, 2022600),
    "e2正南300m": (347120, 2022450),
    "e2西南500m": (346700, 2022450),
    "S东端(345980,2022016)附近": (345980, 2022016),
}
for nm, (x, y) in pts_probe.items():
    p = Point(x, y)
    owns = [t for t, geom in T.items() if geom.covers(p)]
    print(f"探测 {nm} {x,y}: {owns}")
