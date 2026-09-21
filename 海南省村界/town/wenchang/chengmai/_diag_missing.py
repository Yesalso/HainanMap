import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely import make_valid

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"

def poly_parts(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    if g.geom_type == "MultiPolygon":
        return list(g.geoms)
    out = []
    for s in getattr(g, "geoms", []):
        out.extend(poly_parts(s))
    return out

def main():
    ref = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    out = gpd.read_file(BASE + r"\chengmai_region.shp", encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    H, W = img.shape[:2]
    minx, miny, maxx, maxy = ref.total_bounds
    gw, gh = maxx - minx, maxy - miny
    sx, sy = gw / W, gh / H

    def fill(gdf):
        m = np.zeros((H, W), np.uint8)
        for g in gdf.geometry:
            for p in poly_parts(g):
                ext = np.array([((x - minx) / gw * W, (maxy - y) / gh * H) for x, y in p.exterior.coords], np.int32)
                cv2.fillPoly(m, [ext], 255)
        return m > 0

    outer = unary_union(list(ref.geometry)).buffer(0)
    land = fill(gpd.GeoDataFrame(geometry=[outer], crs=ref.crs))
    cover = fill(out)
    miss = land & ~cover
    over = cover & ~land
    print(f"land {land.sum()}  cover {cover.sum()}  miss {miss.sum()}px = {miss.sum()*sx*sy/1e6:.4f} km²  over {over.sum()}px = {over.sum()*sx*sy/1e6:.4f} km²")
    n, lab, st, cen = cv2.connectedComponentsWithStats(miss.astype(np.uint8), 8)
    comps = sorted([(int(st[i, cv2.CC_STAT_AREA]), float(cen[i][0]), float(cen[i][1])) for i in range(1, n)], reverse=True)
    print("最大缺口组件 (px, cx, cy):")
    for a, cx, cy in comps[:10]:
        print(f"  {a:6d}px {a*sx*sy/1e6:.4f}km²  质心像素({cx:.0f},{cy:.0f})")
    # 缺口是否沿外边界
    edge = np.zeros((H, W), np.uint8)
    cv2.rectangle(edge, (0, 0), (W - 1, H - 1), 255, 3)
    print("贴图幅边界的缺口像素占比:", (miss & (edge > 0)).sum() / max(miss.sum(), 1))

if __name__ == "__main__":
    main()