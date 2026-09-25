# -*- coding: utf-8 -*-
"""诊断5：S 两端与蓝弧两端的边界网络锚定（局部拓扑）。"""
import numpy as np, geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Point, LineString

SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"

g = gpd.read_file(SHP, encoding="utf-8")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy()
utm = s.to_crs("EPSG:32649")
T = {t: geom.buffer(0) for t, geom in zip(utm["TOWN"], utm.geometry)}
A, B = T["荔枝沟区"], T["牛岭乡"]
U2 = unary_union([A, B]).buffer(0)
S = A.boundary.intersection(B.boundary)
segs = list(S.geoms) if S.geom_type == "MultiLineString" else [S]
segs = sorted(segs, key=lambda p: p.coords[0][0])
S_w, S_e = Point(segs[0].coords[0]), Point(segs[-1].coords[-1])

# 蓝弧（同前法）
import cv2
IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
minx, miny, maxx, maxy = utm.total_bounds
img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
bgr = np.array([204, 72, 63], np.int16)
d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
mask = cv2.dilate((d <= 90).astype(np.uint8), np.ones((3, 3), np.uint8), 1)
cols = {}
for j, i in zip(*np.where(mask > 0)):
    cols.setdefault(i, []).append(j)
xs_s = sorted(cols)
line = LineString([(minx + i * 30.0, maxy - np.mean(cols[i]) * 30.0) for i in xs_s]) \
    .simplify(10, preserve_topology=True)
e1, e2 = Point(line.coords[0]), Point(line.coords[-1])

# 各乡镇两两共享边界中，与给定点接近的
def near_bounds(pt, r=1200):
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

for nm, pt in [("S西端", S_w), ("S东端", S_e), ("蓝弧e1", e1), ("蓝弧e2", e2)]:
    print(f"\n{nm} {tuple(round(c) for c in pt.coords[0])}:")
    for b, dd in near_bounds(pt):
        print(f"   {b:24s} {dd:5d} m")
