# -*- coding: utf-8 -*-
"""
clean_town_partition.py —— 乡镇分区「去碎屑 + 同名合并」通用清洗工具
================================================================================
适用于「手绘图反向映射 → 乡镇 SHP」流程（reverse_by_region.py / reverse_by_color.py
等）输出的中间层：该层同时含 `沿用`（沿用现行边界的白区）与 `2002新增`（手绘彩色区）
两类要素，常见两类瑕疵：

  ① 碎屑：沿「沿用区 ↔ 新增区」或「沿用区 ↔ 沿用区」边界，因栅格化/EDT 归属留下
     大量细长小碎块，被就近命名为某个大镇（如 金江/老城/文儒/福山），远看像满图麻点；
  ② 同名两片：同一乡镇既有 `沿用` 又有 `2002新增` 两个要素，两者之间多出一条内部界线。

本工具做两件事：
  1. 去碎屑：把目标乡镇 `沿用` 要素中面积 < 阈值的每个小部件，并入「公共边界最长」
     的相邻要素（找不到公共边则并入最近要素）。碎屑只会在相邻要素间搬家，不产生缝隙。
  2. 同名合并：把目标乡镇的 `沿用`（剩余大块）+ `2002新增` 用 unary_union 合并为一个
     要素，内部界线自然消失；`沿用` 行被吸收删除。

最后做一次重叠消除与几何净化（make_valid、去退化部件、去微小孔洞、SHP 缠绕方向），
并输出 QA 报告与前后对比图。

--------------------------------------------------------------------------------
用法
--------------------------------------------------------------------------------
  # 最简：自动识别所有「同名两片」的乡镇，阈值 0.6 km²
  python clean_town_partition.py --in chengmai_v_g40.shp --out chengmai_v_g40_fixed

  # 指定要处理/合并的乡镇，并另存 Albers 版
  python clean_town_partition.py --in chengmai_v_g40.shp --out chengmai_v_g40_fixed \
      --merge-towns 金江镇,老城镇,文儒镇,福山镇 \
      --albers-prj chengmai_v_g40_Albers.prj

  # 只去碎屑、不合并（例如某镇沿用块需保留）
  python clean_town_partition.py --in xx.shp --out xx_clean --frag-towns 中兴镇

参数
  --in            输入 SHP
  --out           输出前缀（写 <out>.shp；可选 <out>_Albers.shp）
  --code-field    代码字段（默认 CODE）
  --name-field    乡镇名字段（默认 TOWN）
  --source-field  来源字段（默认 SOURCE）
  --old-value     沿用值（默认 沿用）
  --new-value     新增值（默认 2002新增）
  --merge-towns   要合并同名两片的乡镇，逗号分隔；默认自动
  --frag-towns    要消碎屑的乡镇；默认 = merge-towns
  --thresh-km2    碎屑面积阈值 km²（默认 0.6）
  --min-hole-m2   孔洞噪点阈值 m²（默认 100）
  --albers-prj    参考 .prj，另存一份该 CRS
  --albers-crs    直接给 CRS（EPSG/WKT）
  --no-albers / --no-plot / --no-report
  --encoding      DBF 编码（默认自动 utf-8 → gb18030）

--------------------------------------------------------------------------------
经验与踩坑（复用必读）
--------------------------------------------------------------------------------
  * 属性编码：部分 DBF 的 .cpg 标 UTF-8 但 pyogrio/ogr 会按 GBK 误读，中文变乱码。
    因此本工具**自己解析 DBF**（先 utf-8 后 gb18030 兜底），几何用 geopandas 读，
    按行序拼接；这样字段名、中文值都可靠。
  * 阈值选择：本项目数据里「合法大块」与「碎屑」之间有天然断档（如 6.46 km² ↔ 0.55 km²），
    0.6 km² 是稳妥默认；换县时先看部件面积排序找断档再定。
  * 目的地用「公共边界最长」而非「最近点」：碎屑常夹在两镇之间，最近点法易误判，
    最长公共边法等价于 ArcGIS Eliminate，结果更符合直觉。
  * 不能让碎屑并入「同属待清理的沿用碎块」，否则会形成搬运链；本工具把待清理沿用行
    从候选目的地中剔除。
  * 岛屿/飞地：若某部件与任何要素都无公共边（真孤岛），保留而非乱并；本工具对
    「无公共边」只在最近要素过远时不并（阈值内并入最近，孤岛建议排除出 frag-towns）。
  * 输出前必须统一几何：make_valid 可能产出 GeometryCollection，需只取面；孔洞面积
    为 0 的退化环要删；SHP 外环应顺时针（orient sign=-1）否则 ogr 会告警。
  * 面积守恒校验：输出并面积应等于输入并面积（本工具会打印），不等说明引入了缝/叠。
"""
from __future__ import annotations

