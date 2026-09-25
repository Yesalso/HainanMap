# -*- coding: utf-8 -*-
"""根因定位：崖城/梅山、抱板/七叉、福报 的分隔线为何在节点化网络中缺失"""
import geopandas as gpd
import shapely
from shapely.ops import unary_union, polygonize
from shapely import STRtree

SRC = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_codefix.shp"
gdf = gpd.read_file(SRC, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
polys = list(gdf.geometry)

names = {"崖城镇": 51, "梅山镇": 56, "抱板乡": 180, "七叉镇": 265, "福报乡": 285}
for n, i in names.items():
    g = polys[i]
    print(f"\n=== {n} idx={i} type={g.geom_type} valid={g.is_valid} area={g.area/1e6:.2f} km²")
    b = g.boundary
    print(f"    boundary type={b.geom_type} length={b.length:,.0f} m")
    if b.geom_type == "MultiLineString":
        closed = sum(1 for l in b.geoms if l.is_closed)
        print(f"    子线 {len(b.geoms)} 条，闭合 {closed} 条")
    elif b.geom_type == "LineString":
        print(f"    闭合={b.is_closed}")
    # 检查边界是否被其他镇完全重复覆盖（相邻镇共享边吻合度）
    # 与相邻镇的共享情况
    tree = STRtree(polys)
    for j in tree.query(g, predicate="touches"):
        other = polys[j]
        shared = g.boundary.intersection(other.boundary).length
        if shared > 5000:
            print(f"    与 {gdf.loc[j,'TOWN']} 共享边 {shared:,.0f} m "
                  f"(自身边界 {shared/g.boundary.length*100:.1f}%)")

# 网络中是否包含崖城边界
net = unary_union([g.boundary for g in polys])
for n, i in names.items():
    b = polys[i].boundary
    covered = net.intersection(b).length
    print(f"\n{n}: 边界 {b.length:,.0f} m，在网络中出现 {covered:,.0f} m "
          f"({covered/b.length*100:.1f}%)")

# 崖城-梅山之间的分隔线到底存不存在：
a_, b_ = polys[51], polys[56]
print(f"\n崖城-梅山 距离: {a_.distance(b_):.1f} m, 相交: {a_.intersects(b_)}, 接触: {a_.touches(b_)}")
seg = a_.intersection(b_)
print(f"交集类型: {seg.geom_type if not seg.is_empty else '空'}, 长度 {seg.length:,.0f} m, 面积 {seg.area:,.0f}")
