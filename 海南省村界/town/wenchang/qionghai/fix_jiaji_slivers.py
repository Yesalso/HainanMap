# -*- coding: utf-8 -*-
"""
fix_jiaji_slivers.py —— 嘉积镇细缝就近划归（Eliminate 窄条，保留核心）
=====================================================================
在手绘 12 色反向映射结果（qionghai2002_fixed.shp）之上：
  1) 向量法识别嘉积镇(沿用)余块中的真正细窄部分：
     core = 嘉积镇.buffer(---width/2)；浅带 = 嘉积镇 - core。
     浅带中"不接触 core"的孤立块 = 可并入邻居的细窄碎片
     （宽带的边缘环带仍贴着 core，属于边界，予以保留；核心原封不动）。
  2) 每个细窄碎片按"共享边界最长"（否则按质心最近）就近划给相邻乡镇
     （泮水乡/上埇乡/温泉镇/中原镇 等，非嘉积镇）。
  3) 嘉积镇 = 原几何 - 碎片；目标乡镇 = 原几何 ∪ 碎片。
     纯矢量布运算，划出面积 == 划入面积，严格守恒，零重叠。

用法：
  python fix_jiaji_slivers.py
  python fix_jiaji_slivers.py --width 100 --min-area-m2 5000 --out qionghai2002_final
输出：<out>.shp（EPSG:32649）+ <out>_Albers.shp（源 CRS）
      + <out>_QA报告.md + <out>_overlay.png + <out>_sidebyside.png + <out>_划转面积.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os

import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Point, Polygon
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
        except TypeError:
            try:
                x = make_valid(g)
            except Exception:
                continue
        except Exception:
            continue
        if x is not None and not x.is_empty and x.area > 0 and abs(x.area - g.area) < g.area:
            return x
    return g.buffer(0)


def slivers_of(jj, r_thr, min_area, cell=15.0):
    """光栅内切半径法：返回需划入邻居的细窄碎片（矢量，按像素方块精确成型）。

    1) 将 嘉积镇 栅格化为 cell 分辨率，cv2 距离变换得内切半径 R；
    2) shallow = R < r_thr；core = R >= r_thr；
    3) 把"8 邻域内无 core 像素"的 shallow 像元视为真正细窄碎片（舌端/孤立窄瓣），
       其余浅带（贴着 core 或彼此连通到 core 的边缘环带）保留；core 本身保留。
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
    core = R >= r_thr
    shallow = (mask == 1) & (R < r_thr)
    kd = cv2.dilate(core.astype(np.uint8), np.ones((3, 3), np.uint8), 1) > 0
    thin = shallow & (~kd)

    n, lab, st, _ = cv2.connectedComponentsWithStats(thin.astype(np.uint8) * 255, 8)

    ytops = maxy - np.arange(H) * cell

    def pxcell(r, c):
        x0 = minx + c * cell
        yt = ytops[r]
        return Polygon([(x0, yt), (x0 + cell, yt), (x0 + cell, yt - cell), (x0, yt - cell)])

    frags = []
    for i in range(1, n):
        a_m2 = int(st[i, cv2.CC_STAT_AREA]) * cell * cell
        if a_m2 < min_area:
            continue
        ys, xs = np.nonzero(lab == i)
        u = unary_union([pxcell(int(y), int(x)) for y, x in zip(ys, xs)])
        if not u.is_valid:
            u = u.buffer(0)
        u = u.intersection(jj)          # 贴紧 嘉积镇，杜绝越界
        if u.is_empty or u.area <= 0:
            continue
        frags.append(u)

    # 将碎片按连通片拆开（intersection 可能拆成多片），每片 >= min_area 独立处理
    out = []
    for f in frags:
        for p in parts_of(f):
            if p.area >= min_area:
                out.append(p)
    return out


def assign_neighbor(frag, o_geoms, tol):
    pb = frag.boundary.buffer(tol)
    best, bl = None, 0.0
    for nm, og in o_geoms.items():
        try:
            L = pb.intersection(og.boundary.buffer(tol)).length
        except Exception:
            L = 0.0
        if L > bl:
            bl, best = L, nm
    if best is not None:
        return best, bl
    # 兜底：质心最近
    c = frag.representative_point()
    best, dmin = None, None
    for nm, og in o_geoms.items():
        d = c.distance(og if not og.is_empty else og)
        if dmin is None or d < dmin:
            dmin, best = d, nm
    return best, 0.0


