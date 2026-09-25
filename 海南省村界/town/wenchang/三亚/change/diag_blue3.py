# -*- coding: utf-8 -*-
"""诊断2：共享边界结构 + 蓝线端点锚定情况。"""
import numpy as np, cv2, geopandas as gpd, os
from shapely.ops import unary_union
from shapely.geometry import LineString, Point, MultiLineString
import shapely

IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"

g = gpd.read_file(SHP, encoding="utf-8")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy()
utm = s.to_crs("EPSG:32649")
minx, miny, maxx, maxy = utm.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
print(f"UTM四至 {geo_w:.0f}x{geo_h:.0f} m; PNG {W}x{H}; "
      f"W*30={W*30:.0f} H*30={H*30:.0f}")

# 蓝线像素 -> UTM 坐标（配准：x=minx+i*30, y=maxy-j*30）
bgr = np.array([204, 72, 63], np.int16)
d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
bys, bxs = np.where(d <= 60)
pts_utm = [(minx + i * 30.0, maxy - j * 30.0) for i, j in zip(bxs, bys)]

# 最近邻链排序
import math
unused = list(range(len(pts_utm)))
chain = [unused.pop(0)]
while unused:
    last = pts_utm[chain[-1]]
    bi, bd = None, 1e18
    for k in unused:
        dd = (pts_utm[k][0]-last[0])**2 + (pts_utm[k][1]-last[1])**2
        if dd < bd:
            bd, bi = dd, k
    if bd > (90)**2:   # 断口>90m 视为不连续
        print(f"  ⚠ 链中断，间隔 {math.sqrt(bd):.0f} m（继续连接）")
    chain.append(bi); unused.remove(bi)
line_utm = LineString([pts_utm[k] for k in chain])
print(f"蓝线折线：{len(chain)} 点，长度 {line_utm.length:.0f} m")
line_utm_s = line_utm.simplify(15, preserve_topology=True)
print(f"简化后 {len(line_utm_s.coords)} 点，长度 {line_utm_s.length:.0f} m")
e1, e2 = Point(line_utm_s.coords[0]), Point(line_utm_s.coords[-1])

# 共享边界
A = utm[utm["TOWN"] == "荔枝沟区"].geometry.union_all().buffer(0)
B = utm[utm["TOWN"] == "牛岭乡"].geometry.union_all().buffer(0)
S = A.boundary.intersection(B.boundary)
print(f"\n共享边界类型: {S.geom_type}")
pieces = list(S.geoms) if S.geom_type == "MultiLineString" else [S]
for k, p in enumerate(pieces):
    print(f"  段{k}: 长 {p.length:.0f} m, 端点 "
          f"{[tuple(round(c) for c in p.coords[0]), tuple(round(c) for c in p.coords[-1])]}")
print(f"端点e1 {tuple(round(c) for c in e1.coords[0])} 到S距离: {e1.distance(S):.0f} m")
print(f"端点e2 {tuple(round(c) for c in e2.coords[0])} 到S距离: {e2.distance(S):.0f} m")
print(f"蓝线中段到S最大偏离: {line_utm_s.distance(S):.0f} m(最小), "
      f"S到蓝线最大距离: {S.distance(line_utm_s):.0f} m")

# S 各段到蓝线的距离（判断哪段被替换）
for k, p in enumerate(pieces):
    print(f"  段{k} 到蓝线距离: min={p.distance(line_utm_s):.0f} m")

# 蓝线与S的交点
inter = line_utm_s.intersection(S)
print(f"蓝线与S交点: {inter.geom_type}, {sum(1 for _ in (inter.geoms if hasattr(inter,'geoms') else [inter]))} 个")
if hasattr(inter, "geoms"):
    for p in inter.geoms:
        print(f"   {tuple(round(c) for c in p.coords[0])}")
