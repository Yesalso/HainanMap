import sys
import numpy as np
import geopandas as gpd

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"
fn = sys.argv[1]

g = gpd.read_file(BASE + "\\" + fn, encoding="utf-8")
for _, r in g.iterrows():
    gm = r.geometry
    parts = list(gm.geoms) if gm.geom_type == "MultiPolygon" else [gm]
    if len(parts) <= 3:
        continue
    areas = sorted((p.area for p in parts), reverse=True)
    print(f"{r['CODE']} {r['TOWN']}  部件{len(parts)} 面积km2 {gm.area/1e6:.3f}")
    print("   最大5:", [round(a/1e6, 4) for a in areas[:5]])
    print("   最小5:", [round(a/1e6, 6) for a in areas[-5:]])
    print("   <0.05km2的部件数:", sum(1 for a in areas if a < 5e4))