def main(argv=None):
    ap = argparse.ArgumentParser(description="嘉积镇细缝就近划归")
    ap.add_argument("--in", dest="inp", default=os.path.join(HERE, "qionghai2002_fixed.shp"))
    ap.add_argument("--out", default=os.path.join(HERE, "qionghai2002_final"))
    ap.add_argument("--town", default="嘉积镇")
    ap.add_argument("--img", default=os.path.join(HERE, "Qionghai2002.png"))
    ap.add_argument("--crs", default="EPSG:32649")
    ap.add_argument("--width", type=float, default=60.0,
                    help="判定细窄的最大局部宽度(m)：小于此值即细缝")
    ap.add_argument("--cell-m", type=float, default=15.0, help="光栅像元(m)")
    ap.add_argument("--min-area-m2", type=float, default=3000.0, help="忽略更小的噪碎屑(m²)")
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
    if len(t_idx) > 1:
        raise SystemExit(f"✗ 乡镇 {town} 有 {len(t_idx)} 个要素，需手工处理")
    i0 = t_idx[0]
    jj = g.loc[i0, "geometry"]

    others = g[g.index != i0]
    o_geoms = {r["TOWN"]: r.geometry for _, r in others.iterrows()}

    r_thr = args.width / 2.0
    moved = slivers_of(jj, r_thr, args.min_area_m2, cell=args.cell_m)
    log(f"  嘉积镇 {jj.area/1e6:.4f} km²，细窄判定 局部宽度<{args.width:g} m")
    log(f"  细窄碎片 {len(moved)} 块，合计 {sum(p.area for p in moved)/1e6:.4f} km²")

    rows_move = []
    for poly in moved:
        nm, bl = assign_neighbor(poly, o_geoms, r_thr)
        rows_move.append((poly, nm, bl))
        log(f"    {poly.area/1e4:7.1f} ha  就近→ {nm}  公共边 {bl/1e3:6.1f} km")

    # ---- 划转 ----
    jj_area0 = jj.area
    if rows_move:
        moved_u = unary_union([r[0] for r in rows_move]).buffer(0)
        jj_new = repair(jj.difference(moved_u).buffer(0))
    else:
        jj_new = jj
    assign = {}
    for poly, nm, bl in rows_move:
        assign.setdefault(nm, []).append(poly)

    updates = []
    for nm, pols in assign.items():
        old = o_geoms[nm]
        new = repair(unary_union([old] + pols).buffer(0))
        updates.append((nm, old, new, sum(p.area for p in pols)))

    # ---- 组装新表 ----
    assign_geom = {nm: new for nm, old, new, _ in updates}
    rows = []
    for _, r in g.iterrows():
        base = dict(code=str(r["CODE"]), town=r["TOWN"], city=r.get("CITY", ""),
                    en=r.get("EN", ""), nfeat=int(r.get("N_FEAT", 0) or 0),
                    source=r["SOURCE"])
        base["geom"] = jj_new if r["TOWN"] == town else assign_geom.get(r["TOWN"], r.geometry)
        rows.append(base)

    gdf = gpd.GeoDataFrame(
        {"CODE": [r["code"] for r in rows], "TOWN": [r["town"] for r in rows],
         "CITY": [r["city"] for r in rows], "EN": [r["en"] for r in rows],
         "N_FEAT": [r["nfeat"] for r in rows], "SOURCE": [r["source"] for r in rows]},
        geometry=[r["geom"] for r in rows], crs=args.crs)
    gdf["geometry"] = gdf.geometry.apply(lambda x: repair(x) if x is not None else x)
    gdf = gdf[~gdf.geometry.is_empty].reset_index(drop=True)
    gdf["AREA_KM2"] = (gdf.geometry.area / 1e6).round(3)

    # ---- 校验 ----
    geoms = list(gdf.geometry)
    ua = unary_union(geoms).buffer(0).area / 1e6
    tot = gdf.geometry.area.sum() / 1e6
    cur = g.geometry.area.sum() / 1e6
    mx = 0.0
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if geoms[i].intersects(geoms[j]):
                mx = max(mx, geoms[i].intersection(geoms[j]).area)
    log(f"  面积：原 {cur:.6f} → 现 {tot:.6f} km²，并 {ua:.6f}，最大重叠 {mx:.3f} m²，"
        f"非法 {int((~gdf.geometry.is_valid).sum())}")
    for nm, old, new, din in updates:
        log(f"    {nm}：{old.area/1e6:.4f} → {new.area/1e6:.4f} km² (+{din/1e6:.4f})")

    # ---- 写出 ----
    for ext in SIDECARS:
        p = out_base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    gdf.to_file(out_base + ".shp", encoding="utf-8")
    log(f"  写出：{out_base}.shp")
    if (not args.no_albers) and src_crs is not None and str(src_crs) != args.crs:
        gdf.to_crs(src_crs).to_file(out_base + "_Albers.shp", encoding="utf-8")
        log(f"  写出：{out_base}_Albers.shp")

    # ---- QA 报告 ----
    report = out_base + "_QA报告.md"
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 嘉积镇细缝划转 QA 报告\n\n")
        f.write(f"- 输入：`{os.path.abspath(args.inp)}`\n")
        f.write(f"- 输出：`{out_base}.shp`\n")
        f.write(f"- 细窄判定：局部宽度 < {args.width:g} m（buffer(-{args.width/2:g})取 core），"
                f"最小碎片 {args.min_area_m2:g} m²\n")
        f.write(f"- 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.write(f"嘉积镇余块：{jj_area0/1e6:.4f} km² → {jj_new.area/1e6:.4f} km²"
                f"（划出 {(jj_area0-jj_new.area)/1e6:.4f} km²）\n\n")
        f.write("| 碎片 | 面积 km² | 就近划入 | 公共边 km |\n|---|---:|---|---:|\n")
        for poly, nm, bl in rows_move:
            f.write(f"| 细窄碎片 | {poly.area/1e6:.4f} | {nm} | {bl/1e3:.2f} |\n")
        f.write("\n| 目标乡镇 | 划转前 km² | 划转后 km² | 划入 km² |\n|---|---:|---:|---:|\n")
        for nm, old, new, din in updates:
            f.write(f"| {nm} | {old.area/1e6:.4f} | {new.area/1e6:.4f} | {din/1e6:.4f} |\n")
        f.write(f"\n- 总面积：{cur:.6f} → {tot:.6f} km²（守恒量 {tot-cur:.6f}）\n")
        f.write(f"- 两两最大重叠 {mx:.3f} m²，非法几何 {int((~gdf.geometry.is_valid).sum())}\n")
    log(f"  报告：{report}")

    # CSV
    csv_path = out_base + "_划转面积.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["TOWN", "划转前km2", "划转后km2", "净变化km2"])
        for nm, old, new, din in updates:
            w.writerow([nm, round(old.area / 1e6, 6), round(new.area / 1e6, 6),
                        round((new.area - old.area) / 1e6, 6)])
        w.writerow([town, round(jj_area0 / 1e6, 6), round(jj_new.area / 1e6, 6),
                    round((jj_new.area - jj_area0) / 1e6, 6)])
    log(f"  CSV：{csv_path}")

    # ---- 出图 ----
    try:
        img = cv2.imdecode(np.fromfile(args.img, dtype=np.uint8), cv2.IMREAD_COLOR) \
            if os.path.exists(args.img) else None
        minx0, miny0, maxx0, maxy0 = gdf.total_bounds
        gw, gh = maxx0 - minx0, maxy0 - miny0
        if img is None:
            img = np.full((int(gh / 20), int(gw / 20), 3), 255, np.uint8)
        Hh, Ww = img.shape[:2]
        if Hh == 0 or Ww == 0:
            Hh, Ww = int(gh / 20), int(gw / 20)
            img = np.full((Hh, Ww, 3), 255, np.uint8)

        def to_pts(geom):
            out = []
            for p in parts_of(geom):
                c = np.array(p.exterior.coords)
                pts = np.stack([(c[:, 0] - minx0) / gw * Ww, (maxy0 - c[:, 1]) / gh * Hh], 1)
                if len(pts) >= 3:
                    out.append(pts.astype(np.int32))
            return out

        ov = img.copy()
        for _, r in gdf.iterrows():
            col = (0, 0, 255) if r["TOWN"] == town else (0, 170, 0)
            for pts in to_pts(r.geometry):
                cv2.polylines(ov, [pts], True, col, 3, cv2.LINE_AA)
        for poly in moved:
            for pts in to_pts(poly):
                cv2.polylines(ov, [pts], True, (255, 128, 0), 3, cv2.LINE_AA)
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
        for poly in moved:
            for pts in to_pts(poly):
                cv2.fillPoly(fills1, [pts], (0, 0, 255))
        cv2.imencode(".png", np.hstack([fills0, fills1]))[1].tofile(out_base + "_sidebyside.png")
        log(f"  出图：{out_base}_overlay.png / _sidebyside.png")
    except Exception as e:
        log(f"  ⚠ 出图失败：{e}")
    log("DONE")


if __name__ == "__main__":
    main()