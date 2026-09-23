# -*- coding: utf-8 -*-
"""
fix_jiaji_tails.py —— 嘉积镇「大尾巴就近划转 + 微碎屑清零」
=====================================================================
在 qionghai2002_final.shp 之上：
  1) 微碎屑：嘉积镇 MultiPolygon 中所有独立小部件(面积 < --frag-a m²)，
     按「共享边界最长，否则质心最近」就近划给相邻乡镇 ——
     解决上一轮 fix_jiaji_slivers（阈值 3000 m²）漏掉的 65 块碎屑。
  2) 大尾巴：光栅距离变换取 局部宽度<--width 的细带连通片，
     凡「最长弦 > --tail-chord 且 非核心舌端占比≥15%」者判定为留片尾巴，
     整片从嘉积主块切下，同样就近划入贴邻乡镇。
  3) 纯矢量布运算：划出面积 == 划入面积，严格守恒、零重叠、
     非法几何归零。（泊水乡/温泉镇/中原镇/… 作为接收乡镇按共享边择优）

用法：
  python fix_jiaji_tails.py
  python fix_jiaji_tails.py --out qionghai2002_slim --width 60 --tail-chord 600
输出：<out>.shp（EPSG:32649）+ <out>_Albers.shp（源 CRS）
     + <out>_QA报告.md + <out>_overlay.png + <out>_sidebyside.png + <out>_划转面积.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import math
import itertools
import os

import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely import make_valid, get_parts

HERE = os.path.dirname(os.path.abspath(__file__))
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")


def log(m=""):
    print(m, flush=True)


def parts_of(g):
    if g is None or g.is_empty:
        return []
    return [p for p in get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]


def repair(g):
    if g is None or g.is_empty:
        return None
    if g.is_valid:
        return g
    for m in ("structure", "linework"):
        try:
            x = make_valid(g, method=m)
        except Exception:
            try:
                x = make_valid(g)
            except Exception:
                continue
        if x is not None and not x.is_empty and x.area > 0 and abs(x.area - g.area) < g.area:
            return x
    return g.buffer(0)


def longest_chord(coords):
    best = 0.0
    for a, b in itertools.combinations(coords, 2):
        d = math.hypot(a[0] - b[0], a[1] - b[1])
        if d > best:
            best = d
    return best


def assign_neighbor(g, o_geoms, tol):
    """共享边界最长，否则质心最近。返回 (乡镇名, 公共边长度)。"""
    pb = g.boundary.buffer(tol)
    best, bl = None, 0.0
    for nm, og in o_geoms.items():
        try:
            L = pb.intersection(og.boundary.buffer(tol)).length
        except Exception:
            L = 0.0
        if L > bl:
            bl, best = L, nm
    if best is not None and bl > 0:
        return best, bl
    c = g.representative_point()
    best, dmin = None, None
    for nm, og in o_geoms.items():
        d = c.distance(og if not og.is_empty else og)
        if dmin is None or d < dmin:
            dmin, best = d, nm
    return best, 0.0


def find_tail_components(jj, core, cell, r_thr, area_min, chord_min, tongue_frac_min):
    """光栅法找「长尾巴细带」：

    thin = 局部宽度<2*r_thr 的细胞；core=raster 内距≥r_thr。
    tongue = thin 中 8 邻域无 core 的细胞（真悬舌/死端）。
    对 thin 的每个连通片：最长弦>chord_min 且 tongue 占比>=tongue_frac_min
    => 长尾巴，返回 (面积, 最长弦, 舌端占比, 矢量多边形)。
    """
    b = jj.bounds
    minx, miny, maxx, maxy = b[0], b[1], b[2], b[3]
    W = int((maxx - minx) / cell) + 1
    H = int((maxy - miny) / cell) + 1
    mask = np.zeros((H, W), np.uint8)

    def to_px(xs, ys):
        xs = np.asarray(xs, float)
        ys = np.asarray(ys, float)
        return np.stack([(xs - minx) / cell, (maxy - ys) / cell], 1).astype(np.int32)

    for p in parts_of(jj):
        cv2.fillPoly(mask, [to_px(p.exterior.xy[0], p.exterior.xy[1])], 1)
        for hole in p.interiors:
            cv2.fillPoly(mask, [to_px(hole.xy[0], hole.xy[1])], 0)

    dt = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 3)
    R = dt * cell
    is_core = R >= r_thr
    thin = (mask == 1) & (R < r_thr)
    kd = cv2.dilate(is_core.astype(np.uint8), np.ones((3, 3), np.uint8), 1) > 0
    tongue = thin & (~kd)

    n, lab, st, _ = cv2.connectedComponentsWithStats(thin.astype(np.uint8) * 255, 8)

    def pxcell(r, c):
        x0 = minx + c * cell
        yt = maxy - r * cell
        return Polygon([(x0, yt), (x0 + cell, yt), (x0 + cell, yt - cell), (x0, yt - cell)])

    out = []
    for i in range(1, n):
        a_m2 = int(st[i, cv2.CC_STAT_AREA]) * cell * cell
        if a_m2 < area_min:
            continue
        ys, xs = np.nonzero(lab == i)
        pts = []
        for y, x in zip(ys, xs):
            if tongue[y, x]:
                pts.append((minx + x * cell, maxy - y * cell))
        tongue_frac = len(pts) / max(len(xs), 1.0)
        # 快速上限：bbox 对角线
        yy0, yy1 = ys.min(), ys.max()
        xx0, xx1 = xs.min(), xs.max()
        chord_hi = math.hypot((xx1 - xx0) * cell, (yy1 - yy0) * cell)
        if chord_hi < chord_min:
            continue
        if tongue_frac < tongue_frac_min:
            continue
        u = unary_union([pxcell(int(y), int(x)) for y, x in zip(ys, xs)])
        if not u.is_valid:
            u = u.buffer(0)
        u = u.intersection(jj)
        if u.is_empty or u.area <= 0:
            continue
        # 真实最长弦(边界点集)
        cpts = [p0.exterior.coords for p0 in parts_of(u)]
        flat = [x for ring in cpts for x in ring]
        chord = longest_chord(flat)
        if chord < chord_min:
            continue
        out.append((a_m2, chord, tongue_frac, u))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="嘉积镇大尾巴+微碎屑就近划转")
    ap.add_argument("--in", dest="inp", default=os.path.join(HERE, "qionghai2002_final.shp"))
    ap.add_argument("--out", default=os.path.join(HERE, "qionghai2002_slim"))
    ap.add_argument("--town", default="嘉积镇")
    ap.add_argument("--img", default=os.path.join(HERE, "Qionghai2002.png"))
    ap.add_argument("--crs", default="EPSG:32649")
    ap.add_argument("--width", type=float, default=60.0, help="判定细带的最大局部宽度(m)")
    ap.add_argument("--cell-m", type=float, default=10.0, help="光栅像元(m)")
    ap.add_argument("--frag-a", type=float, default=10000.0,
                    help="独立小部件面积阈值(m²)，以下视为微碎屑划走")
    ap.add_argument("--tail-chord", type=float, default=600.0,
                    help="细带最长弦阈值(m)，以上视为长尾巴")
    ap.add_argument("--tail-min-area", type=float, default=1000.0)
    ap.add_argument("--tail-tongue", type=float, default=0.15,
                    help="候选细带中舌端(非贴核)细胞的占比下限")
    ap.add_argument("--no-albers", action="store_true")
    args = ap.parse_args(argv)

    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)

    g = gpd.read_file(args.inp, encoding="utf-8")
    ref = gpd.read_file(os.path.join(HERE, "qionghai.shp"), encoding="utf-8")
    src_crs = ref.crs
    g = g.to_crs(args.crs)
    g["geometry"] = g.geometry.apply(lambda x: repair(x) if x is not None else x)

    town = args.town
    t_idx = [i for i, r in g.iterrows() if r["TOWN"] == town]
    if len(t_idx) == 0:
        raise SystemExit(f"✗ 未找到乡镇 {town}")
    jj = repair(unary_union([g.loc[i, "geometry"] for i in t_idx]).buffer(0))

    others = g[~g.index.isin(t_idx)]
    o_geoms = {r["TOWN"]: r.geometry for _, r in others.iterrows()}

    # ---------- 1) 微碎屑：独立小部件 ----------
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    main_blk = parts[0]
    frags = [p for p in parts[1:] if p.area < args.frag_a]
    left_parts = [p for p in parts[1:] if p.area >= args.frag_a]
    log(f"嘉积主块 {main_blk.area/1e6:.4f} km²；独立小部件 {len(parts)-1} 块，"
        f"其中微碎屑 {len(frags)} 块（合计 {sum(p.area for p in frags)/1e4:.4f} ha）；"
        f"其余独立块(>=1ha) {len(left_parts)} 块")

    frag_moves = []   # (poly, neighbor, shared_len)
    for p in frags:
        nm, bl = assign_neighbor(p, o_geoms, args.width / 2)
        frag_moves.append((p, nm, bl))

    # ---------- 2) 大尾巴 ----------
    tails = find_tail_components(main_blk, None, args.cell_m,
                                 args.width / 2.0, args.tail_min_area,
                                 args.tail_chord, args.tail_tongue)
    log(f"长尾巴细带 {len(tails)} 条：")
    tail_moves = []
    for a_m2, chord, tfrac, geom in tails:
        # 沿外缘缓冲半个像元，吞掉细胞折线台阶，避免与矢量边做差产生细缝
        gcut = geom.buffer(args.cell_m * 0.5).intersection(main_blk)
        if gcut.is_empty or gcut.area <= 0:
            gcut = geom
        nm, bl = assign_neighbor(gcut, o_geoms, args.width / 2)
        tail_moves.append((gcut, nm, bl))
        log(f"   {a_m2/1e4:7.2f} ha(实{gcut.area/1e4:7.2f}) 弦长{chord/1e3:.2f}km"
            f" 舌端{tfrac*100:.0f}% 就近→ {nm}  公共边 {bl/1e3:.2f} km")

    # ---------- 3) 划转 ----------
    cut_union = unary_union([g0 for g0, _, _ in frag_moves] +
                            [g0 for g0, _, _ in tail_moves]).buffer(0) \
        if (frag_moves or tail_moves) else None
    jj_area0 = jj.area
    jj_new = repair(main_blk.difference(cut_union).buffer(0)) if cut_union is not None else main_blk

    # 清扫：切完残留的独立小块(< frag_a)也一律划走（吸收切缝噪点）
    sweep_moves = []
    jj_parts = sorted(parts_of(jj_new), key=lambda p: -p.area)
    keep_parts = [jj_parts[0]]
    for p in jj_parts[1:]:
        if p.area < args.frag_a:
            nm, bl = assign_neighbor(p, o_geoms, args.width / 2)
            sweep_moves.append((p, nm, bl))
        else:
            keep_parts.append(p)
    if sweep_moves:
        log(f"切后清扫残余小块 {len(sweep_moves)} 块（合计 {sum(p.area for p,_,_ in sweep_moves)/1e4:.4f} ha）")
        for p, nm, bl in sweep_moves:
            log(f"   {p.area/1e4:7.2f} ha → {nm}")
    if left_parts:
        keep_parts = keep_parts + left_parts
    jj_new = repair(unary_union(keep_parts).buffer(0))
    n_parts = len(parts_of(jj_new))
    log(f"\n嘉积余块 {jj_area0/1e6:.4f} → {jj_new.area/1e6:.4f} km²"
        f"（划出 {(jj_area0-jj_new.area)/1e6:.4f} km²；剩余独立块 {n_parts}）")

    assign = {}
    for g0, nm, bl in frag_moves + tail_moves + sweep_moves:
        assign.setdefault(nm, []).append(g0)
    updates = []
    for nm, pols in assign.items():
        old = o_geoms[nm]
        new = repair(unary_union([old] + pols).buffer(0))
        updates.append((nm, old, new, sum(p.area for p in pols)))

    # ---------- 4) 组装 ----------
    assign_geom = {nm: new for nm, old, new, _ in updates}
    rows = []
    for _, r in g.iterrows():
        base = dict(code=str(r["CODE"]), town=r["TOWN"], city=r.get("CITY", ""),
                    en=r.get("EN", ""), nfeat=int(r.get("N_FEAT", 0) or 0),
                    source=r["SOURCE"])
        if r["TOWN"] == town:
            base["geom"] = jj_new
        else:
            base["geom"] = assign_geom.get(r["TOWN"], r.geometry)
        rows.append(base)

    gdf = gpd.GeoDataFrame(
        {"CODE": [r["code"] for r in rows], "TOWN": [r["town"] for r in rows],
         "CITY": [r["city"] for r in rows], "EN": [r["en"] for r in rows],
         "N_FEAT": [r["nfeat"] for r in rows], "SOURCE": [r["source"] for r in rows]},
        geometry=[r["geom"] for r in rows], crs=args.crs)
    gdf["geometry"] = gdf.geometry.apply(lambda x: repair(x) if x is not None else x)
    gdf = gdf[~gdf.geometry.is_empty].reset_index(drop=True)
    gdf["AREA_KM2"] = (gdf.geometry.area / 1e6).round(3)

    # ---------- 5) 校验 ----------
    geoms = list(gdf.geometry)
    ua = unary_union(geoms).buffer(0).area / 1e6
    tot = gdf.geometry.area.sum() / 1e6
    cur = g.geometry.area.sum() / 1e6
    mx = 0.0
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if geoms[i].intersects(geoms[j]):
                mx = max(mx, geoms[i].intersection(geoms[j]).area)
    log(f"面积：原 {cur:.6f} → 现 {tot:.6f} km²，并 {ua:.6f}，最大重叠 {mx:.3f} m²，"
        f"非法 {int((~gdf.geometry.is_valid).sum())}")
    for nm, old, new, din in updates:
        log(f"   {nm}：{old.area/1e6:.4f} → {new.area/1e6:.4f} km² (+{din/1e6:.4f})")

    # ---------- 6) 写出 ----------
    for ext in SIDECARS:
        p = out_base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    gdf.to_file(out_base + ".shp", encoding="utf-8")
    log(f"写出：{out_base}.shp")
    if (not args.no_albers) and src_crs is not None and str(src_crs) != args.crs:
        gdf.to_crs(src_crs).to_file(out_base + "_Albers.shp", encoding="utf-8")
        log(f"写出：{out_base}_Albers.shp")

    # ---------- 7) QA ----------
    report = out_base + "_QA报告.md"
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 嘉积镇大尾巴/微碎屑划转 QA 报告\n\n")
        f.write(f"- 输入：`{os.path.abspath(args.inp)}`\n")
        f.write(f"- 输出：`{out_base}.shp`\n")
        f.write(f"- 细带判定：局部宽度 < {args.width:g} m，舌端占比 ≥ {args.tail_tongue:g}，"
                f"最长弦 ≥ {args.tail_chord:g} m，最小 {args.tail_min_area:g} m²\n")
        f.write(f"- 微碎屑阈值：独立小部件 < {args.frag_a:g} m²\n")
        f.write(f"- 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.write(f"嘉积镇：{jj_area0/1e6:.4f} km² → {jj_new.area/1e6:.4f} km²"
                f"（划出 {(jj_area0-jj_new.area)/1e6:.4f} km² = {(jj_area0-jj_new.area)/1e4:.2f} ha）\n\n")
        f.write("### 微碎屑划转\n\n| 块数 | 合计 ha |\n|---:|---:|\n")
        f.write(f"| {len(frag_moves)} | {sum(p.area for p, _, _ in frag_moves)/1e4:.4f} |\n")
        f.write("\n### 大尾巴划转\n\n| 面积 ha | 弦长 km | 舌端占比 | 就近划入 | 公共边 km |\n")
        f.write("|---|---:|---:|---|---:|\n")
        for g0, nm, bl in tail_moves:
            a_m2 = g0.area
            cpts = [x for ring in [p.exterior.coords for p in parts_of(g0)] for x in ring]
            chord = longest_chord(cpts)
            f.write(f"| {a_m2/1e4:.2f} | {chord/1e3:.2f} | - | {nm} | {bl/1e3:.2f} |\n")
        f.write("\n### 接收乡镇\n\n| 乡镇 | 划转前 km² | 划转后 km² | 划入 km² |\n|---|---:|---:|---:|\n")
        for nm, old, new, din in updates:
            f.write(f"| {nm} | {old.area/1e6:.4f} | {new.area/1e6:.4f} | {din/1e6:.4f} |\n")
        f.write(f"\n- 总面积：{cur:.6f} → {tot:.6f} km²（守恒量 {tot-cur:.6f}）\n")
        f.write(f"- 两两最大重叠 {mx:.3f} m²，非法几何 {int((~gdf.geometry.is_valid).sum())}\n")
    log(f"报告：{report}")

    # CSV
    csv_path = out_base + "_划转面积.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["TOWN", "划转前km2", "划转后km2", "净变化km2"])
        for nm, old, new, din in updates:
            w.writerow([nm, round(old.area/1e6, 6), round(new.area/1e6, 6),
                        round((new.area-old.area)/1e6, 6)])
        w.writerow([town, round(jj_area0/1e6, 6), round(jj_new.area/1e6, 6),
                    round((jj_new.area-jj_area0)/1e6, 6)])
    log(f"CSV：{csv_path}")

    # ---------- 8) 出图 ----------
    try:
        img = cv2.imdecode(np.fromfile(args.img, dtype=np.uint8), cv2.IMREAD_COLOR) \
            if os.path.exists(args.img) else None
        minx0, miny0, maxx0, maxy0 = gdf.total_bounds
        gw, gh = maxx0-minx0, maxy0-miny0
        if img is None or img.shape[0] == 0:
            img = np.full((int(gh/20), int(gw/20), 3), 255, np.uint8)
        Hh, Ww = img.shape[:2]
        if Hh == 0 or Ww == 0:
            Hh, Ww = int(gh/20), int(gw/20)
            img = np.full((Hh, Ww, 3), 255, np.uint8)

        def to_pts(geom):
            out = []
            for p in parts_of(geom):
                c = np.array(p.exterior.coords)
                pts = np.stack([(c[:, 0]-minx0)/gw*Ww, (maxy0-c[:, 1])/gh*Hh], 1)
                if len(pts) >= 3:
                    out.append(pts.astype(np.int32))
            return out

        ov = img.copy()
        for _, r in g.iterrows():
            col = (0, 0, 255) if r["TOWN"] == town else (0, 170, 0)
            for pts in to_pts(r.geometry):
                cv2.polylines(ov, [pts], True, col, 3, cv2.LINE_AA)
        for g0, nm, bl in tail_moves:
            for pts in to_pts(g0):
                cv2.polylines(ov, [pts], True, (255, 128, 0), 3, cv2.LINE_AA)
        for g0, nm, bl in frag_moves:
            for pts in to_pts(g0):
                cv2.polylines(ov, [pts], True, (128, 255, 255), 2, cv2.LINE_AA)
        cv2.imencode(".png", ov)[1].tofile(out_base + "_overlay.png")

        fills0 = np.full((Hh, Ww, 3), 255, np.uint8)
        fills1 = np.full((Hh, Ww, 3), 255, np.uint8)
        for _, r in g.iterrows():
            col = (0, 0, 255) if r["TOWN"] == town else (235, 235, 235)
            for pts in to_pts(r.geometry):
                cv2.fillPoly(fills0, [pts], col)
                cv2.polylines(fills0, [pts], True, (0, 0, 0), 1, cv2.LINE_8)
        for _, r in gdf.iterrows():
            col = (0, 0, 255) if r["TOWN"] == town else (235, 235, 235)
            for pts in to_pts(r.geometry):
                cv2.fillPoly(fills1, [pts], col)
                cv2.polylines(fills1, [pts], True, (0, 0, 0), 1, cv2.LINE_8)
        cv2.imencode(".png", np.hstack([fills0, fills1]))[1].tofile(out_base + "_sidebyside.png")
        log(f"出图：{out_base}_overlay.png / _sidebyside.png")
    except Exception as e:
        log(f"  ⚠ 出图失败：{e}")
    log("DONE")


if __name__ == "__main__":
    main()