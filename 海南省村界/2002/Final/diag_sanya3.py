# -*- coding: utf-8 -*-
"""诊断3：解剖典型窄带面片——它到底夹在谁和谁之间？+ 放大图直看"""
import geopandas as gpd
import shapely
from shapely.ops import unary_union, polygonize
from shapely import STRtree

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"
GRID = 2.0

gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
sanya = gdf[gdf["CITY"].astype(str).str.contains("三亚", na=False)].reset_index(drop=True)
names = sanya["TOWN"].tolist()
polys = list(sanya.geometry)

net = shapely.set_precision(unary_union([g.boundary for g in polys]), GRID)
net = unary_union([g for g in (net.geoms if net.geom_type == "MultiLineString" else [net]) if g.length > 0])
lines = net.geoms if net.geom_type == "MultiLineString" else [net]
faces = [f for f in polygonize(lines) if f.area >= 1.0]
tree = STRtree(polys)

def owners_of(f):
    out = []
    for i in tree.query(f):
        i = int(i)
        ia = polys[i].intersection(f).area
        if ia > 0:
            out.append((ia / f.area, names[i]))
    return sorted(out, reverse=True)

# 挑几个典型：田独 0.88km²、天涯簇 323xxx、保港 54km²
targets = []
for f in faces:
    w = 2.0 * f.area / f.boundary.length
    if 20.0 <= w <= 500.0 and f.area < 1e6:
        b = f.bounds
        if (357000 < b[0] < 360000) or (323000 < b[0] < 325000) or (300000 < b[0] < 303000):
            targets.append(f)
print(f"候选解剖面片 {len(targets)} 个\n")

for f in targets[:8]:
    b = [round(v) for v in f.bounds]
    w = 2.0 * f.area / f.boundary.length
    print(f"面片 area={f.area:,.0f} 宽={w:.1f}m bounds={b}")
    print(f"  覆盖它的镇: {[(f'{p:.2f}', n) for p, n in owners_of(f)]}")
    # 面片四条长边两侧是谁：沿面片边界取中线法向采样
    # 简化：把面片向外缓冲 40m，看缓冲环主要落在哪些镇里
    ring = f.buffer(40.0).difference(f)
    print(f"  外扩40m环覆盖: {[(f'{p:.2f}', n) for p, n in owners_of(ring)]}")
    # 面片是否为某镇 polygon 的洞（interiors 检查）
    for p, n in owners_of(f)[:2]:
        i = names.index(n)
        g = polys[i]
        if g.geom_type == "MultiPolygon":
            for k, part in enumerate(g.geoms):
                if part.contains(f.representative_point()):
                    print(f"  {n}[{k}]: 洞数={len(part.interiors)}, "
                          f"面片在该部件内={part.contains(f)}")
        else:
            if g.contains(f.representative_point()):
                print(f"  {n}: 洞数={len(g.interiors)}, 面片在多边形内={g.contains(f)}, "
                      f"在洞内={any(shapely.Polygon(h).contains(f) for h in g.interiors)}")
    print()
