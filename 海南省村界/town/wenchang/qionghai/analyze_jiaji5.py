# -*- coding: utf-8 -*-
"""analyze_jiaji5.py —— 全口径长尾巴检测（不受 贴/不贴核心 影响）"""
from __future__ import annotations
import os, math, itertools, json
import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_final.shp")
CELL = 10.0

def parts_of(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]

def log(m=""):
    print(m, flush=True)

def longest_chord_xy(coords):
    best = 0.0
    for a, b in itertools.combinations(coords, 2):
        d = math.hypot(a[0] - b[0], a[1] - b[1])
        if d > best:
            best = d
    return best

def main():
    WMAX = 60.0   # 判定“尾巴”的最大局部宽度
    g = gpd.read_file(SHP, encoding="utf-8")
    jj = unary_union(list(g[g["TOWN"] == "嘉积镇"].geometry))
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    main0 = parts[0]
    cx, cy = main0.centroid.x, main0.centroid.y

    minx, miny, maxx, maxy = jj.bounds
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
    dt = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 3) * CELL
    thin = (mask == 1) & (dt < WMAX / 2)
    n, lab, st, _ = cv2.connectedComponentsWithStats(thin.astype(np.uint8) * 255, 8)

    def cellgeom(ys, xs):
        return Polygon([
            (minx + xs * CELL, maxy - ys * CELL),
            (minx + (xs + 1) * CELL, maxy - ys * CELL),
            (minx + (xs + 1) * CELL, maxy - (ys + 1) * CELL),
            (minx + xs * CELL, maxy - (ys + 1) * CELL)])

    comps = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA] * CELL * CELL
        ys, xs = np.nonzero(lab == i)
        if len(ys) == 0:
            continue
        geom = unary_union([cellgeom(int(y), int(x)) for y, x in zip(ys, xs)])
        c = [p.exterior.coords for p in parts_of(geom)]
        pts = [x for rng in c for x in rng]
        chord = longest_chord_xy(pts)
        comps.append((a, chord, geom))
    comps.sort(key=lambda t: -t[0])

    total_thin = sum(t[0] for t in comps)
    log(f"嘉积镇面积 {jj.area/1e6:.4f} km²")
    log(f"窄于{WMAX:.0f}m 的像元合计 {total_thin:.0f} m² = {total_thin/1e6:.4f} km²"
        f"（占镇域 {total_thin/jj.area*100:.2f}%）")
    log(f"窄带分区数：{len(comps)}")

    big = [t for t in comps if t[1] > 1000 and t[0] >= 3000]
    log(f"\n== 大尾巴（最长弦>1km，面积≥3000m²）共 {len(big)} 条 ==")
    feats = []
    for a, chord, geom in big:
        perim = sum(p.exterior.length for p in parts_of(geom))
        length = perim / 2
        avg_w = a / max(length, 1e-6)
        cx0, cy0 = geom.representative_point().x, geom.representative_point().y
        ang = math.degrees(math.atan2(cy0 - cy, cx0 - cx))
        dirs = ["东", "东南", "南", "西南", "西", "西北", "北", "东北"]
        d = dirs[int((ang + 22.5) / 45) % 8]
        dist = math.hypot(cx0 - cx, cy0 - cy)
        # 贴邻乡镇
        nb = []
        for _, r in g.iterrows():
            if r["TOWN"] == "嘉积镇":
                continue
            try:
                if geom.boundary.buffer(1).intersects(r.geometry.boundary.buffer(1)):
                    nb.append(r["TOWN"])
            except Exception:
                pass
        log(f"  [{d}] 面积{a/1e4:7.2f}ha 弦长{chord/1e3:.2f}km 估算长{length/1e3:.2f}km "
            f"平均宽{avg_w:.1f}m 距质心{dist/1e3:.2f}km 贴邻{nb}")
        feats.append({"type": "长尾巴", "area_m2": round(a), "length_m": round(chord),
                      "avg_width_m": round(avg_w, 1), "bearing": d, "neighbors": nb,
                      "geometry": geom})
    ta = sum(t[0] for t in big)
    log(f"\n大尾巴合计 {ta/1e4:.2f} ha = {ta/1e6:.4f} km²（占镇域 {ta/jj.area*100:.2f}%）")

    # 中小尾巴（0.5-1km 弦）
    mid = [t for t in comps if 500 < t[1] <= 1000 and t[0] >= 3000]
    if mid:
        log(f"\n== 中等窄带（弦0.5–1km）{len(mid)} 条 ==")
        for a, chord, geom in mid:
            cx0, cy0 = geom.representative_point().x, geom.representative_point().y
            ang = math.degrees(math.atan2(cy0 - cy, cx0 - cx))
            dirs = ["东", "东南", "南", "西南", "西", "西北", "北", "东北"]
            d = dirs[int((ang + 22.5) / 45) % 8]
            log(f"  面积{a/1e4:6.2f}ha 弦长{chord/1e3:.2f}km 方位{d}")
            feats.append({"type": "中等窄带", "area_m2": round(a), "length_m": round(chord),
                          "bearing": d, "geometry": geom})

    # 导出
    gj = {"type": "FeatureCollection",
          "crs": {"type": "name", "properties": {"name": "EPSG:32649"}},
          "features": []}
    for f2 in feats:
        pr = dict(f2)
        pr.pop("geometry", None)
        gj["features"].append({"type": "Feature", "properties": pr, "geometry": f2["geometry"]})
    out = os.path.join(HERE, "jiaji_tails.geojson")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(gj, f, ensure_ascii=False, default=lambda o: None)
    log(f"\n导出 {out}")

if __name__ == "__main__":
    main()