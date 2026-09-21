import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely import snap, make_valid, segmentize

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"

def poly_parts(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if g.geom_type == "MultiPolygon":
        return list(g.geoms)
    out = []
    for sub in getattr(g, "geoms", []):
        out.extend(poly_parts(sub))
    return out

def raster_lines(gdf, h, w, x0, x1, y0, y1, thick=1):
    out = np.zeros((h, w), np.uint8)
    sx, sy = w / (x1 - x0), h / (y1 - y0)
    for g in gdf.geometry:
        for p in poly_parts(g):
            for ring in (list(p.interiors) + [p.exterior]):
                pts = np.array([((x - x0) * sx, (y1 - y) * sy) for x, y in ring.coords], np.int32)
                cv2.polylines(out, [pts], True, 255, thick)
    return out > 0

def main():
    ref = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    out = gpd.read_file(BASE + r"\chengmai2002_noclip.shp", encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = ref.total_bounds
    black = ((img[:, :, 0] < 100) & (img[:, :, 1] < 100) & (img[:, :, 2] < 100)).astype(np.uint8)
    dt_black = cv2.distanceTransform((black == 0).astype(np.uint8), cv2.DIST_L2, 5)
    e_ref = raster_lines(ref, h, w, x0, x1, y0, y1)
    dt_ref = cv2.distanceTransform((~e_ref).astype(np.uint8), cv2.DIST_L2, 5)

    ref_union = unary_union(list(ref.geometry)).buffer(0)
    ref_bnd = ref_union.boundary
    for tol in (20.0, 40.0, 60.0):
        dense = segmentize(ref_bnd, tol / 2.0)
        geoms = []
        for g in out.geometry:
            s = snap(g, dense, tol)
            if not s.is_valid:
                s = make_valid(s)
            geoms.append(s)
        tmp = gpd.GeoDataFrame(geometry=geoms, crs=32649)
        e = raster_lines(tmp, h, w, x0, x1, y0, y1)
        ys, xs = np.where(e)
        on = (dt_ref[ys, xs] <= 1).mean() * 100
        resid = ((dt_ref[ys, xs] <= 1) & (dt_black[ys, xs] > 5)).mean() * 100
        # 面积守恒
        da = (tmp.geometry.area.sum() - out.geometry.area.sum())
        print(f"tol={tol:4.0f}m 在SHP线 {on:5.1f}%  真残留 {resid:5.2f}%  面积差 {da:,.0f} m²  非法 {int((~tmp.geometry.is_valid).sum())}")

if __name__ == "__main__":
    main()