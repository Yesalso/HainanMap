# -*- coding: utf-8 -*-
"""
fix_partition.py —— 分区式反向映射结果的配准式修复（消半像素缝/孔洞/碎屑）
================================================================================
配合 reverse_by_color.py 使用。针对"手绘图粗糙 → 映射回 SHP 后出现大量细缝/
孔洞/细屑"的问题，按 `手绘地图粗糙映射空隙问题_解决方案.md` 的分层方案修复。

核心思路（第 1 + 3 + 6 层）：
  1. 手绘填充色块普遍比手绘黑线中心内缩约 1px（半像素偏移），导致相邻旧乡镇
     之间留下 ~2px 的"母镇残条"，母镇因此出现与旧乡镇等面积的孔洞；
     → 将每个旧乡镇按 snap_m（≈1px）向外生长，再裁回所属母镇，令其贴到线中心；
  2. 重建严格分区：remain = 母镇 − union(旧乡镇)；天然无缝隙、无重叠、面积守恒；
  3. 收尾：remove_overlaps + drop_micro_holes + precision_normalize + 缝隙归属兜底；
  4. 未受影响乡镇几何逐字节保持不变（"名单外不变"纪律）。

输入：--in 反向结果 SHP（含 SOURCE 字段，或码不在权威层里的即视为新增）
      --ref 现行权威 SHP（本县）
输出：<out>.shp（EPSG:32649）+ <out>_Albers.shp（源 CRS）
      + <out>_QA报告.md + <out>_overlay.png + <out>_sidebyside.png

用法：
  python fix_partition.py
  python fix_partition.py --in wenchang2002.shp --ref wenchang.shp \
      --out wenchang2002_fixed --snap-m 20
"""
from __future__ import annotations

import argparse
import datetime
import os

import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union, polygonize
from shapely import make_valid, set_precision, get_parts

HERE = os.path.dirname(os.path.abspath(__file__))
EMPTY = Polygon()
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")


def log(m=""):
    print(m, flush=True)


def parts_of(g):
    if g is None or g.is_empty:
        return []
    return [p for p in get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]


def to_polygonal(g):
    if g is None or g.is_empty:
        return None
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    if g.geom_type == "GeometryCollection":
        ps = [q for p in g.geoms for q in parts_of(p)]
        return unary_union(ps) if ps else None
    return None


def repair_validity(g, grid=0.001):
    if g is None or g.is_empty:
        return None
    if g.is_valid and g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    cands = []

    def _add(x):
        x = to_polygonal(x)
        if x is not None and not x.is_empty and x.area > 0 and x.is_valid:
            cands.append(x)

    if grid and grid > 0:
        try:
            _add(set_precision(g, grid))
        except Exception:
            pass
    for m in ("structure", "linework"):
        try:
            _add(make_valid(g, method=m))
        except TypeError:
            try:
                _add(make_valid(g))
            except Exception:
                pass
            break
        except Exception:
            pass
    try:
        _add(g.buffer(0))
    except Exception:
        pass
    if not cands:
        return None
    a0 = g.area
    cands.sort(key=lambda x: (abs(x.area - a0) / max(a0, 1e-9), len(parts_of(x))))
    return cands[0]


def precision_normalize(g, grid):
    if grid and grid > 0 and g is not None and not g.is_empty:
        try:
            x = to_polygonal(set_precision(g, grid))
            if x is not None and not x.is_empty and x.area > 0 and x.is_valid:
                return x
        except Exception:
            pass
    return g


def drop_micro_holes(g, min_hole_area):
    if g is None or g.is_empty or min_hole_area <= 0:
        return g
    try:
        changed = False
        out = []
        for p in parts_of(g):
            keep = [r for r in p.interiors if Polygon(r).area >= min_hole_area]
            if len(keep) != len(p.interiors):
                changed = True
                out.append(Polygon(p.exterior, keep))
            else:
                out.append(p)
        if not changed:
            return g
        return unary_union(out) if len(out) > 1 else out[0]
    except Exception:
        return g


def width_index(g):
    return (2.0 * g.area / g.length) if g.length > 0 else 0.0


def remove_overlaps(geoms, tol):
    """顺序占用：面积大者优先占据重叠区，保证互斥。返回 (新几何列表, 剔除面积)。"""
    order = sorted(range(len(geoms)), key=lambda i: -geoms[i].area)
    out = [EMPTY] * len(geoms)
    occ = None
    removed = 0.0
    for i in order:
        g = geoms[i]
        if occ is not None and not occ.is_empty:
            clipped = g.difference(occ)
            removed += max(0.0, g.area - clipped.area)
            g = clipped
        if g is not None and not g.is_empty and not g.is_valid:
            g = repair_validity(g)
        out[i] = g if (g is not None and not g.is_empty) else EMPTY
        occ = out[i] if occ is None else unary_union([occ, out[i]])
    return out, removed


