# -*- coding: utf-8 -*-
"""诊断：重复描边率 + 偏移线对定位（只读，不改任何文件）"""
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Polygon
from shapely import STRtree

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_codefix.shp"

gdf = gpd.read_file(SHP, encoding="utf-8")
gdf = gdf.to_crs("EPSG:32649")
print(f"要素数: {len(gdf)}, 列: {gdf.columns.tolist()}")
gdf["geometry"] = gdf.geometry.buffer(0)

# 1) 边界总长 vs 唯一线网
per_poly_len = sum(g.boundary.length for g in gdf.geometry)
network = unary_union([g.boundary for g in gdf.geometry])
print(f"逐多边形边界总长: {per_poly_len:,.0f} m")
print(f"唯一线网长度:     {network.length:,.0f} m")

# 2) polygonize 后的碎屑面（偏移双线之间的窄带）
from shapely.ops import polygonize
faces = list(polygonize(network.geoms if hasattr(network, "geoms") else [network]))
print(f"polygonize 面片数: {len(faces)}（若拓扑干净应≈要素数 {len(gdf)}）")

# 3) 定位窄长碎屑面：面积小、细长（对应 30-80m 偏移带）
slivers = []
for f in faces:
    bl = f.boundary.length
    area = f.area
    # 细长度 = 面积 / (周长/2)^2，接近 1 为圆胖，越小越细长
    elong = area / (bl/2)**2 if bl > 0 else 1
    if area < 200_000:  # <0.2 km²
        slivers.append((area, elong, f))

print(f"\n疑似碎屑面（<0.2 km²）: {len(slivers)} 个")
for area, elong, f in sorted(slivers, key=lambda x: x[0])[:40]:
    print(f"  area={area:>10,.0f} m²  elong={elong:.3f}  bounds={[round(v) for v in f.bounds]}")

# 4) 乡镇覆盖检查：有多少面片落在两个以上原多边形内（归属模糊 = 偏移带）
polys = list(gdf.geometry)
tree = STRtree(polys)
ambiguous = 0
for f in faces:
    idx = tree.query(f.representative_point())
    if len(idx) == 0:
        continue
    hits = [i for i in idx if polys[i].intersects(f) and polys[i].intersection(f).area > 1]
    if len(hits) > 1:
        # 计算与各乡镇重叠面积占比
        fracs = sorted((polys[i].intersection(f).area / f.area, i) for i in hits)
        if fracs[-1][0] < 0.99:  # 最大重叠都盖不满 → 真偏移带
            ambiguous += 1
print(f"\n归属模糊面片（被多个乡镇部分覆盖）: {ambiguous} 个")
