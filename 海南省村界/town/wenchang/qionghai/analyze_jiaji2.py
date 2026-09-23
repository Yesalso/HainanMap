# -*- coding: utf-8 -*-
"""analyze_jiaji2.py —— 嘉积镇边界异常详查（数值化，不依赖看图）"""
from __future__ import annotations
import os, math
import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Polygon, MultiPolygon, Point

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_final.shp")
CELL = 10.0
WIDTH_THR = 60.0
R = WIDTH_THR / 2

def parts_of(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]

def log(m=""):
    print(m, flush=True)

def bearing(cx, cy, x, y):
    dx, dy = x - cx, y - cy
    ang = math.degrees(math.atan2(dy, dx))
    dirs = ["东", "东南", "南", "西南", "西", "西北", "北", "东北"]
    idx = int((ang + 22.5) / 45) % 8
    return dirs[idx], math.hypot(dx, dy)

def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    jj_rows = g[g["TOWN"] == "嘉积镇"]
    jj = unary_union(list(jj_rows.geometry))
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    main0 = parts[0]
    cx, cy = main0.centroid.x, main0.centroid.y
    others = unary_union(list(g[g["TOWN"] != "嘉积镇"].geometry)).buffer(0)

    # ---- A) 微型碎片 ----
    frags = parts[1:]
    if frags:
        ta = sum(p.area for p in frags)
        sizes = sorted([p.area for p in frags])
        log(f"== A) 残留微型碎片：{len(frags)} 块，合计 {ta:.1f} m² ({ta/1e6:.6f} km²) ==")
        log(f"  面积中位数 {np.median(sizes):.0f} m²，最大 {sizes[-1]:.0f} m²，最小 {sizes[0]:.0f} m²")
        for k, p in enumerate(frags, 1):
            b, d = bearing(cx, cy, p.representative_point().x, p.representative_point().y)
            log(f"  碎片{k}: {p.area:.0f} m²  方位{b} 距质心{d/1e3:.1f}km")
    else:
        log("== A) 无残留碎片 ==")

    # ---- B) 细窄区域细化（区分“尾巴/半岛”与“细腰”）----
    bnd = main0.bounds
    minx, miny, maxx, maxy = bnd
    W = int((maxx - minx) / CELL) + 1
    H = int((maxy - miny) / CELL) + 1
    mask = np.zeros((H, W), np.uint8)
    def to_px(xs, ys):
        xs = np.asarray(xs, float); ys = np.asarray(ys, float)
        return np.stack([(xs - minx) / CELL, (maxy - ys) / CELL], 1).astype(np.int32)
    for p in parts:
        cv2.fillPoly(mask, [to_px(p.exterior.xy[0], p.exterior.xy[1])], 1)
        for h in p.interiors:
            cv2.fillPoly(mask, [to_px(h.xy[0], h.xy[1])], 0)
    dt = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 3)
    Rmap = dt * CELL
    thin = (mask == 1) & (Rmap < R)
    n, lab, st, _ = cv2.connectedComponentsWithStats(thin.astype(np.uint8) * 255, 8)

    # thick = 粗核心(>=R)；细带中 8邻域邻接 thick 的为“边缘浅带”，否则为悬舌
    thick = Rmap >= R
    kd = cv2.dilate(thick.astype(np.uint8), np.ones((3,3), np.uint8), 1) > 0
    edgeband = thin & kd           # 贴核心的浅带
    tongue   = thin & (~kd)        # 不贴核心（孤独细片/舌端）
    def cellgeom(ys, xs):
        return Polygon([
            (minx + xs*CELL, maxy - ys*CELL),
            (minx + (xs+1)*CELL, maxy - ys*CELL),
            (minx + (xs+1)*CELL, maxy - (ys+1)*CELL),
            (minx + xs*CELL, maxy - (ys+1)*CELL)])

    log("\n== B) 细窄网格(<60m)分析 ==")
    for name, cmap, rev in (("edgeband 贴核心浅带", edgeband, False), ("tongue 悬舌/孤独细片", tongue, True)):
        n2, lab2, st2, _ = cv2.connectedComponentsWithStats(cmap.astype(np.uint8)*255, 8)
        comps = []
        for i in range(1, n2):
            a = st2[i, cv2.CC_STAT_AREA] * CELL * CELL
            if a < 500:
                continue
            ys, xs = np.nonzero(lab2 == i)
            comps.append((a, unary_union([cellgeom(int(y), int(x)) for y, x in zip(ys, xs)])))
        comps.sort(key=lambda t: -t[0])
        log(f"  {name}: {len(comps)} 片(>=500m²)")
        for a, geom in comps:
            # 细长率
            L = geom.length; ratio = a / (L/2)**2
            bd = bearing(cx, cy, geom.representative_point().x, geom.representative_point().y)
            b = bd[0]; bdist = bd[1]
            wmax = None
            # 该片内最大内切半径
            b2 = geom.bounds
            W2 = max(2, int((b2[2]-b2[0])/CELL)+1); H2 = max(2, int((b2[3]-b2[1])/CELL)+1)
            mk = np.zeros((H2, W2), np.uint8)
            def tp(xs, ys):
                xs = np.asarray(xs,float); ys=np.asarray(ys,float)
                return np.stack([(xs-b2[0])/CELL, (b2[3]-ys)/CELL],1).astype(np.int32)
            for pp in parts_of(geom):
                cv2.fillPoly(mk, [tp(pp.exterior.xy[0], pp.exterior.xy[1])], 1)
            if mk.sum() > 0:
                dt2 = cv2.distanceTransform(mk*255, cv2.DIST_L2, 3) * CELL
                wmax = dt2.max()*2
            tag = "  ← 窄长条" if ratio < 0.12 else ""
            log(f"    {a:.0f} m²  方位{b} 距质心{bdist/1e3:.1f}km  细长率{ratio:.3f} 最宽{wmax:.0f}m{tag}")

    # ---- C) 外凸尖刺检测：边界上“长度/基底”比值大的突出 ----
    log("\n== C) 外凸突起(尖刺)检测 ==")
    # 用主块边界点，计算各点处“到多边形外侧长距离但内侧很快见水”的凹角：
    # 简化：凸包边界与多边形边界的“嵌入深度”基于 hull - poly 的窄带
    hull = main0.convex_hull
    diff = hull.difference(main0)
    if not diff.is_empty:
        log(f"  凸包⊖多边形：{diff.area:.1f} m² (占凸包 {diff.area/hull.area*100:.2f}%)")
        # diff 的各连通件 = 缺口；缺口宽度/面积 => 潜在尖刺
        n3, lab3, st3, _ = cv2.connectedComponentsWithStats((~mask & np.zeros_like(mask)).astype(np.uint8), 8)
    else:
        log("  与凸包完全重合（无凹入）")

    # ---- D) 主块与邻居贴合度 ----
    log("\n== D) 主块全局形态 ==")
    log(f"  面积 {main0.area/1e6:.4f} km²，周长 {main0.length/1e3:.2f} km，"
        f"紧凑度 {4*math.pi*main0.area/main0.length**2:.3f}")
    log(f"  凸包面积 {hull.area/1e6:.4f} km²，凹入比 {1-main0.area/hull.area:.3f}")

if __name__ == "__main__":
    main()