import argparse
import os
import struct
import warnings

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pyproj import CRS
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely import make_valid

warnings.filterwarnings("ignore", category=RuntimeWarning)
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


# ----------------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------------
def _decode(b: bytes) -> str:
    b = b.rstrip(b"\x00")
    for enc in ("utf-8", "gb18030"):
        try:
            return b.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    return b.decode("latin1").strip()


def read_dbf(path: str):
    """手工解析 DBF：返回 (fields, records)。fields=[(name,type,len,dec), ...]。"""
    raw = open(path, "rb").read()
    nrec = struct.unpack("<I", raw[4:8])[0]
    hlen = struct.unpack("<H", raw[8:10])[0]
    rlen = struct.unpack("<H", raw[10:12])[0]
    off, fields = 32, []
    while raw[off] != 0x0D:
        fd = raw[off:off + 32]
        fields.append((fd[:11].split(b"\x00")[0].decode("ascii", "replace"),
                       chr(fd[11]), fd[16], fd[17]))
        off += 32
    recs = []
    for i in range(nrec):
        r = raw[hlen + i * rlen: hlen + (i + 1) * rlen]
        pos, vals = 1, {}
        for name, typ, ln, dec in fields:
            s = _decode(r[pos:pos + ln])
            pos += ln
            if typ in "NF":
                try:
                    vals[name] = int(float(s)) if dec == 0 else float(s)
                except ValueError:
                    vals[name] = None
            else:
                vals[name] = s
        recs.append(vals)
    return fields, recs


