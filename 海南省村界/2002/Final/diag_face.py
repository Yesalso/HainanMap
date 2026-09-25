# -*- coding: utf-8 -*-
"""解剖 441 km² 巨型面片：崖城/梅山两个闭合环为何没把面片分开"""
import geopandas as gpd
from shapely.ops import unary_union, polygonize
from shapely import STRtree

SRC = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_codefix.shp"
gdf = gpd.read_file(SRC, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
polys = list(gdf.geometry)

net = unary_union([g.boundary for g in polys])
lines = net.geoms if net.geom_type == "MultiLineString" else [net]
faces = [f for f in polygonize(lines) if f.area >= 1.0]

big = sorted(faces, key=lambda f: -f.area)
print(f"面片总数(面积>=1m²): {len(faces)}")
print(f">50 km² 的面片: {sum(1 for f in faces if f.area > 5e7)}")
print(f">10 km² 的面片: {sum(1 for f in faces if f.area > 1e7)}")

yc, ms = polys[51], polys[56]
for f in big[:3]:
    print(f"\n--- 面片 area={f.area/1e6:.2f} km², 边界长 {f.boundary.length:,.0f} m, "
          f"洞数 {len(f.interiors)}, valid={f.is_valid}")
    print(f"    包含崖城质心: {f.contains(yc.centroid)}, 包含梅山质心: {f.contains(ms.centroid)}")
    print(f"    崖城环在其边界中: {f.boundary.contains(yc.exterior) if yc.geom_type=='Polygon' else '?'}")
    print(f"    bounds={[round(v) for v in f.bounds]}")
    # 该面片边界由多少条网络线构成
    fl = f.boundary
    nsub = len(fl.geoms) if fl.geom_type == 'MultiLineString' else 1
    print(f"    边界子线数: {nsub}")

# 崖城与梅山边界的交叉点（环互相穿越的地方 = 分隔失效处）
inter = yc.boundary.intersection(ms.boundary)
print(f"\n崖城边界×梅山边界 交集: type={inter.geom_type}, length={inter.length:,.0f} m")
if inter.geom_type == "GeometryCollection":
    for k, part in enumerate(inter.geoms):
        print(f"    part{k}: {part.geom_type} len={part.length:,.0f} m bounds={[round(v) for v in part.bounds]}")
        if k > 10:
            break
elif inter.geom_type == "MultiLineString":
    for k, part in enumerate(inter.geoms[:10]):
        print(f"    part{k}: len={part.length:,.0f} m bounds={[round(v) for v in part.bounds]}")

# 两个镇内部是否互相越界（内环重叠面积）
ia = yc.intersection(ms).area
print(f"\n崖城∩梅山 面积: {ia:,.0f} m²  (重叠则说明两镇互相压盖)")
# 用 0.5m 缓冲看两者真实关系
print(f"崖城.buffer(50m) ∩ 梅山 面积: {yc.buffer(50).intersection(ms).area/1e6:.3f} km²")
