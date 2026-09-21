import sys
import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union

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
    fn = sys.argv[1]
    ref = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    out = gpd.read_file(BASE + "\\" + fn, encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = ref.total_bounds
    black = ((img[:, :, 0] < 100) & (img[:, :, 1] < 100) & (img[:, :, 2] < 100)).astype(np.uint8)
    dt_black = cv2.distanceTransform((black == 0).astype(np.uint8), cv2.DIST_L2, 5)
    e_ref = raster_lines(ref, h, w, x0, x1, y0, y1)
    dt_ref = cv2.distanceTransform((~e_ref).astype(np.uint8), cv2.DIST_L2, 5)

    e = raster_lines(out, h, w, x0, x1, y0, y1)
    ys, xs = np.where(e)
    on_shp = dt_ref[ys, xs] <= 1
    print(f"文件: {fn}   要素 {len(out)}")
    print(f"  边界像素 {len(ys)}")
    print(f"  精确落在SHP线(0px): {(dt_ref[ys, xs] == 0).mean()*100:.1f}%")
    print(f"  在现行SHP线上(<=1px): {on_shp.mean()*100:.1f}%")
    for th in (2, 5, 10):
        resid = on_shp & (dt_black[ys, xs] > th)
        print(f"  真残留(在SHP线上 且 离黑线>{th}px={th*20}m): {resid.mean()*100:.2f}%")
    # 手绘贴合
    by, bx = np.where(black > 0)
    d = dt_ref[by, bx]
    e_out = e
    dt_out = cv2.distanceTransform((~e_out).astype(np.uint8), cv2.DIST_L2, 5)
    db = dt_out[by, bx]
    print(f"  手绘黑线->输出边界: <=1px {(db<=1).mean()*100:.2f}%  <=3px {(db<=3).mean()*100:.2f}%  平均{db.mean():.2f}px")
    uo = unary_union(list(out.geometry))
    ur = unary_union(list(ref.geometry))
    print(f"  输出∪面积 {uo.area/1e6:.4f} km²  与现行SHP对称差 {uo.symmetric_difference(ur).area/1e6:.4f} km²")

if __name__ == "__main__":
    main()