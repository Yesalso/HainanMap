# -*- coding: utf-8 -*-
"""嘉积镇边界异常定位 + 局部放大出图"""
from __future__ import annotations
import os
import numpy as np
import geopandas as gpd
import cv2
from shapely.ops import unary_union
from shapely.geometry import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_final.shp")
TOWN = "嘉积镇"
OUT = os.path.join(HERE, "jiaji_final_anomaly_zoom.png")
OUT_TXT = os.path.join(HERE, "jiaji_final_anomaly_list.txt")

log_lines = []
def log(m=""):
    print(m, flush=True)
    log_lines.append(str(m))

def parts_of(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]

def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    rows = g[g["TOWN"] == TOWN]
    jj = unary_union(list(rows.geometry)).buffer(0)
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    main_body = parts[0]
    fragments = parts[1:]

    log(f"主体面积 {main_body.area/1e6:.4f} km²")
    log(f"碎块数量 {len(fragments)}，碎块合计面积 {sum(f.area for f in fragments):.1f} m²")
    log("\n碎块明细（按面积降序，含中心坐标）:")
    for i, f in enumerate(sorted(fragments, key=lambda p: -p.area)[:20]):
        c = f.centroid
        log(f"  #{i:2d} 面积 {f.area:8.1f} m²  周长 {f.length:6.1f} m  "
            f"中心 X={c.x:.0f} Y={c.y:.0f}  长宽 {f.bounds[2]-f.bounds[0]:.0f}×{f.bounds[3]-f.bounds[1]:.0f} m")

    # 细窄区域（骨架宽度）
    CELL = 5.0
    minx, miny, maxx, maxy = jj.bounds
    W = int((maxx - minx) / CELL) + 2
    H = int((maxy - miny) / CELL) + 2
    mask = np.zeros((H, W), np.uint8)
    def to_px(ring):
        c = np.asarray(ring.coords, float)
        return np.stack([(c[:, 0] - minx) / CELL, (maxy - c[:, 1]) / CELL], 1).astype(np.int32)
    for p in parts:
        cv2.fillPoly(mask, [to_px(p.exterior)], 1)
        for h in p.interiors:
            cv2.fillPoly(mask, [to_px(h)], 0)
    dt = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 5) * CELL

    # ---- 多面板出图：全图 + 3 个异常区放大 ----
    def render(x0, x1, y0, y1, Wpx, Hpx, draw_frag=True):
        img = np.full((Hpx, Wpx, 3), 255, np.uint8)
        gw, gh = x1 - x0, y1 - y0
        def pts_of(geom):
            out = []
            for p in parts_of(geom):
                c = np.array(p.exterior.coords)
                pt = np.stack([(c[:, 0] - x0) / gw * Wpx, (y1 - c[:, 1]) / gh * Hpx], 1)
                if len(pt) >= 3:
                    out.append(pt.astype(np.int32))
            return out
        for _, r in g.iterrows():
            col = (0, 0, 230) if r["TOWN"] == TOWN else (120, 200, 120)
            for p in pts_of(r.geometry):
                cv2.polylines(img, [p], True, col, 2, cv2.LINE_AA)
        if draw_frag:
            for f in fragments:
                for p in pts_of(f):
                    cv2.polylines(img, [p], True, (255, 0, 180), 2, cv2.LINE_AA)
        return img

    # 全图
    P1 = render(minx, maxx, miny, maxy, 900, 900)

    # 放大区：左上尾巴、右上接口、底部
    panels = [
        ("左上尾巴", minx, minx + (maxx - minx) * 0.22, maxy - (maxy - miny) * 0.28, maxy),
        ("右上接口", maxx - (maxx - minx) * 0.35, maxx, maxy - (maxy - miny) * 0.35, maxy),
        ("底部/碎片", minx, minx + (maxx - minx) * 0.4, miny, miny + (maxy - miny) * 0.3),
    ]
    P2 = render(*panels[0][1:], 450, 450)
    P3 = render(*panels[1][1:], 450, 450)
    P4 = render(*panels[2][1:], 450, 450)

    top = np.hstack([P2, P3])
    bot = np.hstack([P4, np.full((450, 450, 3), 255, np.uint8)])
    right = np.vstack([top, bot])
    canvas = np.hstack([P1, right])
    cv2.putText(canvas, "FULL", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    for i, (name, *_ ) in enumerate(panels):
        pass
    cv2.imencode(".png", canvas)[1].tofile(OUT)
    log(f"\n出图：{OUT}")
    log("  红=嘉积镇主体  浅绿=邻镇  品红=嘉积镇碎块(细缝)")

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"清单：{OUT_TXT}")

if __name__ == "__main__":
    main()
