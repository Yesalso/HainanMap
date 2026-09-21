import geopandas as gpd
import numpy as np

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"
g = gpd.read_file(BASE + r"\chengmai2002_v2.shp", encoding="utf-8").to_crs(32649)

names = {}
for _, r in g.iterrows():
    names.setdefault(r["TOWN"], []).append(r)

print(f"{'TOWN':<8}{'沿用code/area':>18}{'新增code/area':>18}{'共享边km':>10}{'间距m':>9}{'沿用部件':>9}")
for nm, rows in names.items():
    if len(rows) < 2:
        continue
    old = [r for r in rows if r["SOURCE"] == "沿用"]
    new = [r for r in rows if r["SOURCE"] == "2002新增"]
    if not old or not new:
        continue
    o = old[0].geometry
    n = nw = None
    for r in new:
        pass
    ngeom = new[0].geometry
    try:
        border = o.boundary.intersection(ngeom.buffer(1).boundary).length
    except Exception:
        border = 0
    dist = o.distance(ngeom)
    parts = len(list(o.geoms)) if o.geom_type == "MultiPolygon" else 1
    print(f"{nm:<8}{str(old[0]['CODE'])+'/'+format(old[0].geometry.area/1e6,'.2f'):>18}"
          f"{str(new[0]['CODE'])+'/'+format(new[0].geometry.area/1e6,'.2f'):>18}"
          f"{border/1000:10.1f}{dist:9.0f}{parts:9d}")