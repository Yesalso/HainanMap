import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"

def poly_parts(g):
    if g is None or g.is_empty: return []
    if g.geom_type == "Polygon": return [g]
    if g.geom_type == "MultiPolygon": return list(g.geoms)
    out = []
    for s in getattr(g, "geoms", []): out.extend(poly_parts(s))
    return out

def main():
    ref = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    reg = gpd.read_file(BASE + r"\chengmai_region.shp", encoding="utf-8").to_crs(32649)
    v2 = gpd.read_file(BASE + r"\chengmai2002_v2.shp", encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    H, W = img.shape[:2]
    minx, miny, maxx, maxy = ref.total_bounds
    gw, gh = maxx - minx, maxy - miny

    def fill(gdf):
        m = np.zeros((H, W), np.int32)
        for i, g in enumerate(gdf.geometry, 1):
            for p in poly_parts(g):
                ext = np.array([((x - minx) / gw * W, (maxy - y) / gh * H) for x, y in p.exterior.coords], np.int32)
                cv2.fillPoly(m, [ext], i)
        return m

    reglab = fill(reg)
    regname = {i + 1: r["TOWN"] for i, (_, r) in enumerate(reg.iterrows())}
    v2lab = fill(v2)
    v2name = {i + 1: (r["TOWN"], r["SOURCE"]) for i, (_, r) in enumerate(v2.iterrows())}

    # 对 v2 的每个沿用残块，看被方案B哪个区域覆盖
    print("v2沿用残块 -> 方案B覆盖区域")
    for i, (_, r) in enumerate(v2.iterrows(), 1):
        if r["SOURCE"] != "沿用" or r["geometry"].area / 1e6 < 0.5:
            continue
        m = (v2lab == i)
        if m.sum() == 0: continue
        vals, counts = np.unique(reglab[m], return_counts=True)
        order = np.argsort(-counts)
        top = ", ".join(f"{regname.get(int(vals[k]),'?')}({counts[k]}px)" for k in order[:4])
        print(f"  {r['CODE']} {r['TOWN']} {r['geometry'].area/1e6:.2f}km² -> {top}")

if __name__ == "__main__":
    main()