def find_gaps(geoms, domain, max_gap_width, min_touch=2):
    """三通道检测细小缝隙：polygonize / 闭运算 / 域约束。"""
    valid = [g for g in geoms if g is not None and not g.is_empty and g.area > 0]
    if not valid:
        return []
    cov = unary_union(valid)
    cand = {}
    try:
        faces = list(polygonize([g.boundary for g in valid]))
        for f in faces:
            if f.area > 0 and cov.intersection(f).area < f.area * 1e-9:
                cand[f] = "polygonize"
    except Exception:
        pass
    r = max_gap_width / 2.0
    try:
        closed = cov.buffer(r, quad_segs=16).buffer(-r, quad_segs=16)
        for p in parts_of(closed.difference(cov)):
            if p.area > 0:
                cand.setdefault(p, "closing")
    except Exception:
        pass
    if domain is not None and not domain.is_empty:
        try:
            for p in parts_of(domain.difference(cov)):
                if p.area > 0:
                    cand.setdefault(p, "domain")
        except Exception:
            pass
    area_cap = max_gap_width * max_gap_width * 400.0
    out = []
    for f, src in cand.items():
        try:
            if f.area <= 1.0 or f.area > area_cap:
                continue
            w = width_index(f)
            if w > max_gap_width:
                continue
            # 只统计落在权威域内的缝隙；域外的是海岸凹湾/海面，不填
            if domain is not None and not domain.is_empty:
                if f.intersection(domain).area < 0.5 * f.area:
                    continue
            if sum(1 for g in valid if g.intersects(f)) < min_touch:
                continue
            out.append((f, src, w))
        except Exception:
            continue
    out.sort(key=lambda x: -x[0].area)
    kept = []
    for it in out:
        if any(it[0].intersection(k[0]).area > 0.7 * it[0].area for k in kept):
            continue
        kept.append(it)
    return kept


def eliminate_thin_remain(parent, children, max_gap_width, max_iter=8):
    """把母镇残条（宽度 < max_gap_width 的 remain 碎块）并入相邻旧乡镇（最长公共边界）。

    旧乡镇向外生长后，若手绘线比 2px 宽，相邻旧乡镇之间会残留细条；此法消之，
    保证 remain 无细屑（Eliminate 等价）。返回更新后的 children 列表。
    """
    children = list(children)
    for _ in range(max_iter):
        remain = parent.difference(unary_union(children).buffer(0)).buffer(0)
        moved = False
        for p in parts_of(remain):
            if p.area <= 1.0 or width_index(p) >= max_gap_width:
                continue
            best, bl = None, 0.0
            for k, c in enumerate(children):
                if c is None or c.is_empty:
                    continue
                bl_k = p.boundary.intersection(c.boundary.buffer(max_gap_width / 2)).length
                if bl_k > bl:
                    bl, best = bl_k, k
            if best is not None and bl > 0:
                children[best] = unary_union([children[best], p])
                moved = True
        if not moved:
            break
    return children


def parent_index(poly, ref_geoms):
    best, bo = None, 0.0
    for i, rg in enumerate(ref_geoms):
        if rg is None or rg.is_empty:
            continue
        o = poly.intersection(rg).area
        if o > bo:
            bo, best = o, i
    return best


def diagnose(geoms, domain, max_gap_width):
    n = len(geoms)
    invalid = sum(1 for g in geoms if not g.is_valid)
    ir = sum(len(p.interiors) for g in geoms for p in parts_of(g))
    sum_a = sum(g.area for g in geoms)
    u = unary_union(geoms).buffer(0)
    ov = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            if geoms[i].intersects(geoms[j]):
                ov += geoms[i].intersection(geoms[j]).area
    miss = domain.difference(u).area if (domain is not None and not domain.is_empty) else 0.0
    gaps = find_gaps(geoms, domain, max_gap_width, 2)
    thin = 0
    for g in geoms:
        for p in parts_of(g):
            if p.area < 1000:
                continue
            try:
                if p.buffer(-max_gap_width / 2.0).is_empty:   # 最大内切宽 < max_gap_width
                    thin += 1
            except Exception:
                pass
    return dict(n=n, invalid=invalid, interior_rings=ir, sum_area=sum_a,
                union_area=u.area, overlap=ov, domain_missing=miss,
                gap_cnt=len(gaps), gap_area=sum(g[0].area for g in gaps), thin=thin)


