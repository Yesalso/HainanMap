import json
import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"
CM = json.load(open(BASE + r"\color_map_chengmai.json", encoding="utf-8"))

def hex2bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))

def main():
    g = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    minx, miny, maxx, maxy = g.total_bounds
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    H, W = img.shape[:2]
    geo_w, geo_h = maxx - minx, maxy - miny
    sx, sy = geo_w / W, geo_h / H
    outer = unary_union(list(g.geometry)).buffer(0)

    def px2geo(i, j):
        return minx + (i + 0.5) * sx, maxy - (j + 0.5) * sy

    print(f"{'颜色':<9}{'名称':<7}{'面积km2':>8}  覆盖的现行乡镇(重叠km2)")
    for hx, spec in CM["colors"].items():
        names = spec if isinstance(spec, list) else [spec]
        bgr = np.array(hex2bgr(hx), np.int16)
        d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
        mask = (d <= 30).astype(np.uint8)
        n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            if int(st[i, cv2.CC_STAT_AREA]) < 1000:
                continue
            mm = (lab == i).astype(np.uint8) * 255
            cnts, _ = cv2.findContours(mm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cnts, key=cv2.contourArea)[:, 0, :]
            poly = Polygon([px2geo(int(p[0]), int(p[1])) for p in c]).buffer(0)
            poly = poly.simplify(20, preserve_topology=True)
            poly = poly.intersection(outer)
            ovs = []
            for _, r in g.iterrows():
                o = poly.intersection(r.geometry).area / 1e6
                if o > 0.01:
                    ovs.append(f"{r['TOWN']}({o:.1f})")
            print(f"{hx:<9}{names[0]:<7}{poly.area/1e6:8.2f}  " + " ".join(ovs))

if __name__ == "__main__":
    main()