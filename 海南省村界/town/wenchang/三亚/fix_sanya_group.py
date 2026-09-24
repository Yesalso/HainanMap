# -*- coding: utf-8 -*-
"""
fix_sanya_group.py —— 按手绘图重划三亚“城芯6乡镇”内部区域（整组替换）
================================================================================
背景（依据 手绘地图映射SHP教程.md / 手绘地图粗糙映射空隙问题_解决方案.md）：
  荔枝沟镇、红沙镇、河东区、河西区、南海街道、田独镇 视为一个整体 U（外轮廓/海岸线/
  海岛归属不变），只按手绘图 Sanya1.png 重划 U 内部区域为 9 个新区域：
     牛岭乡 #EFE4B0 | 河西区 #00A2E8 | 六道乡 #99D9EA | 南海区 #3F48CC
     鹿回头区 #FF7F27 | 荔枝沟区 #A349A4 | 红沙区 #22B14C | 河东区 #B5E61D | 田独镇 #A4393A

做法（文昌经验的分区式反向映射 + 唯一归属）：
  1. 配准：手绘图与 sanya.shp 同一正向制图参数（EPSG:32649, 1px=20m, 四至=total_bounds），
     偏移 (0,0)，像素↔米制一一对应；
  2. 域 U = 6 乡镇并集（权威，含海岸线与海岛）；
  3. 反向：手绘彩色块 → 栅格种子；对全域做欧氏距离变换(EDT)，把 U 内每个像素
     唯一归属到最近种子 → 机制上无缝、无重叠、全覆盖（方案B：全域唯一归属）；
  4. 矢量：rasterio.features.shapes 按像素边提取（相邻区域天然共边），再 simplify；
  5. 修复：去重叠（大者优先）+ 残余缝隙/离岛按最长公共边界/最近距离归属 + 微孔清理；
  6. 输出：U 替换为 9 区域（名称/代码按 color_map_sanya.json），其余 10 乡镇逐字节不变。

用法：
  python fix_sanya_group.py
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import numpy as np
import cv2
import geopandas as gpd
import rasterio
from rasterio.features import shapes as rio_shapes, rasterize as rio_rasterize
from rasterio.transform import from_bounds
from shapely.geometry import shape, LineString, Polygon
from shapely.ops import unary_union
from shapely import make_valid

HERE = os.path.dirname(os.path.abspath(__file__))
WENCHANG = os.path.dirname(HERE)          # town/wenchang
if WENCHANG not in sys.path:
    sys.path.insert(0, WENCHANG)
import fix_partition as fp                 # noqa: E402  复用文昌修复工具函数

SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")


def log(m=""):
    print(m, flush=True)


def hex2bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def save_png(img, path):
    with open(path, "wb") as f:
        f.write(cv2.imencode(".png", img)[1].tobytes())


def load_bgr(path):
    im = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise SystemExit(f"✗ 无法读取图像 {path}")
    return im[:, :, :3] if im.ndim == 3 else cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)


def assign_remain(domain, children, min_area=1.0, max_iter=40):
    """把 domain 中未被 children 覆盖的残余块，归给最长公共边界的邻居；
    孤立块（海岛等没邻居）归给最近邻居。保证 union(children)==domain。"""
    children = list(children)

    def parts(g):
        if g is None or g.is_empty:
            return []
        return [p for p in (g.geoms if g.geom_type != "Polygon" else [g]) if p.area > 0]

    for _ in range(max_iter):
        cov = unary_union([c for c in children if c is not None and not c.is_empty])
        remain = domain.difference(cov).buffer(0)
        rem_parts = [p for p in parts(remain) if p.area > min_area]
        if not rem_parts:
            break
        for p in rem_parts:
            best, bs = None, -1.0
            for k, c in enumerate(children):
                if c is None or c.is_empty:
                    continue
                try:
                    bl = p.boundary.intersection(c.boundary).length
                except Exception:
                    bl = 0.0
                if bl > bs:
                    bs, best = bl, k
            if bs <= 0.0:
                rp = p.representative_point()
                best = min(range(len(children)),
                           key=lambda k: children[k].distance(rp))
            children[best] = unary_union([children[best], p]).buffer(0)
    return children


def chaikin(ls, iters):
    """Chaikin 角切割：把折线修成平滑曲线（端点固定，闭合环按环形处理）。"""
    coords = list(ls.coords)
    if len(coords) < 3:
        return ls
    closed = ls.is_ring
    if closed:
        coords = coords[:-1]
    for _ in range(iters):
        if len(coords) < 3:
            break
        n = len(coords)
        if closed:
            new = []
            for i in range(n):
                x0, y0 = coords[i]
                x1, y1 = coords[(i + 1) % n]
                new.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
                new.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        else:
            new = [coords[0]]
            for i in range(n - 1):
                x0, y0 = coords[i]
                x1, y1 = coords[i + 1]
                new.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
                new.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
            new.append(coords[-1])
        coords = new
    if closed:
        coords.append(coords[0])
    return LineString(coords)


def smooth_polygon_curvy(g, tol, iters, min_part=0.0):
    """简化(去 20m 栅格台阶) + Chaikin(成曲线)，逐个部件处理。"""
    out = []
    for p in fp.parts_of(g):
        if min_part > 0 and p.area < min_part:
            continue
        try:
            ext = chaikin(p.exterior.simplify(tol, preserve_topology=True), iters)
            holes = [chaikin(r.simplify(tol, preserve_topology=True), iters)
                     for r in p.interiors]
            q = Polygon(ext, holes)
            if not q.is_valid:
                q = q.buffer(0)
            out.append(q if not q.is_empty else p)
        except Exception:
            out.append(p)
    return unary_union(out).buffer(0) if out else g


def curve_smooth_children(children, U, tol, iters, min_hole=1.0, min_part=0.0):
    """【曲线化】把栅格台阶边界改成平滑曲线，同时严格保持分区与 U 外轮廓。

    各区域独立 simplify+Chaikin 后会互相错开（出现细缝/重叠），再用
    remove_overlaps + assign_remain 重新收口，并把每个区域裁回 U，
    从而保证：外轮廓（县市界/海岸线/母镇边）逐字节等于 U，内部界线为曲线。
    """
    sm = [fp.to_polygonal(smooth_polygon_curvy(c, tol, iters, min_part)) for c in children]
    sm = [c.intersection(U).buffer(0) if (c is not None and not c.is_empty) else Polygon()
          for c in sm]
    sm, _ = fp.remove_overlaps(sm, 0.0)
    sm = assign_remain(U, sm, min_area=1.0)
    sm = [fp.drop_micro_holes(fp.repair_validity(c) or c, min_hole) for c in sm]
    sm = [c.intersection(U).buffer(0) for c in sm]
    if min_part > 0:
        sm = [fp.drop_micro_parts(c, min_part) for c in sm]
    return sm


def main(argv=None):
    ap = argparse.ArgumentParser(description="三亚城芯6乡镇整体重划")
    ap.add_argument("--shp", default=os.path.join(HERE, "sanya.shp"))
    ap.add_argument("--img", default=os.path.join(HERE, "Sanya1.png"))
    ap.add_argument("--color-map", default=os.path.join(HERE, "color_map_sanya.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "sanya_fixed"))
    ap.add_argument("--crs", default="EPSG:32649")
    ap.add_argument("--px-m", type=float, default=20.0)
    ap.add_argument("--color-tol", type=int, default=30)
    ap.add_argument("--min-region-px", type=int, default=1000)
    ap.add_argument("--smooth-close-m", type=float, default=20.0,
                    help="形态学闭运算半径(m)：填平凹角/锯齿，0=关闭")
    ap.add_argument("--smooth-open-m", type=float, default=20.0,
                    help="形态学开运算半径(m)：抹平凸角/锯齿，0=关闭")
    ap.add_argument("--min-hole-m2", type=float, default=1.0)
    ap.add_argument("--min-part-m2", type=float, default=100.0,
                    help="剔除新区域中面积小于该值的孤立碎屑/毛刺(m²)")
    ap.add_argument("--curve-tol", type=float, default=25.0,
                    help="【曲线化】简化容差(m)：去 20m 栅格台阶，越大越平滑，0=关闭")
    ap.add_argument("--curve-iters", type=int, default=3,
                    help="【曲线化】Chaikin 角切割迭代次数（越大越圆滑）")
    args = ap.parse_args(argv)

    with open(args.color_map, encoding="utf-8") as f:
        cm = json.load(f)
    colors = cm["colors"]
    codes = cm["codes"]
    city = cm.get("city", "三亚市")
    group = cm["group_towns"]

    # ---------- 1. 读现行 SHP ----------
    g = gpd.read_file(os.path.abspath(args.shp), encoding="utf-8")
    src_crs = g.crs
    g = g.to_crs(args.crs).reset_index(drop=True)
    missing = [t for t in group if t not in set(g["TOWN"])]
    if missing:
        raise SystemExit(f"✗ 缺少整体乡镇：{missing}")
    minx, miny, maxx, maxy = g.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    log(f"  现行 SHP：{len(g)} 个乡镇 → {args.crs}")
    log(f"  整体 6 乡镇：{'、'.join(group)}")

    is_group = g["TOWN"].isin(group).values
    other = g[~is_group].reset_index(drop=True)          # 逐字节不变的 10 乡镇
    group_geoms = [x if x.is_valid else make_valid(x) for x in g.loc[is_group, "geometry"]]
    U = unary_union(group_geoms).buffer(0)
    log(f"  整体域 U：{U.area/1e6:.3f} km²，{len(fp.parts_of(U))} 个部件")
    log(f"  未受影响乡镇：{len(other)} 个")

    # ---------- 2. 配准核对 + 栅格种子 ----------
    img = load_bgr(os.path.abspath(args.img))
    H, W = img.shape[:2]
    exp_w, exp_h = int(round(geo_w / args.px_m)), int(round(geo_h / args.px_m))
    ok = (W, H) == (exp_w, exp_h)
    log(f"  手绘图 {W}×{H}，按 1px:{args.px_m:g}m 预期 {exp_w}×{exp_h}"
        + ("（一致 ✓）" if ok else "（⚠ 不一致，仍按四至配准）"))
    tr = from_bounds(minx, miny, maxx, maxy, W, H)

    seed = np.zeros((H, W), np.int32)
    names = []
    for k, (hx, nm) in enumerate(colors.items(), 1):
        names.append(nm)
        bgr = np.array(hex2bgr(hx), np.int16)
        d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
        mask = (d <= args.color_tol).astype(np.uint8)
        n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            if int(st[i, cv2.CC_STAT_AREA]) < args.min_region_px:
                continue
            seed[lab == i] = k
    log(f"  彩色种子：{len(names)} 类 → " + "、".join(names))

    # 域 U 的栅格掩膜（外轮廓以现行 SHP 为准）
    Umask = rio_rasterize([(U, 1)], out_shape=(H, W), transform=tr,
                          fill=0, all_touched=True, dtype="uint8") > 0
    seed[~Umask] = 0
    log(f"  U 像素：{int(Umask.sum())}；种子像素：{int((seed > 0).sum())}")

    # ---------- 3. 全域唯一归属（EDT） ----------
    from scipy import ndimage
    _, (iy, ix) = ndimage.distance_transform_edt(seed == 0, return_indices=True)
    final = seed[iy, ix].astype(np.int32)
    final[~Umask] = 0
    px_area = (geo_w / W) * (geo_h / H)
    for k, nm in enumerate(names, 1):
        log(f"    {nm:<5} 像素 {int((final == k).sum()):>7}  ≈ {(final == k).sum()*px_area/1e6:7.2f} km²")

    # ---------- 4. 矢量化（像素边，天然共边） ----------
    polys_by_k = {k: [] for k in range(1, len(names) + 1)}
    for geom, val in rio_shapes(final, mask=Umask, transform=tr, connectivity=8):
        polys_by_k[int(val)].append(shape(geom))
    children = []
    for k, nm in enumerate(names, 1):
        gg = unary_union(polys_by_k[k]).buffer(0) if polys_by_k[k] else None
        if gg is None:
            raise SystemExit(f"✗ 区域 {nm} 矢量化失败")
        gg = gg.intersection(U).buffer(0)
        children.append(gg)
    log(f"  矢量化完成：{len(children)} 个区域，合计 {sum(c.area for c in children)/1e6:.3f} km²")

    # ---------- 4b. 曲线化：形态学开/闭运算抹平像素锯齿 ----------
    def _smooth(g):
        if args.smooth_close_m > 0:
            g = g.buffer(args.smooth_close_m, join_style=1).buffer(-args.smooth_close_m, join_style=1)
        if args.smooth_open_m > 0:
            g = g.buffer(-args.smooth_open_m, join_style=1).buffer(args.smooth_open_m, join_style=1)
        return g.buffer(0)

    if args.smooth_close_m > 0 or args.smooth_open_m > 0:
        children = [_smooth(c) for c in children]
        log(f"  曲线化：闭 {args.smooth_close_m:g}m + 开 {args.smooth_open_m:g}m")

    # ---------- 5. 修复：去重叠 + 残缝/离岛归属 + 微孔清理 ----------
    children, removed = fp.remove_overlaps(children, 0.0)
    log(f"  去重叠：剔除 {removed:.1f} m²")
    children = assign_remain(U, children, min_area=1.0)
    children = [fp.drop_micro_holes(fp.repair_validity(c) or c, args.min_hole_m2) for c in children]
    # 再裁回 U，保证外轮廓权威
    children = [c.intersection(U).buffer(0) for c in children]
    if args.min_part_m2 > 0:
        children = [fp.drop_micro_parts(c, args.min_part_m2) for c in children]

    # ---------- 5b. 曲线化：去 20m 栅格台阶，界线成真实弯曲形态 ----------
    if args.curve_tol > 0 and args.curve_iters > 0:
        children = curve_smooth_children(children, U, args.curve_tol, args.curve_iters,
                                         args.min_hole_m2, args.min_part_m2)
        log(f"  曲线化：simplify {args.curve_tol:g}m + Chaikin×{args.curve_iters}"
            f"（外轮廓严格保持为 U，县市界/海岸线不变）")

    # 严格分区校验
    sum_a = sum(c.area for c in children)
    ua = unary_union(children).buffer(0).area
    miss = U.difference(unary_union(children).buffer(0)).area
    ov = 0.0
    for i in range(len(children)):
        for j in range(i + 1, len(children)):
            if children[i].intersects(children[j]):
                ov += children[i].intersection(children[j]).area
    log(f"  分区校验：U {U.area/1e6:.3f}，区域和 {sum_a/1e6:.3f}，并 {ua/1e6:.3f}，"
        f"重叠 {ov:.2f} m²，域缺 {miss:.2f} m²，非法 {sum(not c.is_valid for c in children)}")

    # ---------- 6. 组装输出 ----------
    rows = []
    for _, r in other.iterrows():
        rows.append(dict(CODE=str(r["CODE"]), TOWN=r["TOWN"], CITY=r.get("CITY", city),
                         EN=r.get("EN", ""), N_FEAT=int(r.get("N_FEAT", 0) or 0),
                         SOURCE="沿用", geom=r.geometry))
    for nm, c in zip(names, children):
        rows.append(dict(CODE=codes[nm], TOWN=nm, CITY=city, EN="",
                         N_FEAT=0, SOURCE="手绘调整", geom=c))
    res = gpd.GeoDataFrame(
        {"CODE": [r["CODE"] for r in rows], "TOWN": [r["TOWN"] for r in rows],
         "CITY": [r["CITY"] for r in rows], "EN": [r["EN"] for r in rows],
         "N_FEAT": [r["N_FEAT"] for r in rows], "SOURCE": [r["SOURCE"] for r in rows]},
        geometry=[r["geom"] for r in rows], crs=args.crs)
    # 防御：确保全部为面（去 GeometryCollection/线，避免写出 ARC 类型）
    log("  几何类型： " + str(res.geom_type.value_counts().to_dict()))
    bad = res[~res.geom_type.isin(["Polygon", "MultiPolygon"])]
    if len(bad):
        log(f"  ⚠ 非面几何 {len(bad)} 个：{bad[['CODE','TOWN']].to_dict('records')}")
    res["geometry"] = res.geometry.apply(lambda x: fp.to_polygonal(x) if x is not None else None)
    res = res[res.geometry.notna() & ~res.geometry.is_empty].reset_index(drop=True)
    res["geometry"] = res.geometry.apply(lambda x: fp.repair_validity(x) or x)
    res["AREA_KM2"] = (res.geometry.area / 1e6).round(3)

    # 未受影响乡镇逐字节不变
    sd_bad = []
    for _, r in other.iterrows():
        sub = res[res["CODE"].astype(str) == str(r["CODE"])]
        sd = unary_union(list(sub.geometry)).buffer(0).symmetric_difference(r.geometry).area
        if sd >= 1.0:
            sd_bad.append((r["TOWN"], sd))
    log(f"  未受影响乡镇不变量校验：{len(other)-len(sd_bad)}/{len(other)} 逐字节不变"
        + (f"，异常 {sd_bad}" if sd_bad else ""))

    # 面积守恒（整县）
    in_area = g.geometry.area.sum()
    out_area = res.geometry.area.sum()
    log(f"  面积守恒：输入 {in_area/1e6:.4f} km² → 输出 {out_area/1e6:.4f} km² "
        f"（差 {(out_area-in_area):.3f} m²）")
    log("")
    log(res[["CODE", "TOWN", "SOURCE", "AREA_KM2"]].to_string(index=False))

    # ---------- 7. 写出 ----------
    out_base = os.path.abspath(args.out)
    for ext in SIDECARS + (".geojson",):
        p = out_base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    # 重投影回源 CRS 可能引入微小自交（如沿用乡镇的长环），写出前再兜底修复
    out = res.to_crs(src_crs)
    out["geometry"] = out.geometry.apply(lambda x: fp.repair_validity(x) or x)
    out.to_file(out_base + ".shp", encoding="utf-8")
    res["geometry"] = res.geometry.apply(lambda x: fp.repair_validity(x) or x)
    res.to_file(out_base + "_utm49.shp", encoding="utf-8")
    log(f"  写出：{out_base}.shp（源 CRS）/ {out_base}_utm49.shp（EPSG:32649）")

    # ---------- 8. QA 报告 ----------
    report = out_base + "_QA报告.md"
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 三亚城芯6乡镇整体重划 QA 报告\n\n")
        f.write(f"- 输入：`{os.path.abspath(args.shp)}`\n")
        f.write(f"- 手绘图：`{os.path.abspath(args.img)}`（{W}×{H}，1px={args.px_m:g}m）\n")
        f.write(f"- 输出：`{out_base}.shp`\n")
        f.write(f"- 坐标系：源 `{src_crs}`\n")
        f.write(f"- 整体域：{'、'.join(group)}（U={U.area/1e6:.3f} km²）\n")
        f.write(f"- 参数：color_tol={args.color_tol}，"
                f"smooth_close={args.smooth_close_m:g}m，smooth_open={args.smooth_open_m:g}m，"
                f"curve_tol={args.curve_tol:g}m，curve_iters={args.curve_iters}，"
                f"min_hole={args.min_hole_m2:g} m²\n")
        f.write(f"- 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        f.write("## 1. 分区校验（9 区域对 U）\n\n```\n")
        f.write(f"domain_U_km2: {U.area/1e6:.6f}\n")
        f.write(f"children_sum_km2: {sum_a/1e6:.6f}\n")
        f.write(f"children_union_km2: {ua/1e6:.6f}\n")
        f.write(f"overlap_m2: {ov:.4f}\n")
        f.write(f"domain_missing_m2: {miss:.4f}\n")
        f.write(f"invalid: {sum(not c.is_valid for c in children)}\n")
        f.write(f"parts: {sum(len(fp.parts_of(c)) for c in children)}\n")
        f.write("```\n\n## 2. 面积守恒（整县）\n\n```\n")
        f.write(f"input_km2: {in_area/1e6:.6f}\n")
        f.write(f"output_km2: {out_area/1e6:.6f}\n")
        f.write(f"diff_m2: {out_area-in_area:.4f}\n")
        f.write("```\n\n## 3. 9 个新区域\n\n")
        f.write("| CODE | TOWN | 面积km² | 部件数 |\n|---|---|---:|---:|\n")
        for nm, c in zip(names, children):
            f.write(f"| {codes[nm]} | {nm} | {c.area/1e6:.4f} | {len(fp.parts_of(c))} |\n")
        f.write("\n## 4. 未受影响乡镇（逐字节不变）\n\n")
        f.write("- 数量：%d/%d\n" % (len(other) - len(sd_bad), len(other)))
        if sd_bad:
            f.write("- 异常：" + ", ".join(f"{t}({a:.1f}m²)" for t, a in sd_bad) + "\n")
        f.write(f"\n- 输出要素总数：{len(res)}（沿用 {len(other)}，手绘调整 {len(children)}）\n")
    log(f"  报告：{report}")

    # ---------- 9. 出图 ----------
    try:
        imgc = load_bgr(os.path.abspath(args.img))
        # overlay：手绘图 + 新界线
        ov = imgc.copy()
        for nm, c in zip(names, children):
            for p in fp.parts_of(c):
                pts = np.stack([(np.asarray(p.exterior.coords)[:, 0] - minx) / geo_w * W,
                                (maxy - np.asarray(p.exterior.coords)[:, 1]) / geo_h * H], 1).astype(np.int32)
                cv2.polylines(ov, [pts], True, (0, 0, 255), 3, cv2.LINE_AA)
        save_png(ov, out_base + "_overlay.png")
        # sidebyside：左=手绘，右=结果着色（沿用灰、调整彩）
        fills = np.full((H, W, 3), 255, np.uint8)
        pal = {nm: hex2bgr(hx) for hx, nm in colors.items()}
        for nm, c in zip(names, children):
            for p in fp.parts_of(c):
                c2 = np.asarray(p.exterior.coords)
                pts = np.stack([(c2[:, 0] - minx) / geo_w * W, (maxy - c2[:, 1]) / geo_h * H], 1).astype(np.int32)
                cv2.fillPoly(fills, [pts], pal[nm])
                cv2.polylines(fills, [pts], True, (0, 0, 0), 1, cv2.LINE_8)
        for _, r in other.iterrows():
            for p in fp.parts_of(r.geometry):
                c2 = np.asarray(p.exterior.coords)
                pts = np.stack([(c2[:, 0] - minx) / geo_w * W, (maxy - c2[:, 1]) / geo_h * H], 1).astype(np.int32)
                cv2.fillPoly(fills, [pts], (235, 235, 235))
                cv2.polylines(fills, [pts], True, (0, 0, 0), 1, cv2.LINE_8)
        save_png(np.hstack([imgc, fills]), out_base + "_sidebyside.png")
        log(f"  出图：{out_base}_overlay.png / _sidebyside.png")
    except Exception as e:
        log(f"  ⚠ 出图失败：{e}")
    log("DONE")


if __name__ == "__main__":
    main()
