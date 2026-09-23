# -*- coding: utf-8 -*-
"""analyze_jiaji4.py —— 尾巴长度/宽度实测 + 导出GeoJSON + 尖刺检测"""
from __future__ import annotations
import os, math, json
import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Polygon, mapping

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

def longest_chord(g):
    import itertools
    c = [p.exterior.coords for p in parts_of(g)]
    pts = [x for ring in c for x in ring]
    best = 0.0
    for a, b in itertools.combinations(pts, 2):
        d = math.hypot(a[0] - b[0], a[1] - b[1])
        if d > best:
            best = d
    return best

def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    jj_row = g[g["TOWN"] == "嘉积镇"]
    jj = unary_union(list(jj_row.geometry))
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    main0 = parts[0]
    cx, cy = main0.centroid.x, main0.centroid.y

    bnd = jj.bounds
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

    items = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA] * CELL * CELL
        if a < 500:
            continue
        ys, xs = np.nonzero(lab == i)
        geom = unary_union([cellgeom(int(y), int(x)) for y, x in zip(ys, xs)])
        items.append((a, geom))
    items.sort(key=lambda t: -t[0])

    feats = []
    log("== 长尾巴/悬舌实测 ==")
    for a, geom in items:
        parts_g = parts_of(geom)
        perim = sum(p.exterior.length for p in parts_g)
        L = max(perim / 2, 1e-6)
        chord = longest_chord(geom)
        avg_w = a / L
        cx0, cy0 = geom.representative_point().x, geom.representative_point().y
        ang = math.degrees(math.atan2(cy0 - cy, cx0 - cx))
        dirs = ["东", "东南", "南", "西南", "西", "西北", "北", "东北"]
        d = dirs[int((ang + 22.5) / 45) % 8]
        dist = math.hypot(cx0 - cx, cy0 - cy)
        log(f"  面积{a:7.0f}m² 最长弦{chord/1e3:6.2f}km 平均宽{avg_w:4.1f}m "
            f"方位{d} 距质心{dist/1e3:.2f}km")
        feats.append({
            "type": "悬舌/细缝", "area_m2": round(a), "length_m": round(chord),
            "avg_width_m": round(avg_w, 1), "bearing": d, "dist_km": round(dist / 1e3, 2),
            "geometry": mapping(geom)})
    log(f"  悬舌合计 {sum(t[0] for t in items):.0f} m² = {sum(t[0] for t in items)/1e6:.4f} km²")

    # 碎片导出
    frags = parts[1:]
    frag_feats = []
    for k, p in enumerate(frags, 1):
        cx0, cy0 = p.representative_point().x, p.representative_point().y
        ang = math.degrees(math.atan2(cy0 - cy, cx0 - cx))
        dirs = ["东", "东南", "南", "西南", "西", "西北", "北", "东北"]
        d = dirs[int((ang + 22.5) / 45) % 8]
        frag_feats.append({
            "type": "残留碎片", "area_m2": round(p.area), "bearing": d,
            "dist_km": round(math.hypot(cx0 - cx, cy0 - cy) / 1e3, 2),
            "geometry": mapping(p)})
    log(f"  碎片 {len(frag_feats)} 块合计 {sum(f['area_m2'] for f in frag_feats)} m²")

    # 尖刺：宽度<30m且长度>150m的舌端（更细）
    spike_band = (mask == 1) & (dt < 15)   # <30m
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(spike_band.astype(np.uint8) * 255, 8)
    spikes = []
    for i in range(1, n2):
        a = st2[i, cv2.CC_STAT_AREA] * CELL * CELL
        if a < 400:
            continue
        ys, xs = np.nonzero(lab2 == i)
        sp = unary_union([cellgeom(int(y), int(x)) for y, x in zip(ys, xs)])
        chord = longest_chord(sp)
        if chord > 150:
            spikes.append((a, chord, sp))
    spikes.sort(key=lambda t: -t[1])
    log(f"\n== 细尖刺(宽<30m, 长>150m)：{len(spikes)} 处 ==")
    for a, chord, sp in spikes:
        log(f"  面积{a:6.0f}m² 长{chord/1e3*1000:5.0f}m")
        feats.append({"type": "细尖刺", "area_m2": round(a), "length_m": round(chord),
                      "geometry": mapping(sp)})

    # 导出 GeoJSON（EPSG:32649）
    gj = {"type": "FeatureCollection",
          "crs": {"type": "name", "properties": {"name": "EPSG:32649"}},
          "features": [{"type": "Feature", "properties": {
              p["type"]: p.get("area_m2"), "area_m2": p.get("area_m2", 0),
              "length_m": p.get("length_m", 0), "bearing": p.get("bearing", "")},
                        "geometry": p["geometry"]} for p in feats]}
    out = os.path.join(HERE, "jiaji_anomalies.geojson")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(gj, f, ensure_ascii=False)
    log(f"\n导出 {out}")

if __name__ == "__main__":
    main()