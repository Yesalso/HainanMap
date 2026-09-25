# -*- coding: utf-8 -*-
"""解剖崖城-梅山共享边界的局部线形：重合段之间到底怎么分叉的"""
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import box, LineString

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
a = gpd.read_file(BASE + r"\Hainan_town_codefix.shp", encoding="utf-8").to_crs("EPSG:32649")
a["geometry"] = a.geometry.buffer(0)
polys = list(a.geometry)
yc, ms = polys[51], polys[56]   # 崖城, 梅山

net = unary_union([g.boundary for g in polys])
lines = list(net.geoms) if net.geom_type == "MultiLineString" else [net]

# 取 301400-302300, 2036300-2037600 窗口内的所有网络线
bb = box(301400, 2036300, 302300, 2037600)
local = [l for l in lines if bb.intersects(l) and l.intersection(bb).length > 5]
print(f"窗口内网络线 {len(local)} 条：")

def owner_of(l):
    """这条网络线属于谁的边界（可能两家共享=重合段）"""
    owners = []
    for i in (51, 56):
        b = polys[i].boundary
        if b.contains(l) or b.intersection(l).length > 0.9 * l.length:
            owners.append(i)
    return ["崖城" if i == 51 else "梅山" for i in owners]

for l in sorted(local, key=lambda x: x.bounds[1]):
    inter = l.intersection(bb)
    seg = inter if inter.geom_type == "LineString" else l
    c = seg.coords
    own = owner_of(l)
    print(f"  len={l.length:7.1f}m own={own} "
          f"start=({c[0][0]:.0f},{c[0][1]:.0f}) end=({c[-1][0]:.0f},{c[-1][1]:.0f}) "
          f"闭合={l.is_closed}")

# 崖城环、梅山环各自在该窗口内的顶点序列（找分叉点）
def ring_coords_in(bb, ring):
    pts = [p for p in ring.coords if bb.covers(gpd.GeoSeries([p], crs=a.crs).geometry[0])]
    return pts

import shapely
from shapely.geometry import Point
for name, idx in [("崖城", 51), ("梅山", 56)]:
    ring = polys[idx].exterior if polys[idx].geom_type == "Polygon" else None
    parts = list(polys[idx].boundary.geoms) if polys[idx].geom_type != "Polygon" else [ring]
    for pi, part in enumerate(parts):
        pts = [p for p in part.coords if bb.buffer(200).covers(Point(p))]
        if len(pts) > 2:
            print(f"\n{name} ring#{pi} 窗口内顶点 {len(pts)} 个（沿环顺序）:")
            for p in pts[:40]:
                print(f"    ({p[0]:.1f}, {p[1]:.1f})")
