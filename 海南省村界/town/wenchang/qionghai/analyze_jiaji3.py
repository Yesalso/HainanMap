# -*- coding: utf-8 -*-
"""analyze_jiaji3.py —— 细带/碎片的矢量归属判定 + 贴邻乡镇"""
from __future__ import annotations
import os, math
import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_final.shp")
CELL = 10.0
R = 30.0

def parts_of(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]

def log(m=""):
    print(m, flush=True)

def neighbor_names(geom, gdf, town, tol=1.0):
    b = geom.boundary.buffer(tol)
    hits = []
    for _, r in gdf.iterrows():
        if r["TOWN"] == town:
            continue
        try:
            if b.intersects(r.geometry.boundary.buffer(tol)):
                hits.append(r["TOWN"])
        except Exception:
            pass
    return hits

def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    jj = unary_union(list(g[g["TOWN"] == "嘉积镇"].geometry))
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    main0 = parts[0]
    own = jj.area

    bnd = main0.bounds
    minx, miny, maxx, maxy = bnd
    W = int((maxx - minx) / CELL) + 1
    H = int((maxy - miny) / CELL) + 1
    mask = np.zeros((H, W), np.uint8)
    def to_px(xs, ys):
        xs = np.asarray(xs, float); ys = np.asarray(ys, float)
        return np.stack([(xs - minx) / CELL, (maxy - ys) / CELL], 1).astype(np.int32)
    for p in parts_of(jj):
        cv2.fillPoly(mask, [to_px(p.exterior.xy[0], p.exterior.xy[1])], 1)
        for h in p.interiors:
            cv2.fillPoly(mask, [to_px(h.xy[0], h.xy[1])], 0)
    dt = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 3) * CELL
    thin = (mask == 1) & (dt < R)
    thick = dt >= R
    kd = cv2.dilate(thick.astype(np.uint8), np.ones((3, 3), np.uint8), 1) > 0
    tongue = thin & (~kd)
    n, lab, st, _ = cv2.connectedComponentsWithStats(tongue.astype(np.uint8) * 255, 8)

    def cellgeom(ys, xs):
        return Polygon([
            (minx + xs * CELL, maxy - ys * CELL),
            (minx + (xs + 1) * CELL, maxy - ys * CELL),
            (minx + (xs + 1) * CELL, maxy - (ys + 1) * CELL),
            (minx + xs * CELL, maxy - (ys + 1) * CELL)])

    log("== 悬舌细带（不贴粗核心）矢量判定 ==")
    items = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA] * CELL * CELL
        if a < 500:
            continue
        ys, xs = np.nonzero(lab == i)
        geom = unary_union([cellgeom(int(y), int(x)) for y, x in zip(ys, xs)])
        items.append((a, geom))
    items.sort(key=lambda t: -t[0])
    for a, geom in items:
        gi = geom.intersection(jj)
        # 判定：是否与主块共边（=半岛/附着尾巴）还是独立多边形（=飞地）
        touch_main = geom.touches(main0) or geom.intersection(main0.boundary).length > 0
        iso = geom.intersection(main0).area / max(a, 1e-9)
        nb = neighbor_names(geom, g, "嘉积镇")
        kind = "独立飞地" if (iso < 0.02 and not touch_main) else ("主体附着的尾巴" if touch_main else "贴在主体上")
        if iso < 0.5 and touch_main is False:
            kind = "独立飞地(与主块仅共边或不相连)"
        elif touch_main:
            kind = "附着在主体上的尾巴"
        else:
            kind = "与主体重叠(含在体内)"
        length = geom.boundary.length / 2.0 if not geom.is_empty else 0
        L = max(length, 1e-6)
        log(f"  {a:7.0f} m²  长宽比{(geom.boundary.length/max(geom.envelope.length,1e-9)):.3f}  主块占比{iso:.3f}  类型:{kind.split('(')[0]}")
        log(f"      贴邻乡镇: {nb[:6]}")
    log(f"\n  tongue 悬舌合计面积 {sum(t[0] for t in items):.0f} m² ({sum(t[0] for t in items)/1e6:.4f} km²)")

    # ---- 各大碎片归属 ----
    log("\n== 残留碎片归属(前10) ==")
    frags = parts[1:]
    for k, p in enumerate(frags, 1):
        iso = p.intersection(main0).area
        nb = neighbor_names(p, g, "嘉积镇")
        log(f"  碎片{k}: {p.area:7.0f} m²  与主块重叠{iso:.0f} m²  贴邻{nb[:4]}")
        if k >= 10:
            break

    # ---- 主干细长块 对比上一版(fixed)看差分 ----
    log("\n== 对比 qionghai2002_fixed.shp（细条是否残留自上一版）==")
    fx = gpd.read_file(os.path.join(HERE, "qionghai2002_fixed.shp"), encoding="utf-8")
    jjx = unary_union(list(fx[fx["TOWN"] == "嘉积镇"].geometry))
    # 细带面积对比：本版悬舌面积 vs 上一版同样口径
    def thin_area(gj):
        pp = parts_of(gj)
        b2 = gj.bounds
        wmin, hmin, wmax, hmax = b2
        WW = int((wmax - wmin) / CELL) + 1
        HH = int((hmax - hmin) / CELL) + 1
        mk = np.zeros((HH, WW), np.uint8)
        def tp(xs, ys):
            xs = np.asarray(xs, float); ys = np.asarray(ys, float)
            return np.stack([(xs - wmin) / CELL, (hmax - ys) / CELL], 1).astype(np.int32)
        for p in pp:
            cv2.fillPoly(mk, [tp(p.exterior.xy[0], p.exterior.xy[1])], 1)
            for h in p.interiors:
                cv2.fillPoly(mk, [tp(h.xy[0], h.xy[1])], 0)
        dt2 = cv2.distanceTransform(mk * 255, cv2.DIST_L2, 3) * CELL
        return int(((mk == 1) & (dt2 < R)).sum()) * CELL * CELL
    a_now = thin_area(jj)
    a_fix = thin_area(jjx)
    log(f"  细窄(<60m)面积：fixed 版 {a_fix:.0f} m² → final 版 {a_now:.0f} m²")
    log(f"  final 悬舌(不贴核) {sum(t[0] for t in items):.0f} m²")

if __name__ == "__main__":
    main()