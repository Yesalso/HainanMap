# -*- coding: utf-8 -*-
"""
reverse_by_color.py —— 手绘上色图 → 指定县市 2002 乡镇 SHP（只用本县现行 SHP）
================================================================================
可复用于海南任意县市：换一个“颜色表 JSON”即可。

原理（依据 town/2002海口转换总结.txt）：
  1. 配准：手绘图与现行 SHP 用同一套正向制图参数（Empty_map/Hainan.py：EPSG:32649、
     1px=20m、四至=本县 SHP 的 total_bounds），故像素↔米制一一对应，偏移 (0,0)；
  2. 事实：手绘图黑线 = 目标年乡镇界；彩色块 = 该年存在、后并入他镇的旧乡镇；
     白区 = 与现行一致的乡镇（可用 check_reverse.py 核验黑线落在现行边界内）；
  3. 切分：对含彩色块的现行乡镇，按其彩色块拆成「旧乡镇 + 其余」；其余乡镇
     几何逐字节保持不变（“名单外不变”纪律；严格划分：无缝隙、无重叠、面积守恒）；
  4. 归属：彩色块按颜色表命名；旧乡镇编号见 JSON 的 new_code_start / new_order。

用法：
  python reverse_by_color.py                                   # 用同目录默认文件
  python reverse_by_color.py --color-map color_map_wenchang.json
  python reverse_by_color.py --shp X.shp --img X_draw.png --out X2002 --color-map cm.json

颜色表 JSON 格式：
  {
    "city": "文昌市",
    "new_code_start": 469005117,
    "new_order": ["湖山乡","宝芳乡","清澜镇","迈号镇","南阳乡","头苑镇","龙马乡","新桥乡"],
    "multi_order": "coast",                 // 一个颜色多个连通域时排序：coast=离海岸由近及远 / area=面积由大到小
    "colors": {
      "#00A2E8": "湖山乡",
      "#22B14C": ["清澜镇","宝芳乡"],        // 列表=该色有多个连通域
      "#FF7F27": "迈号镇"
    }
  }
输出：<out>.shp（EPSG:32649）+ <out>_Albers.shp（源 CRS）+ <out>_preview.png
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

HERE = os.path.dirname(os.path.abspath(__file__))

# 未提供 --color-map 时的内置默认（文昌 2002）
DEFAULT_CM = {
    "city": "文昌市",
    "new_code_start": 469005117,
    "new_order": ["湖山乡", "宝芳乡", "清澜镇", "迈号镇", "南阳乡", "头苑镇", "龙马乡", "新桥乡"],
    "multi_order": "coast",
    "colors": {
        "#00A2E8": "湖山乡",
        "#22B14C": ["清澜镇", "宝芳乡"],
        "#FF7F27": "迈号镇",
        "#FFF200": "南阳乡",
        "#880015": "头苑镇",
        "#99D9EA": "龙马乡",
        "#7F7F7F": "新桥乡",
    },
}

SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")


def log(m=""):
    print(m, flush=True)


def hex2bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def load_color(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def clean(base):
    for ext in SIDECARS + (".geojson",):
        p = base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def load_cm(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cm = json.load(f)
        log(f"  颜色表：{path}")
        return cm
    return DEFAULT_CM


def main(argv=None):
    ap = argparse.ArgumentParser(description="手绘上色图 → 指定县市 2002 乡镇 SHP")
    ap.add_argument("--shp", default=os.path.join(HERE, "wenchang.shp"),
                    help="现行本县乡镇 SHP（唯一数据源，不读全省）")
    ap.add_argument("--img", default=os.path.join(HERE, "Wenchang_draw.png"),
                    help="手绘上色 PNG")
    ap.add_argument("--out", default=os.path.join(HERE, "wenchang2002"),
                    help="输出前缀（不含扩展名）")
    ap.add_argument("--color-map", default=os.path.join(HERE, "color_map_wenchang.json"),
                    help="颜色表 JSON（缺省用内置文昌表）")
    ap.add_argument("--code-field", default="CODE")
    ap.add_argument("--name-field", default="TOWN")
    ap.add_argument("--crs", default="EPSG:32649", help="正向制图坐标系")
    ap.add_argument("--px-m", type=float, default=20.0, help="正向制图比例尺(m/px)，用于核对")
    ap.add_argument("--color-tol", type=int, default=30, help="颜色匹配容差(曼哈顿)")
    ap.add_argument("--min-region-px", type=int, default=1000, help="最小彩色块像素")
    ap.add_argument("--min-keep-m2", type=float, default=1e5, help="过滤碎屑面积(m²)")
    ap.add_argument("--smooth-m", type=float, default=20.0, help="轮廓拓扑保持简化容差(m)")
    ap.add_argument("--block-clip", choices=["parent", "county"], default="parent",
                    help="色块裁剪范围：parent=裁到母镇(方案A，默认)；"
                         "county=只裁到县域(色块保留全域，消跨镇残留旧界)")
    ap.add_argument("--grow-m", type=float, default=0.0,
                    help="色块先外扩该米数(≈色块相对黑线的内缩量，贴回黑线中心)，默认0")
    ap.add_argument("--no-albers", action="store_true", help="不输出源坐标系版本")
    ap.add_argument("--no-preview", action="store_true", help="不出预览图")
    args = ap.parse_args(argv)

    cm = load_cm(args.color_map)
    city = cm.get("city", "")
    colors = cm["colors"]
    new_order = cm.get("new_order", [])
    multi_order = cm.get("multi_order", "coast")
    code_start = int(cm.get("new_code_start", 0))

    out_base = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)

    # ---------------- 1. 读现行 SHP（唯一数据源） ----------------
    g = gpd.read_file(os.path.abspath(args.shp), encoding="utf-8")
    src_crs = g.crs
    g["geometry"] = g.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    g = g.to_crs(args.crs).reset_index(drop=True)
    minx, miny, maxx, maxy = g.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    log(f"  现行 SHP：{len(g)} 个乡镇，CRS→{args.crs}")
    log(f"  四至 [{minx:.1f}, {miny:.1f}, {maxx:.1f}, {maxy:.1f}]  {geo_w:.1f}×{geo_h:.1f} m")

    # ---------------- 2. 读手绘图，核对配准 ----------------
    img = load_color(os.path.abspath(args.img))
    if img is None:
        raise SystemExit(f"✗ 无法读取图像 {args.img}")
    H, W = img.shape[:2]
    exp_w, exp_h = int(round(geo_w / args.px_m)), int(round(geo_h / args.px_m))
    log(f"  手绘图 {W}×{H}，按 1px:{args.px_m:g}m 预期 {exp_w}×{exp_h}"
        + ("（一致 ✓）" if (W, H) == (exp_w, exp_h) else "（⚠ 不一致，仍按四至配准）"))
    sx, sy = geo_w / W, geo_h / H

    def px2geo(i, j):
        return minx + (i + 0.5) * sx, maxy - (j + 0.5) * sy

    outer = unary_union([x for x in g.geometry if x is not None and not x.is_empty]).buffer(0)

    # ---------------- 3. 提取彩色块 ----------------
    def comp_to_poly(mask_comp):
        mm = mask_comp.astype(np.uint8) * 255
        cnts, _ = cv2.findContours(mm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        c = max(cnts, key=cv2.contourArea)[:, 0, :]
        if len(c) < 3:
            return None
        poly = Polygon([px2geo(int(p[0]), int(p[1])) for p in c]).buffer(0)
        if poly.is_empty:
            return None
        if args.smooth_m > 0:
            poly = poly.simplify(args.smooth_m, preserve_topology=True)  # 抹像素台阶，不整体收缩
        return poly if (not poly.is_empty and poly.area > 0) else None

    def parent_town(poly):
        best, bo = None, 0.0
        for idx, r in g.iterrows():
            o = poly.intersection(r.geometry).area
            if o > bo:
                bo, best = o, idx
        return best

    found = []   # [name, poly, parent_idx, hex, cx, cy]
    for hx, spec in colors.items():
        names = spec if isinstance(spec, list) else [spec]
        bgr = np.array(hex2bgr(hx), np.int16)
        d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
        mask = (d <= args.color_tol).astype(np.uint8)
        n, lab, st, cen = cv2.connectedComponentsWithStats(mask, 8)
        comps = []
        for i in range(1, n):
            if int(st[i, cv2.CC_STAT_AREA]) < args.min_region_px:
                continue
            poly = comp_to_poly(lab == i)
            if poly is None:
                continue
            comps.append([poly, int(cen[i][0]), int(cen[i][1])])
        if len(comps) > len(names):
            log(f"  ⚠ 颜色 {hx} 有 {len(comps)} 块，但只给了 {len(names)} 个名称")
        if len(names) > 1:
            if multi_order == "area":
                comps.sort(key=lambda z: -z[0].area)
            else:
                comps.sort(key=lambda z: z[0].distance(outer.boundary))
        for k, (poly, cx, cy) in enumerate(comps):
            nm = names[k] if k < len(names) else f"{names[-1]}_{k+1}"
            found.append([nm, poly, parent_town(poly), hx, cx, cy])

    log(f"  彩色块：{len(found)} 个")
    for nm, poly, pidx, hx, cx, cy in found:
        pt = g.loc[pidx, args.name_field] if pidx is not None else "-"
        log(f"    {nm:<6} {hx}  {poly.area/1e6:6.2f} km²  质心({cx},{cy})  ⊂ {pt}")

    # 色块外扩（贴回黑线中心），并去除外扩后产生的相互重叠（面积大者优先）
    if args.grow_m and args.grow_m > 0:
        for f in found:
            f[1] = f[1].buffer(args.grow_m, quad_segs=8).buffer(0)
        acc = None
        for i in sorted(range(len(found)), key=lambda k: -found[k][1].area):
            if acc is not None and not acc.is_empty:
                found[i][1] = found[i][1].difference(acc).buffer(0)
            acc = found[i][1] if acc is None else unary_union([acc, found[i][1]])
        log(f"  色块外扩 {args.grow_m:g} m 后：{sum(f[1].area for f in found)/1e6:.3f} km²")

    # 色块裁剪范围：parent=裁到所属母镇（方案A，严格划分）；county=只裁到县域（保留全域）
    if args.block_clip == "county":
        for f in found:
            f[1] = f[1].intersection(outer).buffer(0)
        all_blocks = unary_union([f[1] for f in found]).buffer(0) if found else None
    else:
        for f in found:
            if f[2] is not None:
                f[1] = f[1].intersection(g.loc[f[2], "geometry"]).buffer(0)
        all_blocks = None

    # ---------------- 4. 切分现行乡镇 ----------------
    colored_by_town = {}
    for nm, poly, pidx, hx, cx, cy in found:
        colored_by_town.setdefault(pidx, []).append((nm, poly))

    order = [n for n in new_order if n in {f[0] for f in found}]
    order += [n for n in {f[0] for f in found} if n not in order]  # 补上表外名称
    name_to_code = {nm: code_start + i for i, nm in enumerate(order)}
    log(f"  旧乡镇编号：{ {k: v for k, v in name_to_code.items()} }")

    rows = []
    for idx, r in g.iterrows():
        children = colored_by_town.get(idx, [])
        if args.block_clip == "county":
            cunion = all_blocks
        else:
            cunion = unary_union([c[1] for c in children]).buffer(0) if children else None
        if cunion is None:
            rows.append(dict(code=str(r[args.code_field]), town=r[args.name_field],
                             city=r.get("CITY", city), en=r.get("EN", ""),
                             nfeat=int(r.get("N_FEAT", 0) or 0), source="沿用", geom=r.geometry))
            continue
        remain = r.geometry.difference(cunion).buffer(0)
        if not remain.is_empty and remain.area > args.min_keep_m2:
            rows.append(dict(code=str(r[args.code_field]), town=r[args.name_field],
                             city=r.get("CITY", city), en=r.get("EN", ""),
                             nfeat=int(r.get("N_FEAT", 0) or 0), source="沿用", geom=remain))
        if args.block_clip == "county":
            continue                       # 色块统一在循环外添加，避免跨镇重复
        for nm, poly in children:
            if poly.area <= args.min_keep_m2:
                continue
            rows.append(dict(code=str(name_to_code[nm]), town=nm, city=r.get("CITY", city),
                             en="", nfeat=0, source="2002新增", geom=poly))

    if args.block_clip == "county":
        for nm, poly, pidx, hx, cx, cy in found:
            if poly.area <= args.min_keep_m2:
                continue
            cty = g.loc[pidx, "CITY"] if pidx is not None and "CITY" in g.columns else city
            rows.append(dict(code=str(name_to_code[nm]), town=nm, city=cty,
                             en="", nfeat=0, source="2002新增", geom=poly))

    res = gpd.GeoDataFrame(
        {"CODE": [r["code"] for r in rows], "TOWN": [r["town"] for r in rows],
         "CITY": [r["city"] for r in rows], "EN": [r["en"] for r in rows],
         "N_FEAT": [r["nfeat"] for r in rows], "SOURCE": [r["source"] for r in rows]},
        geometry=[r["geom"] for r in rows], crs=args.crs)
    res["geometry"] = res.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    res["AREA_KM2"] = (res.geometry.area / 1e6).round(3)

    # ---------------- 5. 校验 ----------------
    log("")
    log(f"  输出要素：{len(res)} 个（沿用 {int((res.SOURCE=='沿用').sum())}，"
        f"2002新增 {int((res.SOURCE=='2002新增').sum())}）")
    tot = res.geometry.area.sum() / 1e6
    cur = g.geometry.area.sum() / 1e6
    ua = unary_union(list(res.geometry)).buffer(0).area / 1e6
    log(f"  面积：现行 {cur:.3f} km²，结果 {tot:.3f} km²，并 {ua:.3f} km²，"
        f"内部重叠 {(tot-ua):.5f} km²")
    maxov = 0.0
    geoms = list(res.geometry)
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if geoms[i].intersects(geoms[j]):
                maxov = max(maxov, geoms[i].intersection(geoms[j]).area)
    log(f"  两两最大重叠：{maxov:.3f} m²   非法几何：{int((~res.geometry.is_valid).sum())}")
    log("")
    log(res[["CODE", "TOWN", "SOURCE", "AREA_KM2"]].to_string(index=False))

    # ---------------- 6. 写出 ----------------
    clean(out_base)
    res.to_file(out_base + ".shp", encoding="utf-8")
    log(f"  写出：{out_base}.shp")
    if (not args.no_albers) and src_crs is not None and str(src_crs) != args.crs:
        res.to_crs(src_crs).to_file(out_base + "_Albers.shp", encoding="utf-8")
        log(f"  写出：{out_base}_Albers.shp（源 CRS）")

    # ---------------- 7. 预览图 ----------------
    if not args.no_preview:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            for f in ("Microsoft YaHei", "SimHei", "SimSun"):
                try:
                    matplotlib.rcParams["font.sans-serif"] = [f]
                    break
                except Exception:
                    continue
            fig, ax = plt.subplots(figsize=(14, 15), dpi=100)
            res.plot(ax=ax, column="TOWN", cmap="tab20", edgecolor="black", linewidth=0.6)
            for _, r in res.iterrows():
                p = r.geometry.representative_point()
                ax.text(p.x, p.y, str(r["TOWN"]), ha="center", va="center", fontsize=9)
            ax.set_aspect("equal")
            ax.axis("off")
            ax.set_title(f"{city} 目标年乡镇（{len(res)} 单元）", fontsize=15)
            plt.tight_layout()
            png = out_base + "_preview.png"
            import io
            buf = io.BytesIO()
            plt.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
            plt.close(fig)
            buf.seek(0)
            with open(png, "wb") as f:   # 规避 matplotlib 中文路径问题
                f.write(buf.getvalue())
            log(f"  预览图：{png}")
        except Exception as e:
            log(f"  ⚠ 预览图失败：{e}")
    log("DONE")


if __name__ == "__main__":
    main()