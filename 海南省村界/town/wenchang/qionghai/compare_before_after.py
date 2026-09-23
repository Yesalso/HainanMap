# -*- coding: utf-8 -*-
"""嘉积镇 简化前后对比图 + 剩余分块核查"""
from __future__ import annotations
import os
import numpy as np
import geopandas as gpd
import cv2
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "qionghai2002_final.shp")
B = os.path.join(HERE, "qionghai2002_simplified.shp")
TOWN = "嘉积镇"
OUT = os.path.join(HERE, "jiaji_before_after.png")

log_lines = []
def log(m=""):
    print(m, flush=True); log_lines.append(str(m))

def parts_of(g):
    if g is None or g.is_empty: return []
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]

def main():
    ga = gpd.read_file(A, encoding="utf-8")
    gb = gpd.read_file(B, encoding="utf-8")
    ja = unary_union(list(ga[ga.TOWN == TOWN].geometry))
    jb = unary_union(list(gb[gb.TOWN == TOWN].geometry))
    pa = sorted(parts_of(ja), key=lambda p: -p.area)
    pb = sorted(parts_of(jb), key=lambda p: -p.area)
    log(f"简化前：{len(pa)} 块，面积 {ja.area/1e6:.4f} km²")
    log(f"简化后：{len(pb)} 块，面积 {jb.area/1e6:.4f} km²")
    for i, p in enumerate(pb):
        c = p.centroid
        log(f"  后块{i}: 面积 {p.area/1e6:.6f} km² ({p.area:.0f} m²) 周长 {p.length:.0f} m "
            f"中心 X={c.x:.0f} Y={c.y:.0f}")

    minx, miny, maxx, maxy = ja.bounds
    jb_b = jb.bounds
    minx, miny = min(minx, jb_b[0]), min(miny, jb_b[1])
    maxx, maxy = max(maxx, jb_b[2]), max(maxy, jb_b[3])

    def render(geoms_all, town_geom, W, H, x0, x1, y0, y1):
        img = np.full((H, W, 3), 255, np.uint8)
        gw, gh = x1 - x0, y1 - y0
        for _, r in geoms_all.iterrows():
            col = (0, 0, 230) if r["TOWN"] == TOWN else (170, 210, 170)
            for p in parts_of(r.geometry):
                c = np.array(p.exterior.coords)
                pt = np.stack([(c[:, 0] - x0) / gw * W, (y1 - c[:, 1]) / gh * H], 1).astype(np.int32)
                if len(pt) >= 3:
                    cv2.polylines(img, [pt], True, col, 2, cv2.LINE_AA)
        return img

    W = H = 820
    left = render(ga, ja, W, H, minx, maxx, miny, maxy)
    right = render(gb, jb, W, H, minx, maxx, miny, maxy)
    cv2.putText(left, "BEFORE (final)", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.putText(right, "AFTER (simplified)", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.line(left, (W, 0), (W, H), (200, 200, 200), 2)
    canvas = np.hstack([left, right])
    cv2.imencode(".png", canvas)[1].tofile(OUT)
    log(f"\n出图：{OUT}  (左=简化前 右=简化后，红=嘉积镇)")

    with open(os.path.join(HERE, "jiaji_before_after.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

if __name__ == "__main__":
    main()
