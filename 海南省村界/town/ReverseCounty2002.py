# -*- coding: utf-8 -*-
"""
ReverseCounty2002.py —— 手绘地图 PNG → 乡镇级 SHP 反向映射（通用版）
====================================================================
把手动修改后的县市手绘地图 PNG，按正向制图参数（Hainan.py：EPSG:32649、
1px 固定米数、四至范围）反向映射回地理坐标的乡镇级多边形，外海岸线以权威
SHP（海南村界.shp）为准。

流程（与 Sanya/reverse_sanya_2002.py 一致）：
  1. 读权威 SHP，按县码裁出目标县，按 9 位乡镇码分组；
  2. 目标县外轮廓栅格化 → 海岸线权威线网；
  3. 手绘 PNG 黑像素 = 2002 线网（可选：用现行渲染图做文字掩膜剔除地名）；
  4. 线网并上海岸线 → 形态学闭运算桥接断点 → 白色区域 8 连通洪泛 = 各单元内部；
  5. 每区域取外轮廓，顶点吸附到线网中心像素（消半像素偏移）→ 地理多边形；
  6. 与权威外陆地求交、缝隙按 40m 归属邻居、残余海岛补回；
  7. 按最大重叠继承 9 位乡镇码（可另给 code→名称 映射）。

输出：<out>.shp / <out>.geojson（EPSG:32649，字段 乡镇码/乡镇名/面积km2）
      <out>_Albers.shp（转回权威 SHP 的源坐标系）

用法示例：
  python ReverseCounty2002.py \
    --shp ../../海南村界.shp --code-field XZQDM --name-field XZQMC \
    --county 469005 --img ../Empty_map/Wenchang2.png \
    --name-map Wenchang_names.json \
    --out 文昌2002_reverse
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


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def log(msg=""):
    print(msg, flush=True)


def load_gray(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def safe_geom(g):
    if g is None:
        return None
    try:
        if not g.is_valid:
            g = make_valid(g)
    except Exception:
        pass
    return g


def rasterize_ring(mask, geom, minx, miny, maxy, geo_w, geo_h, W, H):
    """把几何的边界线画进 mask（uint8 0/255）。逐部件画，保留内部共享边。"""
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
        pts = np.stack([pxp, pyp], 1).astype(np.int32)
        cv2.polylines(mask, [pts], False, 255, 1, cv2.LINE_8)


def render_polys(polys, minx, maxy, geo_w, geo_h, W, H):
    """把结果多边形栅格化成边界线网，用于重合率校验。"""
    m = np.zeros((H, W), np.uint8)
    for p in polys:
        gs = list(p.geoms) if p.geom_type == "MultiPolygon" else [p]
        for pg in gs:
            coords = np.array(pg.exterior.coords)
            pxp = (coords[:, 0] - minx) / geo_w * W
            pyp = (maxy - coords[:, 1]) / geo_h * H
            cv2.polylines(m, [np.stack([pxp, pyp], 1).astype(np.int32)],
                          True, 255, 1, cv2.LINE_8)
    return (m == 255)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="手绘 PNG → 乡镇级 SHP 反向映射（通用版）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shp", required=True, help="权威 SHP（如 海南村界.shp）")
    ap.add_argument("--shp-encoding", default="gbk", help="权威 SHP 属性编码，默认 gbk")
    ap.add_argument("--code-field", default="XZQDM", help="权威 SHP 行政码字段")
    ap.add_argument("--name-field", default="XZQMC", help="权威 SHP 名称字段")
    ap.add_argument("--county", required=True, help="县码前缀（6 位，如 469005）")
    ap.add_argument("--group-len", type=int, default=9, help="乡镇码位数，默认 9")
    ap.add_argument("--img", required=True, help="手绘地图 PNG（待反向）")
    ap.add_argument("--img-current", default=None,
                    help="现行 SHP 渲染图 PNG（可选，用于文字掩膜；需与 --shp-current 配套）")
    ap.add_argument("--shp-current", default=None,
                    help="现行乡镇 SHP（可选，配合 --img-current 做文字掩膜）")
    ap.add_argument("--crs", default="EPSG:32649", help="正向制图坐标系，默认 EPSG:32649")
    ap.add_argument("--min-region-px", type=int, default=50,
                    help="最小白色区域面积(px²)，默认 50")
    ap.add_argument("--min-keep-m2", type=float, default=5e4,
                    help="过滤小于该面积的碎屑(㎡)，默认 5e4")
    ap.add_argument("--snap-m", type=float, default=40.0,
                    help="外沿缝隙归属邻居的距离(m)，默认 40")
    ap.add_argument("--close-iter", type=int, default=2, help="形态学闭运算迭代，默认 2")
    ap.add_argument("--name-map", default=None,
                    help="code→名称 的 JSON 文件（可选）")
    ap.add_argument("--out", required=True, help="输出前缀（不含扩展名）")
    ap.add_argument("--no-albers", action="store_true", help="不输出源坐标系版本")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    log("=" * 70)
    log("  手绘 PNG → 乡镇级 SHP 反向映射")
    log("=" * 70)

    name_map = {}
    if args.name_map and os.path.exists(args.name_map):
        with open(args.name_map, encoding="utf-8") as f:
            name_map = {str(k): str(v) for k, v in json.load(f).items()}
        log(f"  名称映射：{len(name_map)} 条")

    # ---------- 读权威 SHP ----------
    auth = gpd.read_file(args.shp, encoding=args.shp_encoding)
    src_crs = auth.crs
    auth["geometry"] = auth.geometry.apply(safe_geom)
    auth["_c6"] = auth[args.code_field].astype(str).str[:6]
    county = auth[auth["_c6"] == str(args.county)].copy()
    if county.empty:
        raise SystemExit(f"✗ 权威 SHP 中无县码 {args.county} 的要素")
    county["_code"] = county[args.code_field].astype(str).str[:args.group_len]
    county = county[~county.geometry.is_empty & county.geometry.notna()].copy()
    log(f"  县 {args.county}：{len(county)} 个村级要素，"
        f"{county['_code'].nunique()} 个 {args.group_len} 位码")

    # 投影到制图坐标系
    county = county.to_crs(args.crs)
    groups = county.dissolve(by="_code")
    minx, miny, maxx, maxy = groups.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny

    img = load_gray(args.img)
    if img is None:
        raise SystemExit(f"✗ 无法读取图像 {args.img}")
    H, W = img.shape
    step_x, step_y = geo_w / W, geo_h / H
    log(f"  图幅 {W}×{H}，范围 {geo_w:.1f}×{geo_h:.1f} m，"
        f"步长 {step_x:.4f}×{step_y:.4f} m/px")
    log(f"  四至 [{minx:.1f}, {miny:.1f}, {maxx:.1f}, {maxy:.1f}]")

    def px2geo(i, j):
        return minx + (i + 0.5) * step_x, maxy - (j + 0.5) * step_y

    outer = unary_union([g for g in groups.geometry if g is not None and not g.is_empty]).buffer(0)
    log(f"  权威外陆地面积：{outer.area / 1e6:.3f} km²")

    # 权威外海岸线（只取外框，避免把权威内部旧界线混入线网）
    shp_outer = np.zeros((H, W), np.uint8)
    rasterize_ring(shp_outer, outer, minx, miny, maxy, geo_w, geo_h, W, H)
    shp_outer = (shp_outer == 255).astype(np.uint8)

    dark = (img == 0).astype(np.uint8)

    # ---------- 可选：文字掩膜 ----------
    if args.img_current and args.shp_current:
        cur = gpd.read_file(args.shp_current, encoding="utf-8").to_crs(args.crs)
        cur = cur[cur[args.code_field].astype(str).str[:6] == str(args.county)]
        cur = cur.copy()
        cur["_code"] = cur[args.code_field].astype(str).str[:args.group_len]
        cur_lines = np.zeros((H, W), np.uint8)
        for g in cur.dissolve(by="_code").geometry:
            rasterize_ring(cur_lines, safe_geom(g), minx, miny, maxy, geo_w, geo_h, W, H)
        cur_lines = (cur_lines == 255).astype(np.uint8)
        cur_dark = (load_gray(args.img_current) == 0).astype(np.uint8)
        k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        thick = cv2.dilate(cur_lines, k3, iterations=2)
        text = ((cur_dark == 1) & (thick == 0)).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(text, 8)
        keep = np.zeros_like(text)
        kept = 0
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] <= 4000:
                keep[lab == i] = 1
                kept += 1
        text = keep
        log(f"  文字掩膜：{kept} 个小组件，{int(text.sum())} px")
        lines2002 = ((dark == 1) & (text == 0)).astype(np.uint8)
    else:
        lines2002 = dark

    # 海岸线兜底
    lines2002 = np.maximum(lines2002, shp_outer).astype(np.uint8)
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    net = cv2.morphologyEx(lines2002, cv2.MORPH_CLOSE, k3, iterations=args.close_iter)
    log(f"  线网像素：{int(net.sum())}")

    # ---------- 白色区域洪泛 + EDT 最近区域归属（无缝无叠精确分区） ----------
    # 先把线网膨胀 1px 成实墙，8 连通取白色区域；给每个区域分配种子；
    # 再用欧氏距离变换把墙/缝隙像素归给最近区域 —— 保证全域无缝、无叠、唯一归属。
    net_thick = cv2.dilate(net, k3, iterations=1)
    white = (net_thick == 0).astype(np.uint8)
    n_reg, reg_label, reg_stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)

    seed = np.zeros((H, W), np.int32)
    nid = 0
    for i in range(1, n_reg):
        area = int(reg_stats[i, cv2.CC_STAT_AREA])
        if area < args.min_region_px:
            continue
        x0 = int(reg_stats[i, cv2.CC_STAT_LEFT]); y0 = int(reg_stats[i, cv2.CC_STAT_TOP])
        wb = int(reg_stats[i, cv2.CC_STAT_WIDTH]); hb = int(reg_stats[i, cv2.CC_STAT_HEIGHT])
        if x0 == 0 or y0 == 0 or x0 + wb >= W or y0 + hb >= H:
            continue                                   # 触边 = 海面，不作种子
        nid += 1
        seed[reg_label == i] = nid
    log(f"  白色区域：{n_reg - 1}（陆地种子 {nid}）")

    from scipy import ndimage
    _, inds = ndimage.distance_transform_edt(seed == 0, return_indices=True)
    assigned = seed[inds[0], inds[1]]

    # 权威陆地掩膜（只保留陆地像素，海面剔除）
    outer_mask = np.zeros((H, W), np.uint8)
    _parts = list(outer.geoms) if outer.geom_type == "MultiPolygon" else [outer]
    for pg in _parts:
        ext = np.array(pg.exterior.coords)
        pxp = (ext[:, 0] - minx) / geo_w * W
        pyp = (maxy - ext[:, 1]) / geo_h * H
        cv2.fillPoly(outer_mask, [np.stack([pxp, pyp], 1).astype(np.int32)], 255)
        for hole in pg.interiors:
            hc = np.array(hole.coords)
            pxp = (hc[:, 0] - minx) / geo_w * W
            pyp = (maxy - hc[:, 1]) / geo_h * H
            cv2.fillPoly(outer_mask, [np.stack([pxp, pyp], 1).astype(np.int32)], 0)
    land_mask = outer_mask > 0

    def contour_to_geo(c):
        return [px2geo(int(p[0]), int(p[1])) for p in c[:, 0, :]]

    polys = []
    for rid in range(1, nid + 1):
        mask = ((assigned == rid) & land_mask).astype(np.uint8) * 255
        if int(mask.sum()) // 255 < args.min_region_px:
            continue
        contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not contours or hier is None:
            continue
        hier = hier[0]
        for ci, c in enumerate(contours):
            if hier[ci][3] != -1:                      # 内环：稍后作为孔洞
                continue
            if cv2.contourArea(c) < args.min_region_px:
                continue
            ext = contour_to_geo(c)
            if len(ext) < 3:
                continue
            holes = []
            child = hier[ci][2]
            while child != -1:
                hc = contour_to_geo(contours[child])
                if len(hc) >= 3:
                    holes.append(hc)
                child = hier[child][0]
            poly = Polygon(ext, holes).buffer(0)
            if poly.is_empty or poly.area <= 0:
                continue
            polys.append(poly)
    log(f"  区域多边形：{len(polys)}")

    # ---------- 与权威外陆地求交 ----------
    land = []
    for p in polys:
        inter = p.intersection(outer).buffer(0)
        if inter.is_empty or inter.area <= 0:
            continue
        if inter.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        land.append(inter)
    log(f"  裁剪后：{len(land)}  合计 {sum(p.area for p in land) / 1e6:.3f} km²")

    # ---------- 缝隙填充 ----------
    kept = [p for p in land if p.area > args.min_keep_m2]
    kept = sorted(kept, key=lambda p: -p.area)
    missing = outer.difference(unary_union(kept))
    if not missing.is_empty:
        frags = list(missing.geoms) if missing.geom_type == "MultiPolygon" else [missing]
        frags = [f for f in frags if f.area > 0]
        assigned = 0
        for f in sorted(frags, key=lambda p: -p.area):
            best, bestov = None, 0.0
            fb = f.buffer(args.snap_m, quad_segs=1)
            for k, p in enumerate(kept):
                ov = p.intersection(fb).area
                if ov > bestov:
                    bestov, best = ov, k
            if best is not None:
                kept[best] = kept[best].union(f)
                assigned += 1
        log(f"  缝隙填充：{len(frags)} 段，归属 {assigned}，"
            f"残余 {outer.difference(unary_union(kept)).area / 1e6:.3f} km²")
    else:
        log("  缝隙填充：无缝隙")

    leftover = outer.difference(unary_union(kept))
    if not leftover.is_empty:
        lf = list(leftover.geoms) if leftover.geom_type == "MultiPolygon" else [leftover]
        kept.extend(p for p in lf if p.area > 0)
        log(f"  补回海岛：{sum(1 for p in lf if p.area > 0)} 个")
    final = kept

    # ---------- 属性继承 ----------
    towns = groups.reset_index()
    rows = []
    for ridx, p in enumerate(final):
        best_code, best_ov = None, 0.0
        for _, r in towns.iterrows():
            ov = p.intersection(r.geometry).area
            if ov > best_ov:
                best_ov, best_code = ov, str(r["_code"])
        nm = name_map.get(str(best_code), "")
        rows.append((ridx + 1, "" if best_code is None else best_code, nm,
                     round(p.area / 1e6, 4)))
    res = gpd.GeoDataFrame(
        {"OBJECTID": [r[0] for r in rows],
         "乡镇码": [r[1] for r in rows],
         "乡镇名": [r[2] for r in rows],
         "面积km2": [r[3] for r in rows]},
        geometry=final, crs=args.crs)

    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)
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
    log(f"  写出：{out_base}.shp / .geojson"
        f"{'' if args.no_albers else ' / _Albers.shp'}")

    # ---------- 校验 ----------
    rend = render_polys(final, minx, maxy, geo_w, geo_h, W, H)
    net_bool = (net > 0).astype(np.uint8)
    dt = cv2.distanceTransform(255 - net_bool * 255, cv2.DIST_L2, 3)
    dpx = dt[rend]
    log(f"  校验：渲染 {int(np.count_nonzero(rend))} px")
    for tol in (1.0, 1.5, 2.0, 3.0):
        log(f"    距线网 ≤{tol}px：{(dpx <= tol).mean():.3f}")
    log(f"    平均 {dpx.mean():.2f}px  中位 {np.median(dpx):.2f}px")
    log("DONE")


if __name__ == "__main__":
    main()
