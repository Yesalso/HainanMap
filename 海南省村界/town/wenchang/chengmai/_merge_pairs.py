import geopandas as gpd
from shapely.ops import unary_union
from shapely import make_valid

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"

g = gpd.read_file(BASE + r"\chengmai2002_v2.shp", encoding="utf-8")
g["geometry"] = g.geometry.apply(lambda x: x if x.is_valid else make_valid(x))

rows = []
for town, grp in g.groupby("TOWN", sort=False):
    if len(grp) == 1:
        rows.append(grp.iloc[0].to_dict())
        continue
    old = grp[grp["SOURCE"] == "沿用"]
    base = (old.iloc[0] if len(old) else grp.iloc[0]).to_dict()
    geom = unary_union(list(grp.geometry)).buffer(0)
    base["geometry"] = geom
    base["SOURCE"] = "沿用"
    base["AREA_KM2"] = round(geom.area / 1e6, 4)
    rows.append(base)

out = gpd.GeoDataFrame(rows, geometry="geometry", crs=g.crs)
out["面积km2"] = (out.geometry.area / 1e6).round(4)
out.to_file(BASE + r"\chengmai2002_v2_merged.shp", encoding="utf-8")
print(f"要素 {len(out)}  面积 {out.geometry.area.sum()/1e6:.4f}")
for _, r in out.iterrows():
    parts = list(r.geometry.geoms) if r.geometry.geom_type == "MultiPolygon" else [r.geometry]
    holes = sum(len(p.interiors) for p in parts)
    print(f"  {r['CODE']} {r['TOWN']:<8} {r.geometry.area/1e6:9.3f}km2 部件{len(parts):4d} 内环{holes:4d}")