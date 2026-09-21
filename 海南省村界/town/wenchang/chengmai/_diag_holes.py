import sys
import geopandas as gpd

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"
fn = sys.argv[1]

g = gpd.read_file(BASE + "\\" + fn, encoding="utf-8")
print(f"=== {fn}  要素 {len(g)} ===")
print(f"{'CODE':>10} {'TOWN':<8}{'AREA_KM2':>10}{'部件':>5}{'内环':>5}")
tot_holes = 0
for _, r in g.iterrows():
    gm = r.geometry
    parts = list(gm.geoms) if gm.geom_type == "MultiPolygon" else [gm]
    holes = sum(len(p.interiors) for p in parts)
    tot_holes += holes
    if holes > 0 or len(parts) > 1:
        print(f"{r['CODE']:>10} {r['TOWN']:<8}{gm.area/1e6:10.4f}{len(parts):5d}{holes:5d}")
print("总内环:", tot_holes)