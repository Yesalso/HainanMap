# -*- coding: utf-8 -*-
"""
按"名单乡镇采用 2002 海岸线、乡镇间内部界线一律不变"重建海口图层（仅海口）。

数据源：
  - town/Haikou_2002.png     手绘 2002 海口乡镇图（约 1:30px，即 30 米/像素）
  - 海南村界.shp             村级面 + HainanMap.xlsx（构建 base 画法，同 HainanTownShp.py）

名单（仅这些海口乡镇允许改动，且只改海岸线/外边界）：
  秀英街道(原秀英镇)、灵山镇、人民路街道、海甸街道、府城镇、城西镇、
  国兴街道、蓝天街道、白龙街道、白沙街道、中山街道、滨海街道、
  演丰镇、三江镇

做法（矢量 + 栅格结合，保证名单外完全不变）：
  1. base = 村级面归并出的 41 个海口乡镇（同 HainanTownShp.py 画法）；
  2. 每个乡镇的外边界弧 = 乡镇边界 ∩ 海口整体外轮廓（uu.boundary）；
     区分"海域海岸"与"邻县陆地界"（倾向海侧看是否邻县）；
  3. 名单乡镇的海域海岸弧：直接追踪 2002 图陆地轮廓（find_contours
     land2002）中落在该弧附近的一段作为新海岸线，端头钉回 base 节点；
  4. 列表外乡镇、邻县交界、所有内部界线：一律保留 base 精确边线。

输出：仅 town/海口2002_select.shp（41 个海口乡镇）+ 预览 PNG，不生成全省。
"""

import os
import sys
import math
import random
from collections import defaultdict

import cv2
import numpy as np
import pandas as pd
import geopandas as gpd
from affine import Affine
from rasterio.features import rasterize, shapes
from shapely.geometry import (LineString, MultiLineString, MultiPolygon, Point,
                              Polygon, shape)
from shapely.ops import polygonize, unary_union
from shapely.validation import make_valid
from skimage.measure import find_contours

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import HaikouFineTune as HFT  # 复用其几何/匹配机制与常量

# ==================== 配置 ====================
SELECT_NAMES = [
    "秀英镇", "灵山镇", "人民路街道", "海甸街道", "府城镇", "城西镇",
    "国兴街道", "蓝天路街道", "白龙街道", "白沙街道", "中山街道",
    "滨海街道", "演丰镇", "三江镇", "新埠街道",
]
NAME_ALIAS = {"秀英镇": "秀英街道", "蓝天路街道": "蓝天街道"}

OUT_SHP = os.path.join(HFT.TOWN_DIR, "海口2002_select.shp")
OUT_PREVIEW = os.path.join(HFT.TOWN_DIR, "海口2002_select_preview.png")
OUT_OVERVIEW = os.path.join(HFT.TOWN_DIR, "海口略图.png")

RECTIFY = 30.0          # 30 米/像素（与 Haikou_2002.png 一致）
CORRIDOR_PX = 60        # 海岸弧附近搜索 2002 海岸线的半径（像素）
SIMP_TOL = 22.0         # 简化容差（米）
LAND_SIMP = 40.0        # 2002 陆域多边形简化容差（米）
SNAP_M = 45.0           # 与 base 对齐容差：窄于该宽度的差异忽略，保留 base 顺滑线
WIGGLE_AMP = 30.0       # 边界小幅起伏振幅（米）
WIGGLE_WL = 600.0       # 起伏波长（米）
SEA_PROBE_M = 300.0     # 判定海/邻县的探针长度（米）
CHAIN_GAP = 20.0        # 同乡镇海岸碎片拼接的最大端点间距（米）
# ==============================================


def _open_region(g, r):
    """形态学开运算：去除窄于 2r 的细碎差异（栅格量化噪声）。"""
    if g is None or g.is_empty or r <= 0:
        return g
    try:
        e = g.buffer(-r, join_style=2, mitre_limit=2.0)
        return e.buffer(r, join_style=2, mitre_limit=2.0)
    except Exception:
        return g


def reconcile(b, land_poly, base_all, others_u, r=SNAP_M):
    """名单乡镇：只在差异宽于 2r 处按 2002 图切，其余保留 base 精确线。"""
    removed = _open_region(b.difference(land_poly), r)          # 图上是水、base 是陆
    # 图上是陆、base 是水，且不属于任何 base 单元/邻县（海口内一般为空）
    new_land = land_poly.difference(base_all).difference(others_u)
    added = _open_region(new_land.intersection(b.buffer(600.0)), r)
    return b.difference(removed).union(added)


