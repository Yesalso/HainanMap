# -*- coding: utf-8 -*-
import geopandas as gpd
shp = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo_v3.shp"
g = gpd.read_file(shp, encoding="utf-8").to_crs("EPSG:32649")
g["geometry"] = g.geometry.buffer(0)
g["area_km2"] = g.geometry.area / 1e6
print("总要素:", len(g))
print(g["CITY"].value_counts().to_string())
print("\n--- 海口市 ---")
hk = g[g["CITY"]=="海口市"].sort_values("area_km2")
for _, r in hk.iterrows():
    print(f"{r['TOWN']}\t{r['area_km2']:.1f} km2\t约 {r['area_km2']*1e6/ (70.2**2):.0f} px2")
print("\n--- 三亚市 ---")
sy = g[g["CITY"]=="三亚市"].sort_values("area_km2")
for _, r in sy.iterrows():
    print(f"{r['TOWN']}\t{r['area_km2']:.1f} km2")
print("\n--- 全岛面积 <40km2 的乡镇 ---")
sm = g[g["area_km2"]<40].sort_values("area_km2")
for _, r in sm.iterrows():
    print(f"{r['CITY']} {r['TOWN']}\t{r['area_km2']:.1f} km2")
print("\n字段:", g.columns.tolist())
