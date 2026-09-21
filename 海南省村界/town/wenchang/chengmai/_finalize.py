import geopandas as gpd
from shapely import make_valid
from pyproj import CRS

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"

g = gpd.read_file(BASE + r"\chengmai2002_noclip_g20.shp", encoding="utf-8")
g["geometry"] = g.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
merged = g.dissolve(by="CODE", aggfunc="first").reset_index()
merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=g.crs)
merged["AREA_KM2"] = (merged.geometry.area / 1e6).round(4)
merged["面积km2"] = merged["AREA_KM2"]

out = BASE + r"\chengmai2002_final.shp"
merged.to_file(out, encoding="utf-8")
merged.to_file(BASE + r"\chengmai2002_final.geojson", driver="GeoJSON", encoding="utf-8")

albers_wkt = open(BASE + r"\chengmai2002_fixed_Albers.prj", encoding="utf-8").read().strip()
merged.to_crs(CRS.from_wkt(albers_wkt)).to_file(BASE + r"\chengmai2002_final_Albers.shp", encoding="utf-8")

print(f"要素 {len(merged)}  总面积 {merged.geometry.area.sum()/1e6:.4f} km²  非法 {int((~merged.geometry.is_valid).sum())}")
print(merged[["CODE", "TOWN", "SOURCE", "AREA_KM2"]].to_string(index=False))