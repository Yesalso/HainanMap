import numpy as np
import cv2
import geopandas as gpd

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"

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
    out = gpd.read_file(BASE + r"\chengmai2002_snapref.shp", encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = ref.total_bounds
    sx, sy = w / (x1 - x0), h / (y1 - y0)

    e_ref = raster_lines(ref, h, w, x0, x1, y0, y1)
    dt_ref = cv2.distanceTransform((~e_ref).astype(np.uint8), cv2.DIST_L2, 5)

    print(f"{'CODE':>10} {'TOWN':<6} {'边界km':>7} {'落在SHP线(<=1px)%':>16} {'中位距px':>8}")
    for _, r in out.iterrows():
        e = raster_lines(gpd.GeoDataFrame(geometry=[r.geometry], crs=out.crs), h, w, x0, x1, y0, y1)
        ys, xs = np.where(e)
        if len(ys) == 0:
            continue
        d = dt_ref[ys, xs]
        pct = (d <= 1).sum() / len(d) * 100
        blen = r.geometry.boundary.length / 1000
        print(f"{r['CODE']:>10} {r['TOWN']:<6} {blen:7.1f} {pct:15.1f}% {np.median(d):8.1f}")

if __name__ == "__main__":
    main()