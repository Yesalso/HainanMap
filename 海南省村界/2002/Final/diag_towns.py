# -*- coding: utf-8 -*-
"""诊断 5 个异常乡镇：抱板乡、福报乡、崖城镇、梅山镇、七叉镇 的重叠关系"""
import geopandas as gpd
from shapely import STRtree

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_codefix.shp"
gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)

targets = ["抱板乡", "福报乡", "崖城镇", "梅山镇", "七叉镇"]
polys = list(gdf.geometry)
tree = STRtree(polys)

for name in targets:
    rows = gdf[gdf["TOWN"].astype(str).str.contains(name)]
    for idx, row in rows.iterrows():
        g = row.geometry
        print(f"\n=== {row['TOWN']} (idx={idx}, CODE={row['CODE']}, N_FEAT={row.get('N_FEAT')}) "
              f"面积 {g.area/1e6:.2f} km², 部件数 {len(g.geoms) if g.geom_type=='MultiPolygon' else 1}")
        # 与哪些乡镇重叠
        cand = tree.query(g)
        for j in cand:
            if j == idx:
                continue
            ia = polys[j].intersection(g).area
            if ia > 1e5:  # >0.1 km²
                frac = ia / g.area
                print(f"    与 {gdf.loc[j,'TOWN']} (idx={j}) 重叠 {ia/1e6:.2f} km² "
                      f"(占自身 {frac*100:.1f}%, 占对方 {ia/polys[j].area*100:.1f}%)")

# 顺便：全图互相重叠 >1 km² 的乡镇对
print("\n=== 全图重叠 >1 km² 的乡镇对 ===")
pairs = []
for i in range(len(polys)):
    for j in tree.query(polys[i]):
        if j <= i:
            continue
        ia = polys[i].intersection(polys[j]).area
        if ia > 1e6:
            pairs.append((ia/1e6, gdf.loc[i,'TOWN'], gdf.loc[j,'TOWN']))
for ia, a, b in sorted(pairs, reverse=True):
    print(f"    {a} × {b}: {ia:.2f} km²")
print(f"共 {len(pairs)} 对")
