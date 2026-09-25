# -*- coding: utf-8 -*-
"""诊断：Hainan_town_topo.shp 中三亚市区域的重复描边与真偏移线对（只读）"""
import geopandas as gpd
import shapely
from shapely.ops import unary_union, polygonize
from shapely import STRtree

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"
GRID = 2.0

gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
sanya = gdf[gdf["CITY"].astype(str).str.contains("三亚", na=False)].reset_index(drop=True)
print(f"全岛要素 {len(gdf)}，三亚乡镇/街道 {len(sanya)} 个")
print("名称:", sanya["TOWN"].tolist())

polys = list(sanya.geometry)
per_poly = sum(g.boundary.length for g in polys)

# 2m 网格吸附后的线网：若两侧描边完全一致，union 会合并
net_raw = unary_union([g.boundary for g in polys])
net_snap = shapely.set_precision(net_raw, GRID)
net_snap = unary_union([g for g in (net_snap.geoms if net_snap.geom_type == "MultiLineString"
                                    else [net_snap]) if g.length > 0])
print(f"\n逐面边界总长: {per_poly:,.0f} m")
print(f"唯一线网(原始union): {net_raw.length:,.0f} m")
print(f"唯一线网(2m吸附后): {net_snap.length:,.0f} m  ← 吸附后仍不变短说明重复描边非亚米级")
dup = per_poly - net_snap.length
print(f"共享边长度(正常重复): {dup:,.0f} m ({dup/per_poly*100:.1f}%)")

# 面片化找偏移窄带
lines = net_snap.geoms if net_snap.geom_type == "MultiLineString" else [net_snap]
faces = [f for f in polygonize(lines) if f.area >= 1.0]
print(f"\n面片数: {len(faces)}（应≈{len(sanya)}）")

def width(f):
    bl = f.boundary.length
    return 2.0 * f.area / bl

tree = STRtree(polys)
offset_bands = []
for f in faces:
    w = width(f)
    if not (20.0 <= w <= 120.0):
        continue
    neigh = {int(i) for i in tree.query(f.buffer(5.0), predicate="touches")
             if polys[int(i)].intersection(f.buffer(1.0)).length > 1.0}
    if len(neigh) >= 2:
        offset_bands.append((w, f.area, f, sorted(sanya.iloc[i]["TOWN"] for i in neigh)))

print(f"\n两镇之间 20-120m 宽的偏移窄带: {len(offset_bands)} 条")
tot_band_area = 0
for w, a, f, towns in sorted(offset_bands, key=lambda x: -x[1]):
    tot_band_area += a
    print(f"  宽{w:5.1f}m 面积{a:>9,.0f}m² bounds={[round(v) for v in f.bounds]} 邻接{towns}")
print(f"窄带总面积: {tot_band_area:,.0f} m² ({tot_band_area/1e6:.3f} km²)")

# 相邻镇对级别的偏移长度统计
print("\n各邻接镇对的偏移窄带总长（沿带方向）:")
pair_stat = {}
for w, a, f, towns in offset_bands:
    key = "/".join(towns)
    pair_stat.setdefault(key, [0.0, 0.0])
    pair_stat[key][0] += f.length / 2  # 带长约等于每侧线长
    pair_stat[key][1] += a
for k, (l, a) in sorted(pair_stat.items(), key=lambda x: -x[1][0]):
    print(f"  {k}: 带长≈{l:,.0f} m, 面积 {a:,.0f} m²")
