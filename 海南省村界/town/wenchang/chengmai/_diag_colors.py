# -*- coding: utf-8 -*-
import numpy as np, cv2, geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

CRS = "EPSG:32649"
SHP = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai\Hainan_town_chengmai.shp"
IMG = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai\Chengmai.png"

g = gpd.read_file(SHP, encoding="utf-8")
g = g.to_crs(CRS)
minx, miny, maxx, maxy = g.total_bounds
W, H = 2822, 3455
sx, sy = (maxx - minx) / W, (maxy - miny) / H

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)

colors = {
    "#3F48CC": "山口乡", "#22B14C": "金江镇", "#FFF200": "太平乡", "#C3C3C3": "石浮乡",
    "#00A2E8": "红岗乡", "#FF7F27": "文儒镇", "#7F7F7F": "福山镇", "#B5E61D": "老城镇",
    "#99D9EA": "白莲乡", "#7092BE": "新吴镇", "#C8BFE7": "美亭乡", "#E657E6": "永发镇",
}

def hex2bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))

def px2geo(i, j):
    return minx + (i + 0.5) * sx, maxy - (j + 0.5) * sy

outer = unary_union(list(g.geometry)).buffer(0)
print("current towns:", g.TOWN.tolist())
print()
for hx, nm in colors.items():
    bgr = np.array(hex2bgr(hx), np.int16)
    d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
    mask = (d <= 30).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
    for i in range(1, n):
        if int(st[i, cv2.CC_STAT_AREA]) < 1000:
            continue
        mm = (lab == i).astype(np.uint8) * 255
        cnts, _ = cv2.findContours(mm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cnts, key=cv2.contourArea)[:, 0, :]
        poly = Polygon([px2geo(int(p[0]), int(p[1])) for p in c]).buffer(0).simplify(20, preserve_topology=True)
        best = None
        bo = 0.0
        for idx, r in g.iterrows():
            o = poly.intersection(r.geometry).area
            if o > bo:
                bo, best = o, idx
        if best is None:
            print(f"{nm:<4} {hx} area={poly.area/1e6:7.2f}km2  parent=(None)")
            continue
        pt = g.loc[best, "TOWN"]
        ins = poly.intersection(g.loc[best, "geometry"]).area / 1e6
        code = g.loc[best, "CODE"]
        print(f"{nm:<4} {hx} area={poly.area/1e6:7.2f}km2  parent=({code}, {pt})  内含={ins:6.2f}km2")