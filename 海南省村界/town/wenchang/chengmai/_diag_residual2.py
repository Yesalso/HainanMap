import numpy as np
import cv2
import geopandas as gpd
import sys

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"
OUT = sys.argv[1] if len(sys.argv) > 1 else r"\chengmai2002_snapref.shp"

def raster_lines(gdf, h, w, x0, x1, y0, y1, thick=1):
    out = np.zeros((h, w), np.uint8)
    sx, sy = w / (x1 - x0), h / (y1 - y0)
    for g in gdf.geometry:
        for p in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            for ring in (list(p.interiors) + [p.exterior]):
                pts = np.array([((x - x0) * sx, (y1 - y) * sy) for x, y in ring.coords], np.int32)
                cv2.polylines(out, [pts], True, 255, thick)
    return out > 0

def main():
    ref = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    out = gpd.read_file(BASE + OUT, encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = ref.total_bounds
    black = ((img[:, :, 0] < 100) & (img[:, :, 1] < 100) & (img[:, :, 2] < 100)).astype(np.uint8)
    dt_black = cv2.distanceTransform((black == 0).astype(np.uint8), cv2.DIST_L2, 5)
    e_ref = raster_lines(ref, h, w, x0, x1, y0, y1)
    dt_ref = cv2.distanceTransform((~e_ref).astype(np.uint8), cv2.DIST_L2, 5)

    print(f"{'CODE':>10} {'TOWN':<6} {'边px':>7} {'在SHP线%':>9} {'其中无黑线(残留)%':>16}")
    for _, r in out.iterrows():
        e = raster_lines(gpd.GeoDataFrame(geometry=[r.geometry], crs=out.crs), h, w, x0, x1, y0, y1)
        ys, xs = np.where(e)
        if len(ys) == 0:
            continue
        on_shp = dt_ref[ys, xs] <= 1
        no_black = dt_black[ys, xs] > 2
        resid = on_shp & no_black
        print(f"{r['CODE']:>10} {r['TOWN']:<6} {len(ys):7d} {on_shp.mean()*100:8.1f}% {resid.mean()*100:15.1f}%")

if __name__ == "__main__":
    main()