def transfer_island(gdf, src, dst, max_area=0.3, max_dist=300.0):
    """把 src 乡镇中面积小、紧邻 dst 的离岛整块划归 dst。"""
    si = gdf.index[gdf.CODE == src][0]
    di = gdf.index[gdf.CODE == dst][0]
    sg, dg = gdf.at[si, "geometry"], gdf.at[di, "geometry"]
    parts = list(sg.geoms) if sg.geom_type == "MultiPolygon" else [sg]
    parts.sort(key=lambda p: -p.area)
    keep, move = [parts[0]], []
    for p in parts[1:]:
        if p.area / 1e6 <= max_area and p.distance(dg) <= max_dist:
            move.append(p)
        else:
            keep.append(p)
    if not move:
        return gdf
    gdf.at[si, "geometry"] = MultiPolygon(keep) if len(keep) > 1 else keep[0]
    gdf.at[di, "geometry"] = make_valid(unary_union([dg] + move)).buffer(0)
    print(f"  江心岛 {len(move)} 块由 {src} 划归 {dst}（{sum(p.area for p in move)/1e6:.4f} km²）")
    return gdf


def _densify_ring(coords, step):
    out = []
    for i in range(len(coords) - 1):
        ax, ay = coords[i]
        bx, by = coords[i + 1]
        d = math.hypot(bx - ax, by - ay)
        n = max(1, int(d // step))
        for k in range(n):
            t = k / n
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    out.append(coords[-1])
    return out


def _wiggle_ring(coords, others_net, amp, wl, phase):
    c = _densify_ring(coords, max(wl / 8.0, 1.0))
    n = len(c)
    if n < 4:
        return coords
    s = [0.0]
    for i in range(1, n):
        s.append(s[-1] + math.hypot(c[i][0] - c[i - 1][0], c[i][1] - c[i - 1][1]))
    new = []
    for i in range(n):
        x, y = c[i]
        i0, i1 = max(0, i - 1), min(n - 1, i + 1)
        dx, dy = c[i1][0] - c[i0][0], c[i1][1] - c[i0][1]
        dl = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / dl, dx / dl
        d_oth = others_net.distance(Point(x, y)) if others_net is not None else 1e9
        m = min(1.0, max(0.0, (d_oth - amp * 1.5) / (amp * 1.5)))   # 靠近他乡镇处不扰动
        off = amp * (0.6 * math.sin(2 * math.pi * s[i] / wl + phase)
                     + 0.4 * math.sin(2 * math.pi * s[i] / (wl * 0.41) + phase * 1.7)) * m
        new.append((x + nx * off, y + ny * off))
    return new


def _wiggle_geom(g, others_net, amp, wl, seed):
    rng = random.Random(seed)

    def proc(poly):
        ext = _wiggle_ring(list(poly.exterior.coords), others_net, amp, wl, rng.uniform(0, 6.283))
        if len(ext) < 4:
            return poly
        ints = []
        for r in poly.interiors:
            ir = _wiggle_ring(list(r.coords), others_net, amp, wl, rng.uniform(0, 6.283))
            if len(ir) >= 4:
                ints.append(ir)
        try:
            return Polygon(ext, ints).buffer(0)
        except Exception:
            return poly

    if g.geom_type == "Polygon":
        return proc(g)
    polys = []
    for p in g.geoms:
        r = proc(p)
        if r.is_empty:
            continue
        if r.geom_type == "Polygon":
            polys.append(r)
        else:
            polys.extend(list(r.geoms))
    return make_valid(unary_union(polys)).buffer(0)


def wiggle_towns(gdf, units, sel, others_u=None, amp=WIGGLE_AMP, wl=WIGGLE_WL):
    """给名单乡镇的自由边界加小幅度起伏，避免直线感；与邻界重合处不动。"""
    codes = list(units.CODE)
    bds = [units.geometry.iloc[j].boundary for j in range(len(units))]
    extra = [others_u] if others_u is not None and not others_u.is_empty else []
    for k in gdf.index:
        c = gdf.at[k, "CODE"]
        if c not in sel:
            continue
        i = codes.index(c)
        others = unary_union([bds[j] for j in range(len(units)) if j != i] + extra)
        gdf.at[k, "geometry"] = _wiggle_geom(gdf.at[k, "geometry"], others, amp, wl, seed=i)
    return gdf


def land_region_polygon(land, minx, maxy, sx, sy):
    """把 2002 纯黑线围成的陆域栅格转成矢量多边形。"""
    tr = Affine(sx, 0, minx, 0, -sy, maxy)
    geoms = [shape(g) for g, v in shapes(land.astype(np.uint8), mask=land, transform=tr)]
    if not geoms:
        return None
    poly = unary_union(geoms).buffer(0)
    return poly.simplify(LAND_SIMP, preserve_topology=True)


def pick_select_codes(units):
    codes = []
    for name in SELECT_NAMES:
        target = NAME_ALIAS.get(name, name)
        matches = units[units["TOWN"] == target]
        if matches.empty:
            matches = units[units["TOWN"].str.contains(target, na=False)]
        if matches.empty:
            print(f"  ⚠ 未找到 {name}，跳过")
            continue
        if len(matches) > 1:
            print(f"  ⚠ {name} 匹配到多个：{list(matches.CODE)}，取第一个")
        codes.append(matches.CODE.iloc[0])
    return set(codes)


def land2002_mask(dark):
    """从 2002 手绘线网提取"陆地区域"掩膜：白色且不与图像边框连通的区域。"""
    white = (dark == 0).astype(np.uint8)
    nreg, lab, stats, cent = cv2.connectedComponentsWithStats(white, connectivity=4)
    border = (set(lab[0, :]) | set(lab[-1, :])
              | set(lab[:, 0]) | set(lab[:, -1]))
    sea = {c for c in border if c != 0}
    land = (dark == 0) & (~np.isin(lab, list(sea)))
    return land


def iso_rings(land):
    """返回 2002 陆地轮廓环（skimage find_contours，每环为 (y,x) 浮点数组）。"""
    return [c for c in find_contours(land.astype(np.float32), 0.5) if len(c) >= 8]


def pixel_in_corridor(arc_px, H, W, radius):
    """arc 像素膨胀 radius 得到走廊掩膜。"""
    corr = np.zeros((H, W), np.uint8)
    pts = np.array(arc_px, dtype=np.int32).reshape(-1, 1, 2)
    cv2.polylines(corr, [pts], False, 1, 1)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    corr = cv2.dilate(corr, k)
    return corr > 0


def chain_fragments(parts, tol=8.0):
    """按端点距离把同乡镇海岸碎片拼成连续链（返回合并后的线）。"""
    res = []
    items = [(a, Point(a.coords[0]), Point(a.coords[-1])) for a in parts]
    used = set()
    for i in range(len(items)):
        if i in used:
            continue
        chain = list(items[i][0].coords)
        used.add(i)
        changed = True
        while changed:
            changed = False
            jbest, jmode, jd = None, None, 1e18
            for j in range(len(items)):
                if j in used:
                    continue
                a0, a1 = items[j][1], items[j][2]
                cand = [(a0, "head"), (a1, "tail")]
                for pt, mode in cand:
                    d = Point(chain[0]).distance(pt)
                    if d < jd:
                        jd, jbest, jmode = d, j, mode
                    d = Point(chain[-1]).distance(pt)
                    if d < jd:
                        jd, jbest, jmode = d, j, mode
            if jbest is not None and jd < tol:
                b = list(items[jbest][0].coords)
                if jmode == "head":
                    b = b[::-1]
                    chain = b + chain
                else:
                    chain = chain + b
                used.add(jbest)
                changed = True
        res.append(LineString(chain))
    return res


def trace_coast(arc, rings, corr, minx, maxy, sx, sy, W, H):
    """新海岸线 = 2002 陆地轮廓环上、处于 base 弧端点邻域之间的子链（吸附法）。"""
    p0, p1 = Point(arc.coords[0]), Point(arc.coords[-1])
    if p0.distance(p1) < HFT.SAMPLE_M:
        return None  # 闭环海岸保持 base（避免拓扑丢失单元）
    tol_m = CORRIDOR_PX * sx   # base 弧到 2002 轮廓的最大允许偏差（米）

    best = None
    for ring in rings:
        rr = np.asarray(ring, dtype=float)
        n = len(rr)
        if n < 8:
            continue
        row0, col0 = _py(p0, minx, maxy, sx, sy)
        row1, col1 = _py(p1, minx, maxy, sx, sy)
        d0 = (rr[:, 0] - row0) ** 2 + (rr[:, 1] - col0) ** 2
        d1 = (rr[:, 0] - row1) ** 2 + (rr[:, 1] - col1) ** 2
        if d0.min() > (CORRIDOR_PX * 1.5) ** 2 or d1.min() > (CORRIDOR_PX * 1.5) ** 2:
            continue
        i0, i1 = int(d0.argmin()), int(d1.argmin())
        if i0 == i1:
            continue

        # 环上两个方向的子链：正向 i0→i1，反向 i1→i0
        def walk(a, b):
            idx = list(range(a, b + 1)) if b > a else list(range(a, n)) + list(range(0, b + 1))
            return rr[np.array(idx) % n]

        for chain in (walk(i0, i1), walk(i1, i0)):
            if len(chain) < 3:
                continue
            iy = np.clip(chain[:, 0].astype(int), 0, H - 1)
            ix = np.clip(chain[:, 1].astype(int), 0, W - 1)
            if corr[iy, ix].mean() < 0.01:      # 与 base 弧完全无关的环段
                continue
            line = LineString([HFT.px2geo(minx, maxy, sx, sy, float(x), float(y))
                               for y, x in chain])
            if line.is_empty or line.length < 1e-6:
                continue
            if best is None or (abs(line.length - arc.length), _span(line, p0, p1)) < \
                    (abs(best.length - arc.length), _span(best, p0, p1)):
                best = line

    if best is None:
        return None
    if best.distance(p0) > tol_m or best.distance(p1) > tol_m:
        return None
    co = _orient(list(best.coords), p0, p1)
    co[0] = (p0.x, p0.y)
    co[-1] = (p1.x, p1.y)
    line = LineString(co)
    try:
        line = line.simplify(SIMP_TOL, preserve_topology=True)
    except Exception:
        pass
    if line.is_empty or line.length < 0.5 * arc.length:
        return None
    return line


def _py(pt, minx, maxy, sx, sy):
    col = (pt.x - minx) / sx - 0.5
    row = (maxy - pt.y) / sy - 0.5
    return float(row), float(col)


def _span(line, p0, p1):
    a = Point(line.coords[0]); b = Point(line.coords[-1])
    return a.distance(p0) + b.distance(p1)


def _orient(co, p0, p1):
    """使整条线的起点对齐 base 弧的 p0 方向。"""
    if Point(co[0]).distance(p0) + Point(co[-1]).distance(p1) <= \
       Point(co[-1]).distance(p0) + Point(co[0]).distance(p1):
        return co
    return co[::-1]


def main():
    # ---------- 1. base ----------
    units, code_map = HFT.build_base_haikou()
    assert len(units) > 20, "海口乡镇数量异常"
    sel = pick_select_codes(units)
    assert sel, "名单乡镇未匹配到任何单元"
    print(f"名单乡镇（{len(sel)} 个）：{sorted(sel)}")

    uu = unary_union(list(units.geometry)).buffer(0)
    others_u = HFT.build_others()
    print(f"海口 base 单元：{len(units)}，面积 {uu.area / 1e6:.2f} km²")

    # ---------- 2. 栅格与 2002 陆地轮廓 ----------
    minx, miny, maxx, maxy = uu.bounds
    g02 = cv2.imdecode(np.fromfile(HFT.IMG_2002, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert g02.shape == (HFT.CANVAS_H, HFT.CANVAS_W), g02.shape
    Wr = int(round((maxx - minx) / RECTIFY))
    Hr = int(round((maxy - miny) / RECTIFY))
    sx = (maxx - minx) / Wr
    sy = (maxy - miny) / Hr

    base_lab = rasterize(
        [(g, i + 1) for i, g in enumerate(units.geometry)],
        out_shape=(Hr, Wr), transform=Affine(sx, 0, minx, 0, -sy, maxy),
        fill=0, dtype="int32")
    dark_full = (g02 < 128)
    best_off, best_ov = (HFT.OX0, HFT.OY0), -1
    for dy_off in range(HFT.OY0 - 6, HFT.OY0 + 7):
        for dx_off in range(HFT.OX0 - 4, HFT.OX0 + 5):
            if dx_off < 0 or dy_off < 0 or dy_off + Hr > HFT.CANVAS_H or dx_off + Wr > HFT.CANVAS_W:
                continue
            ov = int((dark_full[dy_off:dy_off + Hr, dx_off:dx_off + Wr]
                      & HFT.img2edges(base_lab)).sum())
            if ov > best_ov:
                best_ov, best_off = ov, (dx_off, dy_off)
    OX, OY = best_off
    print(f"图纸偏移：({OX},{OY})，重合像素 {best_ov}")
    dark = dark_full[OY:OY + Hr, OX:OX + Wr].copy()
    land = land2002_mask(dark)
    print(f"2002 陆地像素 {int(land.sum())} / {dark.size}  暗线像素 {int(dark.sum())}")
    rings = iso_rings(land)
    print(f"2002 陆地轮廓环：{len(rings)} 个")

    # ---------- 3. 2002 纯黑线围成的陆域多边形 ----------
    land_poly = land_region_polygon(land, minx, maxy, sx, sy)
    print(f"2002 陆域多边形：{land_poly.area/1e6:.2f} km²")

    # ---------- 4. 名单乡镇 = base ∩ 2002 陆域（完全采纳黑线）；名单外 = base ----------
    nf = dict(zip(units.CODE, units.N_FEAT))
    rows = []
    for i in range(len(units)):
        c = units.CODE.iloc[i]
        b = units.geometry.iloc[i]
        if c in sel:
            g = reconcile(b, land_poly, uu, others_u)
            g = make_valid(g).buffer(0)
            if g.geom_type == "MultiPolygon":
                ps = [p for p in g.geoms if p.area > 1.0]   # 去退化碎片(<1 m²)
                if ps:
                    g = MultiPolygon(ps) if len(ps) > 1 else ps[0]
            if g.is_empty or g.area <= 0:
                g = b
        else:
            g = b
        info = code_map.get(c, {})
        rows.append({
            "CODE": c, "TOWN": info.get("town", ""),
            "CITY": info.get("city", "海口市"), "EN": info.get("en", ""),
            "N_FEAT": int(nf.get(c, 0)),
            "AREA_KM2": round(g.area / 1e6, 3), "geometry": g,
        })
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=HFT.TARGET_CRS)
    out = out[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
    out = out.sort_values("CODE").reset_index(drop=True)
    print(f"输出单元数：{len(out)}")

    # ---------- 4b. 白龙/灵山共管江心岛：整岛划归灵山 ----------
    out = transfer_island(out, "460108006", "460108101")

    # ---------- 4c. 名单乡镇边界加小幅起伏，避免直来直去 ----------
    out = wiggle_towns(out, units, sel, others_u)
    for k in out.index:
        if out.at[k, "CODE"] in sel:
            g = make_valid(out.at[k, "geometry"]).buffer(0)
            if g.geom_type == "MultiPolygon":
                ps = [p for p in g.geoms if p.area > 1.0]
                if ps:
                    g = MultiPolygon(ps) if len(ps) > 1 else ps[0]
            out.at[k, "geometry"] = g
    out["AREA_KM2"] = (out.geometry.area / 1e6).round(3)

    base_codes = set(units.CODE)
    out_codes = set(out["CODE"])
    if base_codes - out_codes:
        print("缺失单元：", sorted(base_codes - out_codes))
    if out_codes - base_codes:
        print("额外单元：", sorted(out_codes - base_codes))

    # ---------- 6. 名单外必须不变，名单内列出变化 ----------
    base_map = {c: g for c, g in zip(units.CODE, units.geometry)}
    changed = []
    broken = []

    def real_diff(g0, g1):
        return g0.symmetric_difference(g1).area / 1e6

    for _, r in out.iterrows():
        c = r["CODE"]
        if c in sel:
            d = real_diff(base_map[c], r.geometry)
            if d > 1e-4:
                changed.append((c, r["TOWN"], base_map[c].area / 1e6,
                                r.geometry.area / 1e6, d))
        else:
            bg = base_map.get(c)
            assert bg is not None
            d = real_diff(bg, r.geometry)
            if d > 1e-4:
                broken.append((c, r["TOWN"], bg.area / 1e6,
                               r.geometry.area / 1e6, d))
    if broken:
        print("\n⚠ 共有 {} 个名单外单元被改动：".format(len(broken)))
        for c, t, b, n, d in broken:
            print(f"   {c} {t:8s} base={b:8.3f} → new={n:8.3f} Δ={n-b:+.3f}"
                  f" 对称差={d:.4f} km²")
    print(f"\n名单内实际变化乡镇：{len(changed)} 个（海岸线）")
    for c, t, b, n, d in changed:
        print(f"   {c} {t:8s}  base={b:8.3f} → new={n:8.3f} km²  Δ={n - b:+.3f}"
              f"  对称差={d:.4f}")

    # ---------- 7. 质量检查 ----------
    bad = int((~out.geometry.is_valid).sum())
    print(f"有效几何：{len(out) - bad}/{len(out)}")
    fin_u = unary_union(list(out.geometry)).buffer(0)
    ovs = (fin_u.intersection(others_u).area if not others_u.is_empty else 0.0)
    print(f"与邻县重叠：{ovs / 1e6:.4f} km²")
    print(f"面积 base={uu.area/1e6:.2f}  final={fin_u.area/1e6:.2f} km²"
          f"  差值={(fin_u.area - uu.area)/1e6:+.2f} km²")

    src_prj = gpd.read_file(HFT.SHP_PATH, encoding=HFT.SHP_ENCODING, rows=1).crs
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = OUT_SHP[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    final = out.to_crs(src_prj)
    final.geometry = final.geometry.apply(make_valid)
    nb = int((~final.geometry.is_valid).sum())
    if nb:
        print(f"输出前修复无效几何：{nb} 个")
    final.to_file(OUT_SHP, encoding=HFT.OUT_ENCODING)
    print(f"已输出：{OUT_SHP}（仅海口 {len(final)} 个乡镇）")

    make_preview(out, units, OUT_PREVIEW)
    print(f"已输出预览：{OUT_PREVIEW}")

    make_overview(out, OUT_OVERVIEW)
    print(f"已输出海口略图：{OUT_OVERVIEW}")


def make_preview(out, units, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        from matplotlib import font_manager
        names = {f.name for f in font_manager.fontManager.ttflist}
        font = next((n for n in ("SimHei", "Microsoft YaHei", "SimSun") if n in names), None)
    except Exception:
        font = None
    fig, ax = plt.subplots(figsize=(12, 11), dpi=110)
    out.geometry.plot(ax=ax, facecolor="white", edgecolor="black", linewidth=0.6)
    units.geometry.boundary.plot(ax=ax, facecolor="none", edgecolor="red",
                                 linewidth=1.0, linestyle="--")
    ax.set_aspect("equal")
    ax.set_title("海口 2002 名单乡镇海岸线（黑=新，红虚线=原 base）", fontsize=13,
                 fontfamily=font)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def _cn_font():
    try:
        from matplotlib import font_manager
        names = {f.name for f in font_manager.fontManager.ttflist}
        return next((n for n in ("SimHei", "Microsoft YaHei", "SimSun") if n in names), None)
    except Exception:
        return None


def make_overview(out, out_path):
    """生成海口乡镇界略图（每次修改 shp 后输出）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm
    font = _cn_font()

    fig, ax = plt.subplots(figsize=(12, 11), dpi=140)
    cmap = plt.get_cmap("tab20")
    for i, (_, r) in enumerate(out.iterrows()):
        gpd.GeoSeries([r.geometry], crs=out.crs).plot(
            ax=ax, facecolor=cmap(i % 20), edgecolor="black", linewidth=0.4, alpha=0.85)
    # 海岸线加粗
    out.boundary.plot(ax=ax, facecolor="none", edgecolor="black", linewidth=0.5)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("海口市乡镇界略图", fontsize=16, fontfamily=font)

    # 比例尺 10 km
    minx, miny, maxx, maxy = out.total_bounds
    L = 10000.0
    x0 = minx + (maxx - minx) * 0.06
    y0 = miny + (maxy - miny) * 0.04
    ax.plot([x0, x0 + L], [y0, y0], color="black", lw=2.5)
    ax.text(x0 + L / 2, y0 + (maxy - miny) * 0.012, "10 km",
            ha="center", va="bottom", fontsize=10, fontfamily=font)
    # 指北针
    ax.annotate("N", xy=(maxx - (maxx - minx) * 0.06, maxy - (maxy - miny) * 0.06),
                xytext=(maxx - (maxx - minx) * 0.06, maxy - (maxy - miny) * 0.14),
                ha="center", va="center", fontsize=14, fontfamily=font,
                arrowprops=dict(facecolor="black", width=2, headwidth=8))
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


if __name__ == "__main__":
    main()