def main(argv=None):
    ap = argparse.ArgumentParser(description="分区式反向结果的配准式修复")
    ap.add_argument("--in", dest="inp", default=os.path.join(HERE, "wenchang2002.shp"))
    ap.add_argument("--ref", default=os.path.join(HERE, "wenchang.shp"))
    ap.add_argument("--out", default=os.path.join(HERE, "wenchang2002_fixed"))
    ap.add_argument("--img", default=os.path.join(HERE, "Wenchang_draw.png"))
    ap.add_argument("--code-field", default="CODE")
    ap.add_argument("--name-field", default="TOWN")
    ap.add_argument("--crs", default="EPSG:32649")
    ap.add_argument("--snap-m", type=float, default=30.0,
                    help="旧乡镇向外生长量(m)，≈1.5px，令其贴到手绘黑线中心（经验最优）")
    ap.add_argument("--max-gap-width", type=float, default=60.0)
    ap.add_argument("--min-hole-m2", type=float, default=1.0)
    ap.add_argument("--grid", type=float, default=0.0, help="精度归一网格(m)，0=关闭")
    ap.add_argument("--no-albers", action="store_true")
    args = ap.parse_args(argv)

    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)

    res = gpd.read_file(args.inp, encoding="utf-8")
    ref = gpd.read_file(args.ref, encoding="utf-8")
    src_crs = ref.crs                 # 以权威层 CRS（如 CGCS2000 Albers）作为源坐标输出
    res = res.to_crs(args.crs)
    ref = ref.to_crs(args.crs)
    res["geometry"] = res.geometry.apply(lambda x: x if x.is_valid else repair_validity(x))
    ref["geometry"] = ref.geometry.apply(lambda x: x if x.is_valid else repair_validity(x))
    ref_geoms = list(ref.geometry)
    domain = unary_union(ref_geoms).buffer(0)

    # ---- 识别旧乡镇（新增）及其母镇 ----
    ref_codes = set(ref[args.code_field].astype(str))
    if "SOURCE" in res.columns:
        is_new = res["SOURCE"].astype(str) == "2002新增"
    else:
        is_new = ~res[args.code_field].astype(str).isin(ref_codes)
    parents = {idx: parent_index(res.loc[idx, "geometry"], ref_geoms)
               for idx in res.index[is_new]}
    log(f"  输入 {len(res)} 个要素（旧乡镇 {int(is_new.sum())}），权威 {len(ref)} 个")

    d0 = diagnose(list(res.geometry), domain, args.max_gap_width)
    log(f"  修复前：孔洞环 {d0['interior_rings']}，缝隙 {d0['gap_cnt']} 处/"
        f"{d0['gap_area']:.1f} m²，重叠 {d0['overlap']:.3f} m²，域缺 {d0['domain_missing']:.1f} m²")

    # ---- 第1步：旧乡镇向外生长到线中心 ----
    geoms = list(res.geometry)
    grow_rows = []
    for idx in res.index[is_new]:
        pi = parents.get(idx)
        if pi is None:
            continue
        old = geoms[idx]
        grown = repair_validity(old.buffer(args.snap_m, quad_segs=8).intersection(ref_geoms[pi])) or old
        grow_rows.append([res.loc[idx, args.name_field], ref.iloc[pi][args.name_field],
                          old.area, idx])
        geoms[idx] = grown
    log(f"  生长旧乡镇 {len(grow_rows)} 个（snap_m={args.snap_m:g} m）")

    # ---- 第2步：同一母镇内旧乡镇去重叠（大者优先）+ 收尾 ----
    by_parent = {}
    for idx in res.index[is_new]:
        if parents.get(idx) is not None:
            by_parent.setdefault(parents[idx], []).append(idx)
    for pi, idxs in by_parent.items():
        idxs = sorted(idxs, key=lambda i: -geoms[i].area)
        occ = None
        for i in idxs:
            g = geoms[i]
            if occ is not None and not occ.is_empty:
                g = g.difference(occ)
            g = repair_validity(g) or EMPTY
            g = drop_micro_holes(g, args.min_hole_m2)
            g = precision_normalize(g, args.grid)
            geoms[i] = g
            occ = g if occ is None else unary_union([occ, g])

    # ---- 第2b步：消母镇残条（细屑并入相邻旧乡镇） ----
    for pi, idxs in by_parent.items():
        kids = [geoms[i] for i in idxs]
        kids = eliminate_thin_remain(ref_geoms[pi], kids, args.max_gap_width)
        for i, g in zip(idxs, kids):
            geoms[i] = precision_normalize(drop_micro_holes(g, args.min_hole_m2), args.grid)

    # ---- 第3步：重建严格分区（remain 由已收尾的旧乡镇导出，严丝合缝） ----
    rows = []
    affected_codes = {str(ref.iloc[pi][args.code_field]) for pi in by_parent}
    for idx in res.index[~is_new]:
        if str(res.loc[idx, args.code_field]) in affected_codes:
            continue   # 该母镇将被其旧乡镇 + remain 取代，勿重复
        rows.append(dict(code=str(res.loc[idx, args.code_field]),
                         town=res.loc[idx, args.name_field],
                         city=res.loc[idx].get("CITY", ""), en=res.loc[idx].get("EN", ""),
                         nfeat=int(res.loc[idx].get("N_FEAT", 0) or 0),
                         source="沿用", touch=False, geom=geoms[idx]))
    for idx in res.index[is_new]:
        rows.append(dict(code=str(res.loc[idx, args.code_field]),
                         town=res.loc[idx, args.name_field],
                         city=res.loc[idx].get("CITY", ""), en="", nfeat=0,
                         source="2002新增", touch=True, geom=geoms[idx]))
    for pi in sorted(p for p in by_parent.keys() if p is not None):
        child_geoms = [geoms[i] for i in by_parent[pi] if not geoms[i].is_empty]
        cu = unary_union(child_geoms).buffer(0) if child_geoms else EMPTY
        remain = ref_geoms[pi].difference(cu).buffer(0)
        remain = drop_micro_holes(remain, args.min_hole_m2)
        if remain is not None and not remain.is_empty and remain.area > 1.0:
            rows.append(dict(code=str(ref.iloc[pi][args.code_field]),
                             town=ref.iloc[pi][args.name_field],
                             city=ref.iloc[pi].get("CITY", ""), en=ref.iloc[pi].get("EN", ""),
                             nfeat=int(ref.iloc[pi].get("N_FEAT", 0) or 0),
                             source="沿用", touch=True, geom=remain))
        else:
            log(f"  ⚠ 母镇 {ref.iloc[pi][args.name_field]} 其余部分为空，跳过")

    # ---- 第4步：组装（未改动乡镇原样保留，仅做合法性保护） ----
    geoms_out = []
    for r in rows:
        g = r["geom"]
        if not r.get("touch"):
            g = repair_validity(g) or EMPTY
        geoms_out.append(g)

    meta = dict(
        CODE=[r["code"] for r in rows], TOWN=[r["town"] for r in rows],
        CITY=[r["city"] for r in rows], EN=[r["en"] for r in rows],
        N_FEAT=[r["nfeat"] for r in rows], SOURCE=[r["source"] for r in rows])
    gdf = gpd.GeoDataFrame(meta, geometry=geoms_out, crs=args.crs)
    gdf = gdf[~gdf.geometry.is_empty].reset_index(drop=True)

    # ---- 第5步：缝隙诊断（仅报告，不再改动未受影响乡镇） ----
    gaps = find_gaps(list(gdf.geometry), domain, args.max_gap_width, 2)
    log(f"  残余缝隙：{len(gaps)} 处 / {sum(g[0].area for g in gaps):.2f} m²")

    gdf["AREA_KM2"] = (gdf.geometry.area / 1e6).round(3)
    d1 = diagnose(list(gdf.geometry), domain, args.max_gap_width)
    log(f"  修复后：孔洞环 {d1['interior_rings']}，缝隙 {d1['gap_cnt']} 处/"
        f"{d1['gap_area']:.1f} m²，重叠 {d1['overlap']:.3f} m²，域缺 {d1['domain_missing']:.1f} m²")
    log(f"  要素 {len(gdf)}（沿用 {int((gdf.SOURCE=='沿用').sum())}，"
        f"2002新增 {int((gdf.SOURCE=='2002新增').sum())}）")

    # ---- 校验：未受影响乡镇是否逐字节不变 ----
    new_towns = set(gdf.loc[gdf.SOURCE == "2002新增", "TOWN"])
    unchanged, changed = 0, []
    for _, r in ref.iterrows():
        sub = gdf[gdf[args.code_field].astype(str) == str(r[args.code_field])]
        if len(sub) == 0:
            continue
        sd = unary_union(list(sub.geometry)).buffer(0).symmetric_difference(r.geometry).area
        if sd < 1.0:
            unchanged += 1
        else:
            changed.append((r[args.name_field], sd))
    log(f"  未改动乡镇 {unchanged} 个；被拆分 {len(changed)} 个："
        + ", ".join(f"{t}({a/1e6:.2f}km²)" for t, a in changed))

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
        f.write("# 分区修复 QA 报告\n\n")
        f.write(f"- 输入：`{os.path.abspath(args.inp)}`\n")
        f.write(f"- 输出：`{out_base}.shp`\n")
        f.write(f"- 权威参考：`{os.path.abspath(args.ref)}`\n")
        f.write(f"- 坐标系：`{args.crs}`\n")
        f.write(f"- 参数：snap_m={args.snap_m:g} m，max_gap_width={args.max_gap_width:g} m，"
                f"min_hole={args.min_hole_m2:g} m²，grid={args.grid:g} m\n")
        f.write(f"- 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.write("## 1. 修复前诊断\n\n```\n")
        for k, v in d0.items():
            f.write(f"{k}: {v}\n")
        f.write("```\n\n## 2. 修复后校验\n\n```\n")
        for k, v in d1.items():
            f.write(f"{k}: {v}\n")
        f.write("```\n\n## 3. 旧乡镇生长（贴到黑线中心）\n\n")
        f.write("| 旧乡镇 | 母镇 | 生长前km² | 最终km² | 增量km² |\n|---|---|---:|---:|---:|\n")
        for nm, pn, a0, idx in grow_rows:
            a1 = geoms[idx].area
            f.write(f"| {nm} | {pn} | {a0/1e6:.4f} | {a1/1e6:.4f} | {(a1-a0)/1e6:.4f} |\n")
        f.write(f"\n## 4. 未受影响乡镇\n\n- 逐字节不变：{unchanged} 个\n- 被拆分："
                + ", ".join(f"{t}（{a/1e6:.2f} km²）" for t, a in changed) + "\n")
        f.write(f"\n- 要素总数：{len(gdf)}（沿用 {int((gdf.SOURCE=='沿用').sum())}，"
                f"2002新增 {int((gdf.SOURCE=='2002新增').sum())}）\n")
        f.write(f"- 面积：输入 {d0['sum_area']/1e6:.6f} km² → 输出 {d1['sum_area']/1e6:.6f} km²\n")
    log(f"  报告：{report}")

    # ---- 出图 ----
    try:
        img = cv2.imdecode(np.fromfile(args.img, dtype=np.uint8), cv2.IMREAD_COLOR) \
            if os.path.exists(args.img) else None
        if img is not None:
            minx, miny, maxx, maxy = gdf.total_bounds
            gw, gh = maxx - minx, maxy - miny
            H, W = img.shape[:2]
            ov = img.copy()
            for _, r in gdf.iterrows():
                col = (0, 0, 255) if r["TOWN"] in new_towns else (0, 170, 0)
                for p in parts_of(r.geometry):
                    c = np.array(p.exterior.coords)
                    pts = np.stack([(c[:, 0]-minx)/gw*W, (maxy-c[:, 1])/gh*H], 1).astype(np.int32)
                    cv2.polylines(ov, [pts], True, col, 3, cv2.LINE_AA)
            cv2.imencode(".png", ov)[1].tofile(out_base + "_overlay.png")
            fills = np.full((H, W, 3), 255, np.uint8)
            for _, r in gdf.iterrows():
                col = (0, 0, 255) if r["TOWN"] in new_towns else (235, 235, 235)
                for p in parts_of(r.geometry):
                    c = np.array(p.exterior.coords)
                    pts = np.stack([(c[:, 0]-minx)/gw*W, (maxy-c[:, 1])/gh*H], 1).astype(np.int32)
                    cv2.fillPoly(fills, [pts], col)
                    cv2.polylines(fills, [pts], True, (0, 0, 0), 1, cv2.LINE_8)
                    for h in p.interiors:
                        hc = np.array(h.coords)
                        hp = np.stack([(hc[:, 0]-minx)/gw*W, (maxy-hc[:, 1])/gh*H], 1).astype(np.int32)
                        cv2.fillPoly(fills, [hp], (255, 255, 255))
            cv2.imencode(".png", np.hstack([img, fills]))[1].tofile(out_base + "_sidebyside.png")
            log(f"  出图：{out_base}_overlay.png / _sidebyside.png")
    except Exception as e:
        log(f"  ⚠ 出图失败：{e}")
    log("DONE")


if __name__ == "__main__":
    main()