# -*- coding: utf-8 -*-
"""
check_reverse.py —— 反向映射结果校验 + 出图（配合 reverse_by_color.py）
=========================================================================
校验项（对齐 town/2002海口转换总结.txt 的纪律）：
  1. 面积守恒、两两重叠、几何有效；
  2. 逐现行乡镇：与结果中同码要素并集的对称差（≈0=未改动；大=被拆分，符合预期）；
  3. 手绘黑线（剔除彩色填充）距新界线距离分布 = 贴合度；
  4. 出图：overlay（新界线叠手绘图：红=目标年新增，绿=沿用）、side-by-side、着色图。

用法：
  python check_reverse.py
  python check_reverse.py --shp wenchang.shp --new wenchang2002.shp --img Wenchang_draw.png
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely import make_valid

HERE = os.path.dirname(os.path.abspath(__file__))


def log(m=""):
    print(m, flush=True)


def hex2bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def load_color(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def raster_rings(mask, geoms, minx, maxy, geo_w, geo_h, W, H, closed=False, color=255, thick=1):
    for geom in geoms:
        ring = geom.boundary
        segs = list(ring.geoms) if ring.geom_type in ("MultiLineString", "GeometryCollection") else [ring]
        for ln in segs:
            if ln is None or ln.is_empty:
                continue
            c = np.array(ln.coords)
            if len(c) < 2:
                continue
            pts = np.stack([(c[:, 0] - minx) / geo_w * W, (maxy - c[:, 1]) / geo_h * H], 1).astype(np.int32)
            cv2.polylines(mask, [pts], closed, color, thick, cv2.LINE_8)


def main(argv=None):
    ap = argparse.ArgumentParser(description="反向映射结果校验 + 出图")
    ap.add_argument("--shp", default=os.path.join(HERE, "wenchang.shp"), help="现行本县 SHP")
    ap.add_argument("--new", default=os.path.join(HERE, "wenchang2002.shp"), help="反向结果 SHP")
    ap.add_argument("--img", default=os.path.join(HERE, "Wenchang_draw.png"), help="手绘 PNG")
    ap.add_argument("--out", default=os.path.join(HERE, "wenchang2002"), help="出图前缀")
    ap.add_argument("--crs", default="EPSG:32649")
    ap.add_argument("--color-map", default=os.path.join(HERE, "color_map_wenchang.json"))
    ap.add_argument("--color-tol", type=int, default=30)
    args = ap.parse_args(argv)

    import json
    colors = {}
    if os.path.exists(args.color_map):
        with open(args.color_map, encoding="utf-8") as f:
            colors = json.load(f).get("colors", {})

    args.out = os.path.abspath(args.out)

    cur = gpd.read_file(args.shp, encoding="utf-8")
    new = gpd.read_file(args.new, encoding="utf-8")
    cur = cur.to_crs(args.crs)
    new = new.to_crs(args.crs)
    cur["geometry"] = cur.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    new["geometry"] = new.geometry.apply(lambda x: x if x.is_valid else make_valid(x))

    newnames = set(new.loc[new.get("SOURCE", "") == "2002新增", "TOWN"]) if "SOURCE" in new else set()

    log("--- 1) 面积 / 重叠 / 合法性 ---")
    tot = new.geometry.area.sum() / 1e6
    curv = cur.geometry.area.sum() / 1e6
    ua = unary_union(list(new.geometry)).buffer(0).area / 1e6
    log(f"  现行 {curv:.3f} km²  结果 {tot:.3f} km²  并 {ua:.3f} km²  重叠 {(tot-ua):.5f} km²")
    mx = 0.0
    geoms = list(new.geometry)
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if geoms[i].intersects(geoms[j]):
                mx = max(mx, geoms[i].intersection(geoms[j]).area)
    log(f"  两两最大重叠 {mx:.3f} m²  非法几何 {int((~new.geometry.is_valid).sum())}")

    log("--- 2) 逐现行乡镇：未改动 vs 被拆分 ---")
    unchanged = changed = 0
    for _, r in cur.iterrows():
        sub = new[new["CODE"].astype(str) == str(r["CODE"])] if "CODE" in new else new.iloc[0:0]
        if len(sub) == 0:
            continue
        u = unary_union(list(sub.geometry)).buffer(0)
        sd = u.symmetric_difference(r.geometry).area
        if sd < 1:
            unchanged += 1
        else:
            changed += 1
            log(f"  {r['TOWN']}: 被拆分，对称差 {sd/1e6:.3f} km²（含旧乡镇 {'/'.join(newnames) if False else ''}）")
    log(f"  未改动 {unchanged} 个，被拆分 {changed} 个（应为受影响乡镇数）")

    log("--- 3) 手绘黑线 vs 新界线 贴合度 ---")
    minx, miny, maxx, maxy = new.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    img = load_color(args.img)
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    colored = np.zeros((H, W), np.uint8)
    for hx in colors:
        d = np.abs(img.astype(np.int16) - np.array(hex2bgr(hx), np.int16)).sum(axis=2)
        colored = np.maximum(colored, (d <= args.color_tol).astype(np.uint8))
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    drawn = ((gray < 100) & (cv2.dilate(colored, k3, 1) == 0)).astype(np.uint8)
    lines = np.zeros((H, W), np.uint8)
    raster_rings(lines, list(new.geometry), minx, maxy, geo_w, geo_h, W, H)
    lines = (lines > 0).astype(np.uint8)
    dt = cv2.distanceTransform((1 - lines) * 255, cv2.DIST_L2, 3)
    ys, xs = np.nonzero(drawn)
    dd = dt[ys, xs]
    px_m = geo_w / W
    for t in (1, 2, 3, 5):
        log(f"  ≤{t}px({t*px_m:.0f}m): {(dd<=t).mean():.4f}")
    log(f"  平均 {dd.mean():.2f}px 中位 {np.median(dd):.2f}px")

    log("--- 4) 出图 ---")
    base = load_color(args.img)
    ov = base.copy()
    for _, r in new.iterrows():
        col = (0, 0, 255) if r["TOWN"] in newnames else (0, 180, 0)
        raster_rings(ov, [r.geometry], minx, maxy, geo_w, geo_h, W, H, False, col, 3)
    cv2.imencode(".png", ov)[1].tofile(args.out + "_overlay.png")

    fills = np.full((H, W, 3), 255, np.uint8)
    for _, r in new.iterrows():
        col = (0, 0, 255) if r["TOWN"] in newnames else (235, 235, 235)
        parts = [r.geometry] if r.geometry.geom_type == "Polygon" else list(r.geometry.geoms)
        for pg in parts:
            c = np.array(pg.exterior.coords)
            pts = np.stack([(c[:, 0] - minx) / geo_w * W, (maxy - c[:, 1]) / geo_h * H], 1).astype(np.int32)
            cv2.fillPoly(fills, [pts], col)
            cv2.polylines(fills, [pts], True, (0, 0, 0), 1, cv2.LINE_8)
            for hole in pg.interiors:
                hc = np.array(hole.coords)
                hp = np.stack([(hc[:, 0] - minx) / geo_w * W, (maxy - hc[:, 1]) / geo_h * H], 1).astype(np.int32)
                cv2.fillPoly(fills, [hp], (255, 255, 255))
    comb = np.hstack([base, fills])
    cv2.imencode(".png", comb)[1].tofile(args.out + "_sidebyside.png")
    log(f"  {args.out}_overlay.png（红=新增 绿=沿用）")
    log(f"  {args.out}_sidebyside.png（左=手绘 右=新SHP）")
    log("DONE")


if __name__ == "__main__":
    main()