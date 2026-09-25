# -*- coding: utf-8 -*-
"""诊断2：解剖三亚 63 个面片——找出多余面片的真实形态与归属（只读）"""
import geopandas as gpd
import shapely
from shapely.ops import unary_union, polygonize
from shapely import STRtree

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"
GRID = 2.0

gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
sanya = gdf[gdf["CITY"].astype(str).str.contains("三亚", na=False)].reset_index(drop=True)
polys = list(sanya.geometry)
names = sanya["TOWN"].tolist()

net = shapely.set_precision(unary_union([g.boundary for g in polys]), GRID)
net = unary_union([g for g in (net.geoms if net.geom_type == "MultiLineString" else [net]) if g.length > 0])
lines = net.geoms if net.geom_type == "MultiLineString" else [net]
faces = [f for f in polygonize(lines) if f.area >= 1.0]
print(f"面片 {len(faces)} 个")

tree = STRtree(polys)
# 每个面片：面积、有效宽度、被哪些镇代表点包含、与各镇重叠
town_like = 0
extras = []
for f in faces:
    rp = f.representative_point()
    owners = [int(i) for i in tree.query(rp) if polys[i].contains(rp)]
    w = 2.0 * f.area / f.boundary.length
    # 与最大重叠镇的重叠占比
    best_frac = 0.0
    best_i = -1
    for i in tree.query(f):
        i = int(i)
        ia = polys[i].intersection(f).area
        if ia > 0 and ia / f.area > best_frac:
            best_frac, best_i = ia / f.area, i
    tag = "正常" if (len(owners) == 1 and best_frac > 0.95 and f.area > 1e6) else "多余?"
    if tag == "正常":
        town_like += 1
    else:
        extras.append((f.area, w, best_frac, best_i, owners, f))
print(f"近似正常镇面 {town_like} 个，可疑多余面 {len(extras)} 个\n")

for a, w, frac, bi, owners, f in sorted(extras, key=lambda x: -x[0])[:50]:
    owner_names = [names[i] for i in owners]
    best_name = names[bi] if bi >= 0 else "?"
    print(f"area={a:>12,.0f}m² 宽={w:6.1f}m 最大重叠={frac:.2f}({best_name}) "
          f"代表点归属={owner_names or '无主'} bounds={[round(v) for v in f.bounds]}")
