import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"

def rasterize_lines(gdfs, h, w, x0, x1, y0, y1, thick=2):
    """把一组要素的所有边界线栅格化为二值图。"""
    out = np.zeros((h, w), dtype=np.uint8)
    sx, sy = w / (x1 - x0), h / (y1 - y0)
    for gdf in gdfs:
        for g in gdf.geometry:
            for p in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
                for ring in (list(p.interiors) + [p.exterior]):
                    pts = np.array([((x - x0) * sx, (y1 - y) * sy)
                                    for x, y in ring.coords], dtype=np.int32)
                    cv2.polylines(out, [pts], True, 255, thickness=thick)
    return out

def run():
    ref = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    out = gpd.read_file(BASE + r"\chengmai2002_snapref.shp", encoding="utf-8").to_crs(32649)
    inp = gpd.read_file(BASE + r"\chengmai2002_fixed.shp", encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = ref.total_bounds

    black = ((img[:, :, 0] < 100) & (img[:, :, 1] < 100) & (img[:, :, 2] < 100)).astype(np.uint8) * 255

    e_out = rasterize_lines([out], h, w, x0, x1, y0, y1)
    e_ref = rasterize_lines([ref], h, w, x0, x1, y0, y1)
    e_inp = rasterize_lines([inp], h, w, x0, x1, y0, y1)

    dt_out = cv2.distanceTransform((e_out == 0).astype(np.uint8), cv2.DIST_L2, 5)
    dt_ref = cv2.distanceTransform((e_ref == 0).astype(np.uint8), cv2.DIST_L2, 5)
    dt_inp = cv2.distanceTransform((e_inp == 0).astype(np.uint8), cv2.DIST_L2, 5)

    ys, xs = np.where(black > 0)
    n_black = len(xs)
    print(f"手绘黑线像素: {n_black}")
    print("手绘黑线 -> snapref输出边界 贴合度:")
    for d in (1, 2, 3, 5):
        near = black[ys, xs] * (dt_out[ys, xs] <= d)
        print(f"   <= {d}px({d*20}m): {(near > 0).sum() / n_black * 100:7.3f}%")
    ds = dt_out[ys, xs]
    print(f"   平均 {ds.mean():.3f}px  中位 {np.median(ds):.3f}px")

    print("\nsnapref输出边界 -> 现行SHP边界 距离分布:")
    oy, ox = np.where(e_out > 0)
    dr = dt_ref[oy, ox]
    n = len(dr)
    for d in (0, 1, 2, 3, 5, 10, 20):
        print(f"   <= {d:2d}px: {(dr <= d).sum() / n * 100:7.2f}%")
    print(f"   平均 {dr.mean():.3f}px  中位 {np.median(dr):.3f}px")

    print("\nfixed输入边界 -> snapref输出边界 距离分布:")
    iy, ix = np.where(e_inp > 0)
    do = dt_out[iy, ix]
    n2 = len(do)
    for d in (0, 1, 2, 3, 5):
        print(f"   <= {d:2d}px: {(do <= d).sum() / n2 * 100:7.2f}%")
    print(f"   平均 {do.mean():.3f}px  中位 {np.median(do):.3f}px")

    uo = unary_union(list(out.geometry))
    ui = unary_union(list(inp.geometry))
    ur = unary_union(list(ref.geometry))
    print(f"\n输出-输入 对称差: {uo.symmetric_difference(ui).area:,.1f} m²")
    print(f"输出-现行SHP 对称差: {uo.symmetric_difference(ur).area:,.1f} m²")

if __name__ == "__main__":
    run()