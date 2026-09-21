import geopandas as gpd
from pyproj import CRS

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"
albers_wkt = open(BASE + r"\chengmai2002_fixed_Albers.prj", encoding="utf-8").read().strip()
crs = CRS.from_wkt(albers_wkt)

g = gpd.read_file(BASE + r"\chengmai2002_snapref.shp", encoding="utf-8").to_crs(crs)
g.to_file(BASE + r"\chengmai2002_snapref_Albers.shp", encoding="utf-8")
print("已写出 _Albers，CRS:", g.crs)
print("要素数:", len(g), " 面积km2:", round(g.geometry.area.sum() / 1e6, 4))