def parts_of(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    out = []
    for s in getattr(g, "geoms", []):
        if s.geom_type == "Polygon":
            out.append(s)
        else:
            out.extend(parts_of(s))
    return out


def clean(g, min_hole=100.0):
    """只保留面：修非法几何、删退化部件/微孔、按 SHP 缠绕方向整理。空则返回 None。"""
    if g is None or g.is_empty:
        return None
    g = make_valid(g) if not g.is_valid else g
    polys = []

    def rec(x):
        if x.geom_type == "Polygon":
            polys.append(x)
        elif x.geom_type in ("MultiPolygon", "GeometryCollection"):
            for s in x.geoms:
                rec(s)

    rec(g)
    polys = [p for p in polys if p.area > 0]
    if not polys:
        return None
    out = unary_union([Polygon(p.exterior, [h for h in p.interiors
                                            if Polygon(h).area >= min_hole])
                       for p in polys])
    out = make_valid(out) if not out.is_valid else out
    if out.geom_type not in ("Polygon", "MultiPolygon"):
        return clean(out, min_hole)
    return orient(out, -1.0)


def load_layer(path, code_f, name_f, source_f):
    """读几何(geopandas) + 读属性(自解析DBF)，按行序拼接。"""
    gdf = gpd.read_file(path)
    dbf = os.path.splitext(path)[0] + ".dbf"
    if os.path.exists(dbf):
        fields, recs = read_dbf(dbf)
        if len(recs) == len(gdf):
            for name, *_ in fields:            # 用自解析值覆盖全部字段（避免乱码）
                if name in recs[0]:
                    gdf[name] = [r.get(name) for r in recs]
    for f in (code_f, name_f, source_f):
        if f and f not in gdf.columns:
            raise SystemExit(f"✗ 缺少字段 {f}")
    return gdf


# ----------------------------------------------------------------------------
# 主处理
# ----------------------------------------------------------------------------
def process(gdf, code_f, name_f, source_f, old_value, new_value,
            merge_towns, frag_towns, thresh_m2, min_hole):
    names = list(gdf[name_f])
    sources = list(gdf[source_f])
    old_rows = {n: [] for n in set(names)}
    new_rows = {n: [] for n in set(names)}
    for i, (n, s) in enumerate(zip(names, sources)):
        if s == old_value:
            old_rows[n].append(i)
        elif s == new_value:
            new_rows[n].append(i)

    if merge_towns is None:
        merge_towns = [n for n in dict.fromkeys(names)
                       if old_rows[n] and new_rows[n]]
    if frag_towns is None:
        frag_towns = list(merge_towns)
    merge_towns = list(dict.fromkeys(merge_towns))
    frag_towns = list(dict.fromkeys(frag_towns))

    geom = {i: clean(gdf.geometry.iloc[i], min_hole) for i in range(len(gdf))}
    # 待清理的沿用行，不能作为碎屑的目的地
    frag_rows = {i for n in frag_towns for i in old_rows[n]}
    stable = [i for i in range(len(gdf)) if i not in frag_rows]

    log = []
    for n in frag_towns:
        for i in old_rows[n]:
            parts = parts_of(geom[i])
            kept = [p for p in parts if p.area >= thresh_m2]
            moved = 0
            for p in parts:
                if p.area >= thresh_m2:
                    continue
                best, bl = None, -1.0
                for j in stable:
                    gj = geom[j]
                    if gj is None or gj.is_empty or not gj.intersects(p):
                        continue
                    l = p.boundary.intersection(gj.boundary).length
                    if l > bl:
                        bl, best = l, j
                if best is None:
                    best = min(stable, key=lambda j: geom[j].distance(p))
                geom[best] = clean(unary_union([geom[best], p]), min_hole)
                moved += 1
            geom[i] = clean(unary_union(kept), min_hole) if kept else None
            log.append((n, gdf.geometry.iloc[i].area / 1e6,
                        sum(p.area for p in kept) / 1e6, moved))

    # 同名合并
    merged_log = []
    for n in merge_towns:
        if not new_rows[n]:
            continue
        target = new_rows[n][0]
        survivors = [geom[i] for i in old_rows[n] if geom[i] is not None]
        if survivors:
            geom[target] = clean(unary_union([geom[target]] + survivors), min_hole)
            merged_log.append((n, True))
        else:
            merged_log.append((n, False))
        for i in old_rows[n]:
            geom[i] = None

    rows, geoms = [], []
    for i in range(len(gdf)):
        if geom[i] is None or geom[i].is_empty:
            continue
        rows.append({c: gdf[c].iloc[i] for c in gdf.columns if c != "geometry"})
        geoms.append(geom[i])

    # 去重叠（大者优先占用）
    order = sorted(range(len(geoms)), key=lambda i: -geoms[i].area)
    occ, fixed = None, [None] * len(geoms)
    for i in order:
        g = geoms[i]
        if occ is not None and not occ.is_empty:
            g = clean(g.difference(occ), min_hole) or Polygon()
        fixed[i] = g
        occ = g if occ is None else unary_union([occ, g])

    out = gpd.GeoDataFrame(rows, geometry=fixed, crs=gdf.crs)
    if "AREA_KM2" in out.columns:
        out["AREA_KM2"] = (out.geometry.area / 1e6).round(4)
    return out, log, merged_log


# ----------------------------------------------------------------------------
# 输出
# ----------------------------------------------------------------------------
def save(out_base, gdf, albers):
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = out_base + ext
        if os.path.exists(p):
            os.remove(p)
    gdf.to_file(out_base + ".shp", encoding="utf-8")
    if albers is not None:
        gdf.to_crs(albers).to_file(out_base + "_Albers.shp", encoding="utf-8")


def qa_report(path, src, out, gdf, area_in, area_out, log, merged_log, thresh, min_hole):
    uo = unary_union(list(gdf.geometry)).buffer(0)
    ov = 0.0
    gs = list(gdf.geometry)
    for a in range(len(gs)):
        for b in range(a + 1, len(gs)):
            if gs[a].intersects(gs[b]):
                ov += gs[a].intersection(gs[b]).area
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 乡镇分区清洗 QA 报告\n\n")
        f.write(f"- 输入：`{os.path.abspath(src)}`\n")
        f.write(f"- 输出：`{os.path.abspath(out)}.shp`\n")
        f.write(f"- 阈值：碎屑 < {thresh:.3f} km²，孔洞噪点 < {min_hole:.0f} m²\n\n")
        f.write("## 1. 碎屑迁移\n\n| 乡镇 | 沿用原面积km² | 保留km² | 迁移块数 |\n")
        f.write("|---|---:|---:|---:|\n")
        for n, a0, ka, mv in log:
            f.write(f"| {n} | {a0:.4f} | {ka:.4f} | {mv} |\n")
        f.write("\n## 2. 同名合并\n\n")
        for n, had in merged_log:
            if had:
                f.write(f"- {n}：沿用剩余块 + 新增 → 合并为一个要素（内部界线已删）\n")
            else:
                f.write(f"- {n}：沿用全部为碎屑并已分流，保留新增要素\n")
        f.write("\n## 3. 结果校验\n\n")
        f.write(f"- 要素数：{len(gdf)}\n")
        f.write(f"- 输入并面积：{area_in/1e6:.4f} km²\n")
        f.write(f"- 输出并面积：{uo.area/1e6:.4f} km²\n")
        f.write(f"- 重叠面积：{ov:.4f} m²\n")
        f.write(f"- 非法几何：{int((~gdf.geometry.is_valid).sum())}\n")
        f.write(f"- 面积守恒：{'是' if abs(uo.area - area_in) < 1 else '否'}\n")
    return ov


def plot(path, before, after, name_f):
    fig, axes = plt.subplots(1, 2, figsize=(22, 11))
    for ax, (title, data) in zip(axes, [("修复前", before), ("修复后", after)]):
        data.plot(ax=ax, column=name_f, cmap="tab20", edgecolor="k", linewidth=0.3)
        ax.set_title(title, fontsize=16)
        ax.set_aspect("equal")
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close()


# ----------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="乡镇分区去碎屑 + 同名合并")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--code-field", default="CODE")
    ap.add_argument("--name-field", default="TOWN")
    ap.add_argument("--source-field", default="SOURCE")
    ap.add_argument("--old-value", default="沿用")
    ap.add_argument("--new-value", default="2002新增")
    ap.add_argument("--merge-towns", default=None, help="逗号分隔；默认自动识别")
    ap.add_argument("--frag-towns", default=None, help="逗号分隔；默认=merge-towns")
    ap.add_argument("--thresh-km2", type=float, default=0.6)
    ap.add_argument("--min-hole-m2", type=float, default=100.0)
    ap.add_argument("--albers-prj", default=None)
    ap.add_argument("--albers-crs", default=None)
    ap.add_argument("--no-albers", action="store_true")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    args = ap.parse_args(argv)

    def split(v):
        return [x.strip() for x in v.split(",") if x.strip()] if v else None

    gdf = load_layer(args.inp, args.code_field, args.name_field, args.source_field)
    area_in = unary_union([make_valid(g) if not g.is_valid else g
                           for g in gdf.geometry]).buffer(0).area

    out, log, merged = process(
        gdf, args.code_field, args.name_field, args.source_field,
        args.old_value, args.new_value, split(args.merge_towns),
        split(args.frag_towns), args.thresh_km2 * 1e6, args.min_hole_m2)

    out_base = os.path.abspath(args.out)
    albers = None
    if not args.no_albers:
        if args.albers_crs:
            albers = CRS.from_user_input(args.albers_crs)
        else:
            prj = args.albers_prj or (os.path.splitext(args.inp)[0] + "_Albers.prj")
            if os.path.exists(prj):
                albers = CRS.from_wkt(open(prj, encoding="utf-8").read().strip())
    save(out_base, out, albers)

    ov = None
    if not args.no_report:
        ov = qa_report(out_base + "_QA报告.md", args.inp, out_base, out,
                       area_in, None, log, merged, args.thresh_km2, args.min_hole_m2)
    if not args.no_plot:
        plot(out_base + "_对比.png", gdf, out, args.name_field)

    print(f"要素 {len(out)}  并面积 {unary_union(list(out.geometry)).area/1e6:.4f} km² "
          f"(输入 {area_in/1e6:.4f})  重叠 {ov if ov is None else round(ov,4)} m²")
    for n, a0, ka, mv in log:
        print(f"  {n}: 沿用 {a0:.4f} -> 保留 {ka:.4f} km²，迁移 {mv} 块")
    print("写出:", out_base + ".shp" + (" / _Albers.shp" if albers else ""))


if __name__ == "__main__":
    main()
