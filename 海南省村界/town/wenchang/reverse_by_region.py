# -*- coding: utf-8 -*-
"""
reverse_by_region.py —— 手绘上色 PNG → 目标年乡镇 SHP（洪泛 + EDT 唯一归属，按颜色命名）
========================================================================================
适用：手绘图为"完全重绘"（黑线即目标年边界网络，彩色块为目标年旧乡镇）。

流程：
  1. 读现行本县 SHP（取四至做像素↔米制配准，并取县外轮廓做海岸线）；
  2. 手绘 PNG 黑像素 = 目标年线网，并上县外轮廓线；形态学闭运算桥接断点；
  3. 线网膨胀 1px 成墙 → 白色区 8 连通洪泛 = 各单元种子；
  4. 每个种子区域按"主导填充色"命名（颜色表），无彩色者按与现行乡镇最大重叠判为"沿用"；
  5. 对全图做欧氏距离变换(EDT)，把墙/缝像素归给最近种子 → 全域无缝无叠唯一归属；
  6. 逐区域取轮廓 → 地理多边形（EPSG:32649）。

输出：<out>.shp / .geojson / _Albers.shp，字段 CODE/TOWN/CITY/SOURCE/AREA_KM2
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import make_valid
from scipy import ndimage


def log(m=""):
    print(m, flush=True)


def hex2bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def load_color(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def rasterize_ring(mask, geom, minx, miny, maxy, geo_w, geo_h, W, H):
    if geom is None or geom.is_empty:
        return
    ring = geom.boundary
    lines = list(ring.geoms) if ring.geom_type in ("MultiLineString", "GeometryCollection") else [ring]
    for ln in lines:
        if ln is None or ln.is_empty:
            continue
        try:
            coords = np.array(ln.coords)
        except Exception:
            continue
        if len(coords) < 2:
            continue
        pxp = (coords[:, 0] - minx) / geo_w * W
        pyp = (maxy - coords[:, 1]) / geo_h * H
        cv2.polylines(mask, [np.stack([pxp, pyp], 1).astype(np.int32)], False, 255, 1, cv2.LINE_8)


def fill_poly(mask, geom, minx, miny, maxy, geo_w, geo_h, W, H, val=255):
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for pg in parts:
        ext = np.array(pg.exterior.coords)
        pxp = (ext[:, 0] - minx) / geo_w * W
        pyp = (maxy - ext[:, 1]) / geo_h * H
        cv2.fillPoly(mask, [np.stack([pxp, pyp], 1).astype(np.int32)], val)
        for hole in pg.interiors:
            hc = np.array(hole.coords)
            pxp = (hc[:, 0] - minx) / geo_w * W
            pyp = (maxy - hc[:, 1]) / geo_h * H
            cv2.fillPoly(mask, [np.stack([pxp, pyp], 1).astype(np.int32)], 0)


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shp", required=True, help="现行本县乡镇 SHP")
    ap.add_argument("--img", required=True, help="手绘上色 PNG")
    ap.add_argument("--color-map", required=True, help="颜色表 JSON")
    ap.add_argument("--out", required=True, help="输出前缀")
    ap.add_argument("--crs", default="EPSG:32649")
    ap.add_argument("--color-tol", type=int, default=30, help="颜色匹配容差(曼哈顿)")
    ap.add_argument("--min-region-px", type=int, default=200, help="最小区域像素")
    ap.add_argument("--close-iter", type=int, default=2, help="形态学闭运算迭代")
    ap.add_argument("--min-color-frac", type=float, default=0.05,
                    help="区域中某颜色占比超过该值即判为彩色单元")
    ap.add_argument("--min-keep-km2", type=float, default=0.6,
                    help="小于该面积(km²)的区域并入相邻最长公共边界的区域（孤立岛除外）")
    ap.add_argument("--no-albers", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    cm = json.load(open(args.color_map, encoding="utf-8"))
    city = cm.get("city", "")
    code_start = int(cm.get("new_code_start", 0))
    new_order = cm.get("new_order", [])
    colors = cm["colors"]

    g = gpd.read_file(args.shp, encoding="utf-8")
    src_crs = g.crs
    g["geometry"] = g.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    g = g.to_crs(args.crs).reset_index(drop=True)
    minx, miny, maxx, maxy = g.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny

    img = load_color(args.img)
    H, W = img.shape[:2]
    step_x, step_y = geo_w / W, geo_h / H
    log(f"  现行 SHP：{len(g)} 个乡镇  图幅 {W}×{H}  步长 {step_x:.3f}×{step_y:.3f} m/px")

    def px2geo(i, j):
        return minx + (i + 0.5) * step_x, maxy - (j + 0.5) * step_y

    outer = unary_union([x for x in g.geometry if x is not None and not x.is_empty]).buffer(0)

    # 线网 = 手绘黑线 + 县外轮廓
    b, gg, r = img[:, :, 0].astype(int), img[:, :, 1].astype(int), img[:, :, 2].astype(int)
    dark = ((b < 100) & (gg < 100) & (r < 100)).astype(np.uint8)
    outer_line = np.zeros((H, W), np.uint8)
    rasterize_ring(outer_line, outer, minx, miny, maxy, geo_w, geo_h, W, H)
    lines = np.maximum(dark, (outer_line == 255).astype(np.uint8))
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    net = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, k3, iterations=args.close_iter)
    net_thick = cv2.dilate(net, k3, iterations=1)
    white = (net_thick == 0).astype(np.uint8)
    n_reg, reg_label, reg_stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)

    # 颜色掩膜
    pal = []
    for hx, spec in colors.items():
        names = spec if isinstance(spec, list) else [spec]
        bgr = np.array(hex2bgr(hx), np.int16)
        d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
        pal.append((hx, names, (d <= args.color_tol)))

    # 陆地掩膜
    land = np.zeros((H, W), np.uint8)
    fill_poly(land, outer, minx, miny, maxy, geo_w, geo_h, W, H)
    land = land > 0

    # 种子：每个白色区域一个 id；记录其名称
    seed = np.zeros((H, W), np.int32)
    nid = 0
    reg_name = {}
    reg_kind = {}
    for i in range(1, n_reg):
        area = int(reg_stats[i, cv2.CC_STAT_AREA])
        if area < args.min_region_px:
            continue
        x0 = int(reg_stats[i, cv2.CC_STAT_LEFT]); y0 = int(reg_stats[i, cv2.CC_STAT_TOP])
        wb = int(reg_stats[i, cv2.CC_STAT_WIDTH]); hb = int(reg_stats[i, cv2.CC_STAT_HEIGHT])
        if x0 == 0 or y0 == 0 or x0 + wb >= W or y0 + hb >= H:
            continue
        m = (reg_label == i)
        nid += 1
        seed[m] = nid
        # 主导颜色
        best_name, best_cnt = None, 0
        for hx, names, cmask in pal:
            cnt = int((m & cmask).sum())
            if cnt > best_cnt:
                best_cnt, best_name = cnt, names[0]
        if best_name is not None and best_cnt >= args.min_color_frac * area:
            reg_name[nid] = best_name
            reg_kind[nid] = "color"
        else:
            reg_name[nid] = None
            reg_kind[nid] = "white"
    log(f"  种子区域：{nid} 个（彩色 {sum(1 for k in reg_kind.values() if k=='color')}，"
        f"白区 {sum(1 for k in reg_kind.values() if k=='white')}）")

    # EDT 唯一归属
    _, inds = ndimage.distance_transform_edt(seed == 0, return_indices=True)
    assigned = seed[inds[0], inds[1]]

    # 白色区域按与现行乡镇最大重叠命名
    for rid in [k for k, v in reg_kind.items() if v == "white"]:
        m = ((assigned == rid) & land)
        if m.sum() == 0:
            continue
        best, bo = None, 0.0
        ys, xs = np.where(m)
        # 用代表性点集做快速判定
        for _, rr in g.iterrows():
            # 栅格化该乡镇并求交（用包围盒快速过滤）
            bb = rr.geometry.bounds
            if bb[2] < minx or bb[0] > maxx or bb[3] < miny or bb[1] > maxy:
                continue
            rm = np.zeros((H, W), np.uint8)
            fill_poly(rm, rr.geometry, minx, miny, maxy, geo_w, geo_h, W, H)
            ov = int((m & (rm > 0)).sum())
            if ov > bo:
                bo, best = ov, rr
        if best is not None:
            reg_name[rid] = best["TOWN"]

    # 编号：彩色区域按 new_order 编新码；白区沿用现行码
    color_names = [reg_name[k] for k, v in reg_kind.items() if v == "color"]
    order = [n for n in new_order if n in set(color_names)]
    order += [n for n in set(color_names) if n not in order]
    name_to_code = {nm: str(code_start + i) for i, nm in enumerate(order)}
    log(f"  彩色单元编号：{name_to_code}")

    # 小区域并入相邻（最长公共边界）——消除色块与黑线间的碎条/小碎区
    px_per_km2 = 1e6 / (step_x * step_y)
    min_keep_px = args.min_keep_km2 * px_per_km2
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    merged_cnt = 0
    changed = True
    while changed:
        changed = False
        for s in np.unique(assigned):
            if s == 0:
                continue
            m = (assigned == s) & land
            a = int(m.sum())
            if a == 0 or a >= min_keep_px:
                continue
            dil = cv2.dilate(m.astype(np.uint8), k3, iterations=1).astype(bool) & land & ~m
            nb = assigned[dil]
            nb = nb[(nb != 0) & (nb != s)]
            if len(nb) == 0:
                continue                      # 孤立（如岛屿）→ 保留
            vals, counts = np.unique(nb, return_counts=True)
            tgt = int(vals[np.argmax(counts)])
            assigned[m] = tgt
            merged_cnt += 1
            changed = True
    log(f"  小区域并入：{merged_cnt} 次")

    # 矢量化
    rows = []
    for rid in range(1, nid + 1):
        m = ((assigned == rid) & land).astype(np.uint8) * 255
        if int(m.sum()) // 255 < args.min_region_px:
            continue
        contours, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not contours or hier is None:
            continue
        hier = hier[0]
        polys = []
        for ci, c in enumerate(contours):
            if hier[ci][3] != -1:
                continue
            if cv2.contourArea(c) < args.min_region_px:
                continue
            ext = [px2geo(int(p[0]), int(p[1])) for p in c[:, 0, :]]
            if len(ext) < 3:
                continue
            holes = []
            child = hier[ci][2]
            while child != -1:
                hc = [px2geo(int(p[0]), int(p[1])) for p in contours[child][:, 0, :]]
                if len(hc) >= 3:
                    holes.append(hc)
                child = hier[child][0]
            pg = Polygon(ext, holes).buffer(0)
            if not pg.is_empty and pg.area > 0:
                polys.append(pg)
        if not polys:
            continue
        geom = unary_union(polys)
        nm = reg_name.get(rid)
        kind = reg_kind.get(rid)
        if kind == "color":
            code = name_to_code.get(nm, "")
            src = "2002新增"
        else:
            code = None
            for _, rr in g.iterrows():
                if rr["TOWN"] == nm:
                    code = str(rr["CODE"]); break
            code = code or ""
            src = "沿用"
        rows.append((code, nm or "", city, src, geom))

    res = gpd.GeoDataFrame(
        {"CODE": [x[0] for x in rows], "TOWN": [x[1] for x in rows],
         "CITY": [x[2] for x in rows], "SOURCE": [x[3] for x in rows]},
        geometry=[x[4] for x in rows], crs=args.crs)
    res["geometry"] = res.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    res["AREA_KM2"] = (res.geometry.area / 1e6).round(4)

    log(f"  输出要素 {len(res)}  面积 {res.geometry.area.sum()/1e6:.4f} km²  "
        f"并 {unary_union(list(res.geometry)).area/1e6:.4f} km²")
    log(res[["CODE", "TOWN", "SOURCE", "AREA_KM2"]].to_string(index=False))

    out_base = os.path.abspath(args.out)
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".geojson"):
        p = out_base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    res.to_file(out_base + ".shp", encoding="utf-8")
    res.to_file(out_base + ".geojson", driver="GeoJSON", encoding="utf-8")
    if not args.no_albers and src_crs is not None:
        res.to_crs(src_crs).to_file(out_base + "_Albers.shp", encoding="utf-8")
    log(f"  写出：{out_base}.shp / .geojson")
    log("DONE")


if __name__ == "__main__":
    main()