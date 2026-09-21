#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
fix_slivers.py —— 行政区划矢量图层「细小间隙 / 碎屑多边形 / 重叠」一体化修复工具
===============================================================================

适用问题
--------
相邻多边形之间（或本图层与底图 / 参考图层之间）出现细缝（Gap / Sliver Polygon）、
细长碎屑、重叠（Overlap），导致：
    * 叠加时无法完全重合，视觉上出现"双线 + 细黑缝"
    * 拓扑校验报错（overlap / gap / invalid）
    * 面积统计偏差（碎屑归属错误）

两种修复模式
------------
【topo】自洽修复（默认，无需参考图层）
    1. 几何有效性修复      make_valid / buffer(0) 兜底
    2. 精度归一            set_precision 定点化，消灭浮点噪声造成的微缝
    3. 邻居顶点吸附        snap：把相距 < snap_tol 的相邻边界顶点互相吸附
    4. 缝隙检测与归属      polygonize 平面图 + 形态学闭运算 双通道检测，
                           按「同码优先 → 最长公共边界 → 最大接触面积」归属（Eliminate 等价）
    5. 重叠剔除            面积大者优先，记录被剔除面积
    6. 分区归一            保证输出 = 无缝、无叠、精确覆盖域
    7. 可选按字段融合      同码多部件合并（消除图层内部"假缝隙"）

【snap-ref】配准式修复（推荐，需要 --ref 权威参考图层）
    在 topo 全部步骤之前，先做「形态学编辑保真配准」：
        A_new = R_A ∪ opening(A \ R_A, r)  \  opening(R_A \ A, r)
    即：与权威边界差异 *厚度 < 2r* 的部分判为配准噪声（1px 级错位）直接抹平，
        差异 *厚度 > 2r* 的部分判为真实改动（人工修改）予以保留。
    效果：叠加后与参考图层在容差范围内完全重合，同时不丢真实编辑。

用法
----
# ① 只诊断，不改数据（强烈建议先跑）
python fix_slivers.py --input 三亚乡镇2002_123.shp --diagnose

# ② 自洽修复（没有参考图层时）
python fix_slivers.py --input xxx.shp --output xxx_fixed

# ③ 配准式修复（本例推荐）
python fix_slivers.py --input 三亚乡镇2002_123.shp \
    --ref ../../海南村界.shp --ref-key 乡镇码 --key 乡镇码 \
    --mode snap-ref --conflate-tol 90 --dissolve-by 乡镇码

依赖：geopandas >= 0.14, shapely >= 2.0, pandas, numpy, matplotlib(仅出图)
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd
import geopandas as gpd

import shapely
from shapely import (GeometryCollection, MultiPolygon, Polygon, box, get_parts,
                     make_valid, set_precision, snap, unary_union)
from shapely.geometry import MultiLineString

try:  # shapely >= 2.0
    from shapely import polygonize as _polygonize
except ImportError:                                        # pragma: no cover
    from shapely.ops import polygonize as _polygonize      # type: ignore

# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

EMPTY = Polygon()


def log(msg: str = "") -> None:
    print(msg, flush=True)


class Section:
    """控制台小节标题"""

    def __init__(self, title: str):
        self.title = title

    def __enter__(self):
        log("")
        log("─" * 78)
        log(f"▶ {self.title}")
        log("─" * 78)
        return self

    def __exit__(self, *exc):
        return False


def to_polygonal(geom):
    """把任意几何规整为 (Multi)Polygon；无法规整时返回 None。"""
    if geom is None:
        return None
    try:
        if geom.is_empty:
            return None
    except Exception:
        return None
    gt = geom.geom_type
    if gt in ("Polygon", "MultiPolygon"):
        return geom
    if gt == "GeometryCollection":
        parts = [p for g in geom.geoms for p in get_parts(g)
                 if p.geom_type in ("Polygon", "MultiPolygon")]
        return unary_union(parts) if parts else None
    return None


def drop_micro_holes(geom, min_hole_area: float):
    """删除面积小于阈值的内部孔洞与微小外环——GEOS 布尔运算常见的纳米级伪影。

    注意：这只影响 <min_hole_area 的孔洞；真实湖泊/飞地（通常远大于 1 m²）不受影响。
    """
    if geom is None or geom.is_empty or min_hole_area <= 0:
        return geom
    try:
        changed = False
        new_parts = []
        for p in parts_of(geom):
            keep = [r for r in p.interiors if Polygon(r).area >= min_hole_area]
            if len(keep) != len(p.interiors):
                changed = True
                new_parts.append(Polygon(p.exterior, keep))
            else:
                new_parts.append(p)
        if not changed:
            return geom
        return unary_union(new_parts) if len(new_parts) > 1 else new_parts[0]
    except Exception:
        return geom


def repair_validity(geom, grid: float = 0.001):
    """修复非法几何（自相交、环重复、孔洞外露等）。

    按「set_precision → make_valid(structure) → make_valid(linework) → buffer(0)」
    依次尝试，从所有合法候选里挑选 *面积最接近原几何、部件数最少* 的一个，
    避免 make_valid(linework) 把面炸成上千个碎片。
    """
    if geom is None or geom.is_empty:
        return None
    if geom.is_valid and geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom

    cands = []

    def _add(g):
        g2 = to_polygonal(g)
        if g2 is not None and not g2.is_empty and g2.area > 0 and g2.is_valid:
            cands.append(g2)

    if grid and grid > 0:
        try:
            _add(set_precision(geom, grid))
        except Exception:
            pass
    for method in ("structure", "linework"):
        try:
            _add(make_valid(geom, method=method))
        except TypeError:                                   # 老版本 shapely
            try:
                _add(make_valid(geom))
            except Exception:
                pass
            break
        except Exception:
            pass
    try:
        _add(geom.buffer(0))
    except Exception:
        pass

    if not cands:
        return None
    a0 = float(geom.area)
    cands.sort(key=lambda g: (abs(g.area - a0) / max(a0, 1e-9), len(parts_of(g))))
    return cands[0]


def precision_normalize(geom, grid: float):
    """用固定精度网格消除浮点噪声（消灭 <grid 的伪缝隙）。失败则原样返回。"""
    if grid and grid > 0:
        try:
            g = to_polygonal(set_precision(geom, grid))
            if g is not None and not g.is_empty and g.area > 0 and g.is_valid:
                return g
        except Exception:
            pass
    return geom


def width_index(geom) -> float:
    """细长度指标：2A/P。细长矩形 ≈ 宽度；圆形 ≈ 半径。"""
    p = geom.length
    return (2.0 * geom.area / p) if p > 0 else 0.0


def open_morph(geom, r: float):
    """形态学开运算（先腐蚀后膨胀）：保留厚度 > 2r 的部分，抹掉细丝/细缝。"""
    if r <= 0 or geom.is_empty:
        return geom
    try:
        g = geom.buffer(-r, quad_segs=16, join_style=1).buffer(r, quad_segs=16, join_style=1)
        return g if not g.is_empty else EMPTY
    except Exception:
        return geom


def shared_border_len(a, b, tol: float) -> float:
    """a 的边界落在 b 边界 tol/2 邻域内的长度 ≈ 两者公共边界长度。"""
    try:
        inter = a.boundary.intersection(b.boundary.buffer(tol / 2.0, quad_segs=6))
        return float(inter.length)
    except Exception:
        return 0.0


def parts_of(geom):
    return [p for p in get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon")]


def union_all(geoms):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    return unary_union(geoms) if geoms else EMPTY


def drop_existing(base: str) -> None:
    """写出前清掉同名旧文件（Shapefile 由多个同名文件组成，GDAL 覆写会先删除）。"""
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx",
                ".shp.xml", ".geojson"):
        p = base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError as e:
                log(f"  ⚠ 无法删除已存在的 {os.path.basename(p)}：{e}"
                    f"（写盘可能失败，请先手动删除）")


# --------------------------------------------------------------------------- #
# 1. 读入 / 单位推断
# --------------------------------------------------------------------------- #

def read_layer(path: str) -> gpd.GeoDataFrame:
    last = None
    for enc in (None, "gbk", "utf-8"):
        try:
            kw = {} if enc is None else {"encoding": enc}
            return gpd.read_file(path, **kw)
        except Exception as e:      # pragma: no cover
            last = e
    raise RuntimeError(f"读取失败: {path} -> {last}")


def auto_tolerance(gdf: gpd.GeoDataFrame, tol: float | None, what: str = "snap_tol"):
    """根据 CRS 单位推断容差：投影坐标(m)直接用；地理坐标(度)换算。"""
    crs = gdf.crs
    if crs is None:
        if tol is None:
            raise SystemExit(
                "✗ 图层没有 CRS。请先用 gdf.set_crs(...) 指定坐标系，"
                "否则无法给出以米为单位的容差。")
        return tol, "unknown"
    if crs.is_geographic:
        deg = (tol / 111320.0) if tol is not None else (30.0 / 111320.0)
        log(f"  ⚠ CRS 为地理坐标({crs.to_string()})，{what} 已由 {tol or 30:.1f} m "
            f"换算为 {deg:.8f} 度；建议先投影到等积/等距坐标系后再做几何修复。")
        return deg, "degree"
    return (tol if tol is not None else 30.0), "meter"


# --------------------------------------------------------------------------- #
# 2. 诊断
# --------------------------------------------------------------------------- #

def diagnose(gdf: gpd.GeoDataFrame, key: str | None, snap_tol: float,
             max_gap_width: float, domain=None) -> dict:
    geoms = list(gdf.geometry)
    n = len(geoms)
    reps: dict = {}

    with Section("① 基础信息 / 几何有效性与重复"):
        invalid = int(sum(1 for g in geoms if not g.is_valid))
        empty = int(sum(1 for g in geoms if g is None or g.is_empty))
        nonpoly = int(sum(1 for g in geoms if g.geom_type not in ("Polygon", "MultiPolygon")))
        dup = int(gdf.geometry.to_wkb().duplicated().sum())
        log(f"  要素数            : {n}")
        log(f"  CRS               : {gdf.crs}")
        log(f"  非法几何          : {invalid}")
        log(f"  空几何            : {empty}")
        log(f"  非面几何          : {nonpoly}")
        log(f"  几何完全重复      : {dup}")
        reps.update(n=n, invalid=invalid, empty=empty, nonpolygon=nonpoly, duplicated=dup)

    with Section("② 部件数与碎屑（过度分割诊断）"):
        n_parts, thin = 0, []
        for g in geoms:
            if g is None or g.is_empty:
                continue
            for p in parts_of(g):
                n_parts += 1
                if p.area > 0 and width_index(p) < snap_tol:
                    thin.append((p.area, width_index(p)))
        log(f"  多边形部件总数    : {n_parts}   (要素数 {n})")
        if thin:
            thin.sort(key=lambda x: -x[0])
            log(f"  细长部件(<{snap_tol:.0f}m 宽) : {len(thin)} 个，"
                f"合计 {sum(a for a, _ in thin) / 1e6:.4f} km²")
            for a, w in thin[:8]:
                log(f"      · 面积 {a:12.1f} m²  宽度指标 {w:7.1f} m")
        else:
            log(f"  细长部件(<{snap_tol:.0f}m 宽) : 0")
        reps.update(parts=n_parts, thin_parts=len(thin),
                    thin_area=float(sum(a for a, _ in thin)))

    with Section("③ 重叠 / 空隙 / 封闭孔洞"):
        # 重叠（严格面积法）
        total_union = union_all(geoms)
        sum_area = float(sum(g.area for g in geoms if g is not None and not g.is_empty))
        ov_area = sum_area - total_union.area
        holes = sum(len(p.interiors) for p in parts_of(total_union))
        log(f"  面积之和          : {sum_area / 1e6:.4f} km²")
        log(f"  合并后面积        : {total_union.area / 1e6:.4f} km²")
        log(f"  重叠面积(和-并)   : {ov_area:.1f} m²")
        log(f"  封闭孔洞(内环)    : {holes} 个")
        reps.update(sum_area=sum_area, union_area=float(total_union.area),
                    overlap_area=float(ov_area), interior_rings=holes)

    with Section("④ 相邻要素最小间距分布（细缝宽度体检）"):
        buckets = Counter()
        dmin = []
        for i in range(n):
            for j in range(i + 1, n):
                a, b = geoms[i], geoms[j]
                if a is None or b is None or a.is_empty or b.is_empty:
                    continue
                try:
                    d = a.distance(b)
                except Exception:
                    continue
                if d <= 0:
                    continue
                if d <= max_gap_width * 3:
                    dmin.append(d)
        if dmin:
            arr = np.array(dmin)
            log(f"  间距 ≤ {max_gap_width * 3:.0f} m 的相邻要素对: {len(arr)}")
            log(f"     最小 {arr.min():.1f} m | 中位 {np.median(arr):.1f} m | "
                f"均值 {arr.mean():.1f} m | 最大 {arr.max():.1f} m")
            for t in (max_gap_width, max_gap_width * 2, max_gap_width * 3):
                buckets[f"<={t:.0f}m"] = int((arr <= t).sum())
            log(f"     分布: {dict(buckets)}")
        else:
            log("  未发现间距较小的相邻要素对（图层内部无细缝）")
        reps.update(close_pairs=len(dmin),
                    min_pair_distance=float(min(dmin)) if dmin else None)

    with Section("⑤ 真缝隙检测（平面图 + 形态学闭运算 + 域约束）"):
        gaps = find_gaps(geoms, domain, max_gap_width, min_touch=2)
        src = Counter(s for _, s, _, _ in gaps)
        tot = sum(g.area for g, _, _, _ in gaps)
        log(f"  检出缝隙面        : {len(gaps)} 个，合计 {tot / 1e6:.5f} km²")
        log(f"  来源分布          : {dict(src) if src else '无'}")
        for g, s, w, t in sorted(gaps, key=lambda x: -x[0].area)[:8]:
            log(f"      · {s:9s} 面积 {g.area:12.1f} m²  宽度 {w:6.1f} m  接触 {t} 个要素")
        reps.update(gap_count=len(gaps), gap_area=float(tot),
                    gap_sources={k: int(v) for k, v in src.items()})

    return reps


# --------------------------------------------------------------------------- #
# 3. 缝隙检测与归属
# --------------------------------------------------------------------------- #

def find_gaps(geoms, domain, max_gap_width: float, min_touch: int = 2):
    """三通道检测"细小间隙"。

    通道1 polygonize：由所有边界线构成的平面图里，不被任何要素覆盖的封闭面；
    通道2 形态学闭运算：(U ⊕ r) ⊖ r − U，捕捉开口（通向外部）的细缝；
    通道3 域约束：domain − U，参考域内本图层缺失的部分。

    过滤：宽度 ≤ max_gap_width、面积上限、至少接触 min_touch 个要素
    （接触 1 个的是海岸凹湾、接触 0 个的是开阔海域，都不应被填掉）。
    """
    valid = [g for g in geoms if g is not None and not g.is_empty and g.area > 0]
    if not valid:
        return []
    cov = union_all(valid)
    cand: dict = {}

    # 通道 1
    try:
        lines = [g.boundary for g in valid]
        fc = _polygonize(lines)
        faces = list(fc.geoms) if hasattr(fc, "geoms") else []
        for f in faces:
            try:
                if f.area > 0 and cov.intersection(f).area < f.area * 1e-9:
                    cand[f] = "polygonize"
            except Exception:
                continue
    except Exception:
        pass

    # 通道 2
    r = max_gap_width / 2.0
    try:
        closed = cov.buffer(r, quad_segs=16, join_style=1).buffer(-r, quad_segs=16, join_style=1)
        for p in parts_of(closed.difference(cov)):
            if p.area > 0:
                cand.setdefault(p, "closing")
    except Exception:
        pass

    # 通道 3
    if domain is not None and not domain.is_empty:
        try:
            for p in parts_of(domain.difference(cov)):
                if p.area > 0:
                    cand.setdefault(p, "domain")
        except Exception:
            pass

    area_cap = max_gap_width * max_gap_width * 400.0   # 防止把大块缺失当细缝填掉
    out = []
    for f, src in cand.items():
        try:
            if f.area <= 0 or f.area > area_cap:
                continue
            w = 2.0 * f.area / f.length if f.length > 0 else 0.0
            if w > max_gap_width:
                continue
            touch = sum(1 for g in valid if g.intersects(f))
            if touch < min_touch:
                continue
            out.append((f, src, w, touch))
        except Exception:
            continue
    # 去重（互相高度重叠的候选只留一个）
    out.sort(key=lambda x: -x[0].area)
    kept = []
    for item in out:
        f = item[0]
        if any(f.intersection(k[0]).area > 0.7 * f.area for k in kept):
            continue
        kept.append(item)
    return kept


def assign_gaps(geoms, labels, gaps, tol: float, prefer_same_code: bool = True):
    """把每个缝隙面分配给"最合理的邻居"（ArcGIS Eliminate 的等价实现）。

    优先级：① 与缝隙主邻居同码/同类者优先  ② 公共边界最长  ③ 接触面积最大

    性能：用 STRtree 先做候选筛选，并预先算好各要素边界的缓冲区，
    避免"缝隙数 × 要素数"次大边界 buffer（这是本工具最容易爆掉的地方）。
    """
    log_rows = []
    if not gaps:
        return geoms, log_rows
    n = len(geoms)
    bnd_buf = []
    for g in geoms:
        try:
            bnd_buf.append(g.boundary.buffer(tol / 2.0, quad_segs=6) if not g.is_empty else EMPTY)
        except Exception:
            bnd_buf.append(EMPTY)
    try:
        from shapely import STRtree
        tree = STRtree(geoms)
        use_tree = True
    except Exception:
        tree, use_tree = None, False

    last_owner = None
    for gap, src, w, _ in gaps:
        if gap.is_empty or gap.area <= 0:
            continue
        try:
            gbuf = gap.buffer(tol, quad_segs=4)
        except Exception:
            continue
        if use_tree:
            try:
                cand = list(tree.query(gbuf, predicate="intersects"))
            except Exception:
                cand = list(range(n))
        else:
            cand = list(range(n))
        cand = [i for i in cand if not geoms[i].is_empty]
        if not cand:
            continue
        # 公共边界长度 / 接触面积
        border = np.zeros(n)
        contact = np.zeros(n)
        for i in cand:
            try:
                border[i] = gap.boundary.intersection(bnd_buf[i]).length
            except Exception:
                border[i] = 0.0
            try:
                contact[i] = gap.intersection(geoms[i]).area
            except Exception:
                contact[i] = 0.0
        score = border * 1000.0 + contact
        if score.max() <= 0:
            continue
        dom = int(np.argmax(score))
        if prefer_same_code and labels is not None:
            same = np.zeros(n)
            for i in cand:
                if str(labels[i]) == str(labels[dom]):
                    same[i] = 1.0
            score = score * (1.0 + 3.0 * same)      # 同码加权 4 倍
            tgt = int(np.argmax(score))
            if score[tgt] <= 0:
                tgt = dom
        else:
            tgt = dom
        log_rows.append(dict(gap_area=float(gap.area), width=float(w), source=src,
                             owner=tgt, owner_label=str(labels[tgt]) if labels is not None else "",
                             dominant=dom,
                             dominant_label=str(labels[dom]) if labels is not None else ""))
        try:
            geoms[tgt] = unary_union([geoms[tgt], gap])
        except Exception:
            pass
    return geoms, log_rows


# --------------------------------------------------------------------------- #
# 4. 重叠剔除 / 分区归一
# --------------------------------------------------------------------------- #

def _robust_difference(a, b, grid: float):
    """对 a∖b 做鲁棒差集：GEOS 数值退化时按更粗网格规范化 b 逐级重试。"""
    try:
        d = a.difference(b)
        if d is not None and not d.is_empty and d.is_valid:
            return d
    except Exception:
        pass
    for brk in (0.01, 0.05, max(grid, 0.001), 0.1, 0.5, 1.0, 5.0):
        try:
            bb = to_polygonal(set_precision(b, brk))
            if bb is None or bb.is_empty:
                continue
            d = a.difference(bb)
            if d is not None and not d.is_empty and d.is_valid:
                return d
        except Exception:
            continue
    return a


def remove_overlaps(geoms, tol: float, grid: float = 0.001):
    """顺序占用法：面积大者优先占据重叠区，保证互斥。返回 (新几何, 剔除面积)。"""
    order = sorted(range(len(geoms)), key=lambda i: -geoms[i].area)
    out = [EMPTY] * len(geoms)
    occ = None
    removed = 0.0
    for i in order:
        g = geoms[i]
        if occ is not None and not occ.is_empty:
            clipped = _robust_difference(g, occ, grid)
            removed += max(0.0, g.area - clipped.area)
            g = clipped
        g = to_polygonal(make_valid(g)) if (g is not None and not g.is_empty and not g.is_valid) else g
        out[i] = g if g is not None else EMPTY
        if out[i] is not None and not out[i].is_empty:
            occ = out[i] if occ is None else unary_union([occ, out[i]])
    return out, removed


def _owner_by_overlap(frag, cand_geoms, tree, thresh=0.5):
    """在候选要素里找覆盖 frag 面积最大的那个（覆盖率 > thresh 才算）。"""
    idx = list(range(len(cand_geoms)))
    if tree is not None:
        try:
            idx = list(tree.query(frag.buffer(1.0, quad_segs=4), predicate="intersects"))
        except Exception:
            idx = list(range(len(cand_geoms)))
    best, bestov = None, 0.0
    for j in idx:
        g = cand_geoms[j]
        if g is None or g.is_empty:
            continue
        try:
            ov = frag.intersection(g).area
        except Exception:
            continue
        if ov > bestov:
            bestov, best = ov, j
    if best is not None and bestov >= thresh * frag.area:
        return best
    return None


def _owner_by_border(frag, geoms, labels, tol, prefer_same_code=True, tree=None,
                     bnd_buf=None):
    """按"公共边界最长 → 接触面积最大"归属（Eliminate 规则）。

    bnd_buf：可选的预计算边界缓冲区列表（与 geoms 等长）。当待处理碎片很多时
    必须传入，否则会对每个碎片重复缓冲上万顶点的要素边界，直接拖死进程。
    """
    idx = list(range(len(geoms)))
    if tree is not None:
        try:
            idx = list(tree.query(frag.buffer(tol, quad_segs=4), predicate="intersects"))
        except Exception:
            idx = list(range(len(geoms)))
    best, bestscore = None, 0.0
    for j in idx:
        g = geoms[j]
        if g is None or g.is_empty:
            continue
        try:
            sh = frag.intersection(g).area
        except Exception:
            sh = 0.0
        try:
            bb = bnd_buf[j] if (bnd_buf is not None and j < len(bnd_buf)) else None
            if bb is None:
                bb = g.boundary.buffer(tol / 2, quad_segs=6)
            bl = frag.boundary.intersection(bb).length
        except Exception:
            bl = 0.0
        score = bl * 1000.0 + sh
        if score > bestscore:
            bestscore, best = score, j
    return best


def resolve_partition(geoms, domain, labels, orig_geoms, ref_geoms, tol: float,
                      max_gap_width: float, prefer_same_code: bool = True,
                      min_area: float = 1.0, max_frags: int = 20000,
                      grid: float = 0.001, min_hole_area: float = 1.0):
    """把一组多边形规整成 `domain` 的**精确分区**（无重叠、无空洞、面积守恒）。

    核心规则 —— 争议区统一按「原始图层归属」裁定：

        图层 A 与参考层配准后，A' 之间会同时出现**重叠**（两边都说是自己的）
        和**空洞**（原始图层有、现在没人认领）。两者的正确答案是同一个：
        该地块在**原始图层**里属于谁，配准后就还归谁。

        这条规则等价于「Eliminate + 分区重建」，既能消缝消叠，又完整保住
        原始图层的拓扑语义（真实改动不会因为配准而丢失）。

    归属回退链：原始图层归属 → 参考层归属 → 最长公共边界 → 放弃（并报告）。

    收尾还会做一次「精度归一 + 微空洞回填」，消除 GEOS 布尔运算产生的
    纳米级伪影（否则会残留上千个面积不足 1 m² 的假孔洞）。
    """
    n = len(geoms)
    log_rows = []
    if n == 0:
        return geoms, 0.0, log_rows, 0.0, 0

    orig = [g for g in orig_geoms]
    refs = [g for g in (ref_geoms or [])] or None
    try:
        from shapely import STRtree
        t_orig = STRtree([g for g in orig if g is not None and not g.is_empty])
        t_now = STRtree([g for g in geoms if g is not None and not g.is_empty])
        t_ref = STRtree([g for g in refs if g is not None and not g.is_empty]) if refs else None
    except Exception:
        t_orig = t_now = t_ref = None

    # ---------- ① 收集争议区碎片（重叠 + 空洞） ----------
    frags = []            # (碎片, 冲突方索引 或 None)
    pairs = []
    if t_now is not None:
        try:
            idx_pairs = t_now.query([g for g in geoms if g is not None and not g.is_empty],
                                    predicate="intersects")
            seen = set()
            for a, b in zip(idx_pairs[0], idx_pairs[1]):
                if a == b or (b, a) in seen:
                    continue
                seen.add((a, b))
                pairs.append((int(a), int(b)))
        except Exception:
            pairs = []
    if not pairs:
        pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    for i, j in pairs:
        if i >= n or j >= n:
            continue
        a, b = geoms[i], geoms[j]
        if a is None or a.is_empty or b is None or b.is_empty:
            continue
        try:
            if not a.intersects(b):
                continue
            inter = a.intersection(b)
        except Exception:
            continue
        if inter is not None and not inter.is_empty:
            for p in parts_of(inter):
                if p.area >= min_area:
                    frags.append((p, j))
    # 空洞 = 权威域 − 当前覆盖
    if domain is not None and not domain.is_empty:
        try:
            rest = domain.difference(unary_union(geoms))
        except Exception:
            rest = None
        if rest is not None and not rest.is_empty:
            for p in parts_of(rest):
                if p.area >= min_area:
                    frags.append((p, None))

    dropped = []
    if len(frags) > max_frags:
        frags.sort(key=lambda x: -x[0].area)
        dropped = [p for p, _ in frags[max_frags:]]
        frags = frags[:max_frags]

    # ---------- ② 裁定归属 ----------
    try:
        bnd_buf_now = [g.boundary.buffer(tol / 2, quad_segs=6) if not g.is_empty else EMPTY
                       for g in geoms]
    except Exception:
        bnd_buf_now = None
    give = {}
    ov_removed = 0.0
    for frag, conflicter in frags:
        j = _owner_by_overlap(frag, orig, t_orig)
        src = "orig-owner"
        if j is None and refs:
            j = _owner_by_overlap(frag, refs, t_ref)
            src = "ref-owner"
        if j is None:
            j = _owner_by_border(frag, geoms, labels, tol, prefer_same_code,
                                 t_now, bnd_buf_now)
            src = "longest-border"
        if j is None:
            dropped.append(frag)
            continue
        give.setdefault(j, []).append(frag)
        w = 2.0 * frag.area / frag.length if frag.length > 0 else 0.0
        log_rows.append(dict(
            gap_area=float(frag.area), width=float(w),
            source=("thin-gap" if w <= max_gap_width else "contested-blob") + "/" + src,
            owner=int(j), owner_label=str(labels[j]) if labels is not None else "",
            dominant=int(conflicter) if conflicter is not None else -1,
            dominant_label=(str(labels[conflicter]) if (labels is not None and conflicter is not None) else "无归属(空洞)")))

    # ---------- ③ 重建 ----------
    all_given = {j: union_all(ps) for j, ps in give.items()}
    total_given = union_all(list(all_given.values())) if all_given else EMPTY
    out = []
    for i in range(n):
        g = geoms[i]
        plus = all_given.get(i)
        try:
            if plus is not None and not plus.is_empty:
                g = union_all([g, plus])
            # 判给别人的争议区从本要素里挖掉（求差比反复求并省得多）
            if not total_given.is_empty:
                mine = plus if (plus is not None and not plus.is_empty) else EMPTY
                minus = total_given if mine.is_empty else total_given.difference(mine)
                if not minus.is_empty:
                    g = g.difference(minus)
            if domain is not None and not domain.is_empty:
                g = g.intersection(domain)
            g = repair_validity(g) or EMPTY
        except Exception:
            g = geoms[i] if geoms[i] is not None else EMPTY
        out.append(g)
    ov_removed = float(total_given.area)

    # ---------- ③b 收尾：精度归一 + 微孔洞清理 + 微重叠剔除 ----------
    out = [drop_micro_holes(g, min_hole_area) if (g is not None and not g.is_empty) else EMPTY
           for g in out]
    out = [precision_normalize(g, grid) if (g is not None and not g.is_empty) else EMPTY
           for g in out]
    out, _ = remove_overlaps(out, tol, grid=grid)
    out = [drop_micro_holes(g, min_hole_area) if (g is not None and not g.is_empty) else EMPTY
           for g in out]
    out = [precision_normalize(g, grid) if (g is not None and not g.is_empty) else EMPTY
           for g in out]
    # 纳米级微空洞回填：只处理"肉眼可见"的（≥ min_area），
    # 更小的布尔运算伪影直接计入残余并报告——逐个归属的代价远大于收益。
    if domain is not None and not domain.is_empty:
        try:
            t_now2 = STRtree([g for g in out if g is not None and not g.is_empty])
            bnd_buf2 = [g.boundary.buffer(tol / 2, quad_segs=6) if not g.is_empty else EMPTY
                        for g in out]
        except Exception:
            t_now2, bnd_buf2 = None, None
        micro = []
        micro_skip_area, micro_skip_cnt = 0.0, 0
        try:
            for p in parts_of(domain.difference(union_all(out))):
                if p.area <= 0:
                    continue
                if p.area < max(min_area, 1.0):
                    micro_skip_area += p.area
                    micro_skip_cnt += 1
                    continue
                j = _owner_by_border(p, out, labels, tol, prefer_same_code, t_now2, bnd_buf2)
                if j is not None:
                    micro.append((p, j))
        except Exception:
            micro = []
        for p, j in micro:
            try:
                out[j] = precision_normalize(unary_union([out[j], p]), grid) or out[j]
            except Exception:
                pass

    # ---------- ④ 统计未能归属的残余 ----------
    cov = union_all(out)
    left_area, left_cnt = 0.0, 0
    if domain is not None and not domain.is_empty:
        try:
            left = domain.difference(cov)
            lp = [p for p in parts_of(left) if p.area > 0]
            left_area = float(sum(p.area for p in lp))
            left_cnt = len(lp)
        except Exception:
            pass
    left_area += float(sum(p.area for p in dropped))
    return out, ov_removed, log_rows, left_area, left_cnt + len(dropped)


# --------------------------------------------------------------------------- #
# 5. 邻居顶点吸附
# --------------------------------------------------------------------------- #

def snap_neighbors(geoms, tol: float, passes: int = 2, labels=None, grid: float = 0.001):
    """迭代式邻居顶点吸附：把相距 < tol 的边界顶点互相吸附，令共享边界重合。

    为避免"长边只有一个顶点"的问题，先把邻居边界按 tol/2 加密，使参考线顶点
    足够密，从而实现近似"顶点→线段"的吸附。

    健壮性守卫（很重要）：吸附会让近乎重合的边界产生大量自相交，进而使
    make_valid 把面炸成上千个碎片。因此对每个要素的吸附结果做三重校验——
    合法性、面积变化率、部件数增幅——任一不过就放弃该轮结果、保留原几何。
    """
    cur = list(geoms)
    moved_total = 0.0
    rejected = 0
    for p in range(passes):
        new = list(cur)
        moved = 0
        for i in range(len(cur)):
            gi = cur[i]
            if gi.is_empty:
                continue
            refs = []
            for j in range(len(cur)):
                if i == j or cur[j].is_empty:
                    continue
                try:
                    if gi.distance(cur[j]) <= tol:
                        refs.append(cur[j].boundary)
                except Exception:
                    continue
            if not refs:
                continue
            ref = union_all(refs)
            try:
                ref = shapely.segmentize(ref, tol / 2.0)
                snapped = snap(gi, ref, tol)
                if not snapped.is_valid:
                    snapped = repair_validity(snapped, grid)
                if snapped is None or snapped.is_empty or not snapped.is_valid:
                    rejected += 1
                    continue
                a0, a1 = float(gi.area), float(snapped.area)
                if abs(a1 - a0) > 0.25 * max(a0, 1.0):        # 区域巨变 → 失败
                    rejected += 1
                    continue
                if len(parts_of(snapped)) > max(50, 3 * len(parts_of(gi))):  # 碎片爆炸
                    rejected += 1
                    continue
                moved += gi.symmetric_difference(snapped).area
                new[i] = snapped
            except Exception:
                rejected += 1
                continue
        cur = new
        cur, _ = remove_overlaps(cur, tol)
        moved_total += moved
        log(f"    第 {p + 1} 轮吸附：几何改动面积 {moved:,.0f} m²，"
            f"守卫拒绝 {rejected} 次")
        if moved < 1.0:
            break
    return cur


# --------------------------------------------------------------------------- #
# 6. 配准式修复（形态学编辑保真）
# --------------------------------------------------------------------------- #

def conflate_to_reference(geoms, ref_geoms, r: float, log_rows=None, force: bool = False):
    """配准到权威参考边界。

    force=False（默认，编辑保真）：
        A_new = R_A ∪ opening(A \\ R_A, r)  \\  opening(R_A \\ A, r)
        与权威边界差异厚度 < 2r 的部分 → 判为 1px 级配准噪声，抹平；
        差异厚度 > 2r 的部分 → 判为真实人工改动，保留。
    force=True（完全采用参考边界）：A_new = R_A —— 只保留属性，几何全部用权威版，
        叠加 100% 重合，但 2002 年的真实改动会被丢弃。
    """
    out = []
    for i, (a, rr) in enumerate(zip(geoms, ref_geoms)):
        if a is None or a.is_empty:
            out.append(a)
            continue
        if rr is None or rr.is_empty:
            out.append(a)
            log(f"    #{i} 无匹配参考要素，跳过配准")
            continue
        try:
            if force:
                new = repair_validity(rr)
            else:
                grow = a.difference(rr)                  # 本图层比参考多出来的部分
                keep_grow = open_morph(grow, r)          # 只保留厚于 2r 的"真实扩张"
                miss = rr.difference(a)                  # 本图层比参考缺的部分
                keep_miss = open_morph(miss, r)          # 只保留厚于 2r 的"真实缩减"
                new = unary_union([rr, keep_grow]).difference(keep_miss)
                new = repair_validity(new)
            if new is None or new.is_empty:
                out.append(a)
                continue
            if log_rows is not None:
                log_rows.append(dict(feature=i, ref_area=float(rr.area), in_area=float(a.area),
                                     out_area=float(new.area),
                                     noise_removed=float(a.symmetric_difference(rr).area -
                                                         new.symmetric_difference(rr).area)))
            out.append(new)
        except Exception as e:
            log(f"    #{i} 配准异常({e})，保留原几何")
            out.append(a)
    return out


# --------------------------------------------------------------------------- #
# 7. 校验 / 输出
# --------------------------------------------------------------------------- #

def df_to_md(df: pd.DataFrame) -> str:
    """极简 Markdown 表格渲染（不依赖 tabulate）。"""
    try:
        return df.to_markdown()
    except Exception:
        pass
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for idx, row in df.iterrows():
        cells = [str(idx)] + ["" if pd.isna(v) else str(v) for v in row.tolist()]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def verify(gdf: gpd.GeoDataFrame, ref=None, ref_key=None, key=None, domain=None) -> dict:
    """修复后校验。每一项独立 try/except —— 校验失败绝不能影响已写出的数据。"""
    geoms = list(gdf.geometry)
    res: dict = {}

    def safe(name, fn, ndigits=None):
        try:
            v = fn()
            if ndigits is not None and isinstance(v, (int, float, np.floating)):
                v = round(float(v), ndigits)
            res[name] = v
        except Exception as e:
            res[name] = f"ERROR: {e}"

    safe("features", lambda: len(geoms))
    safe("invalid", lambda: int(sum(1 for g in geoms if not g.is_valid)))
    safe("total_area_km2", lambda: float(sum(g.area for g in geoms)) / 1e6, 6)
    safe("sum_minus_union_m2",
         lambda: max(0.0, float(sum(g.area for g in geoms)) - unary_union(geoms).area), 3)
    safe("interior_rings",
         lambda: int(sum(len(p.interiors) for p in parts_of(unary_union(geoms)))))

    if domain is not None and not domain.is_empty:
        safe("domain_area_km2", lambda: float(domain.area) / 1e6, 6)
        safe("domain_missing_m2",
             lambda: float(domain.difference(unary_union(geoms)).area), 3)
        safe("domain_excess_m2",
             lambda: float(unary_union(geoms).difference(domain).area), 3)

    # 逐要素对称差 —— 这才是"叠加不重合"的真实指标
    if ref is not None and len(ref) and ref_key and key \
            and ref_key in ref.columns and key in gdf.columns:
        def _internal():
            a = gdf.dissolve(by=key).geometry
            b = ref.dissolve(by=ref_key).geometry
            bands, sd, n = [], 0.0, 0
            for k in a.index:
                if k not in b.index:
                    continue
                d = a[k].symmetric_difference(b[k])
                if d is not None and not d.is_empty:
                    bands.append(d)
                    sd += float(d.area)
                n += 1
            # 注意：相邻两个乡镇在交界处"你多我少"时，同一条错位带会被
            # A 记一次、B 记一次；合并后才是真正的物理不重合面积。
            band = union_all(bands) if bands else None
            return dict(matched=n,
                        symdiff_sum_km2=round(sd / 1e6, 6),
                        mismatch_band_km2=round(float(band.area) / 1e6, 6)
                        if band is not None else 0.0)
        safe("internal_symdiff", _internal)
    return res


def print_verify(v: dict) -> None:
    def g(k, default="?"):
        return v.get(k, default)

    log(f"  要素数            : {g('features')}")
    log(f"  非法几何          : {g('invalid')}")
    log(f"  总面积            : {g('total_area_km2')} km²")
    log(f"  重叠面积(和-并)   : {g('sum_minus_union_m2')} m²")
    log(f"  封闭孔洞(内环)    : {g('interior_rings')}")
    if "domain_area_km2" in v:
        log(f"  域面积            : {g('domain_area_km2')} km²")
        log(f"  域内缺口          : {g('domain_missing_m2')} m²")
        log(f"  域外溢出          : {g('domain_excess_m2')} m²")
    if "internal_symdiff" in v:
        s = v["internal_symdiff"]
        if isinstance(s, dict):
            log(f"  逐要素对称差(累加): {s.get('symdiff_sum_km2')} km²"
                f"（{s.get('matched')} 个要素配对；同一错位带两侧各计一次）")
            log(f"  叠加不重合面积    : {s.get('mismatch_band_km2')} km²"
                f"  ← 合并去重后的真实不重合面积")
        else:
            log(f"  内边界对称差      : {s}")


def area_table(before: gpd.GeoDataFrame, after: gpd.GeoDataFrame,
               key: str | None, ref: gpd.GeoDataFrame | None = None):
    def agg(gdf):
        if key and key in gdf.columns:
            return gdf.dissolve(by=key).area / 1e6
        return pd.Series({i: g.area / 1e6 for i, g in enumerate(gdf.geometry)})

    a, b = agg(before), agg(after)
    df = pd.DataFrame({"修复前km2": a.round(4), "修复后km2": b.round(4)})
    df["变化km2"] = (df["修复后km2"] - df["修复前km2"]).round(4)
    df["变化%"] = ((df["变化km2"] / df["修复前km2"].replace(0, np.nan)) * 100).round(3)
    if ref is not None:
        r = agg(ref)
        df["参考km2"] = r.round(4)
        df["修复后-参考km2"] = (df["修复后km2"] - df["参考km2"]).round(4)
        df["修复前-参考km2"] = (df["修复前km2"] - df["参考km2"]).round(4)
    return df


def geom_parts(geom):
    """把任意几何拆成非空部件（不限类型，线面都保留）。"""
    if geom is None:
        return []
    try:
        if geom.is_empty:
            return []
        return [p for p in get_parts(geom) if p is not None and not p.is_empty]
    except Exception:
        return []


def mismatch_band(gdf: gpd.GeoDataFrame, ref: gpd.GeoDataFrame, key: str | None = None):
    """两图层"不重合区域"。

    注意：**必须逐要素（按业务码配对）算对称差再合并**。
    如果直接对两图层的并集求对称差，相邻要素之间的"你多我少"会互相抵消，
    结果接近 0，完全看不出问题 —— 这正是"叠加看着不重合、指标却是 0"的经典陷阱。
    """
    if ref is None or len(ref) == 0 or gdf is None or len(gdf) == 0:
        return None, 0.0
    try:
        if key and key in gdf.columns and key in ref.columns:
            a = gdf.dissolve(by=key).geometry
            b = ref.dissolve(by=key).geometry
            parts, n = [], 0
            for k in a.index:
                if k in b.index:
                    try:
                        d = a[k].symmetric_difference(b[k])
                    except Exception:
                        continue
                    if d is not None and not d.is_empty:
                        parts.append(d)
                        n += 1
            if parts:
                band = union_all(parts)
                return band, float(band.area)
        cov_a = union_all(list(gdf.geometry))
        cov_b = union_all(list(ref.geometry))
        band = cov_a.symmetric_difference(cov_b)
        return band, float(band.area)
    except Exception as e:
        log(f"    (差异带计算失败: {e})")
        return None, 0.0


def plot_before_after(before: gpd.GeoDataFrame, after: gpd.GeoDataFrame,
                      ref: gpd.GeoDataFrame, out_png: str, key: str | None = None,
                      zoom_center=None, zoom_size=9000.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    for f in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS"):
        try:
            matplotlib.rcParams["font.sans-serif"] = [f]
            break
        except Exception:
            continue
    matplotlib.rcParams["axes.unicode_minus"] = False

    def draw(ax, geom, crs, **kw):
        """用正确的 CRS 画几何。

        注意：千万不要给 GeoSeries 硬塞 EPSG:4326 —— geopandas 会判定为地理坐标，
        继而用 y 坐标（投影米制，量级 2e6）去算 1/cos(lat) 的 aspect，
        直接抛 "aspect must be finite and positive"。
        """
        if geom is None:
            return
        ps = geom_parts(geom)
        if not ps:
            return
        gpd.GeoSeries(ps, crs=crs).plot(ax=ax, aspect=None, **kw)

    def draw_gdf(ax, gdf, **kw):
        if gdf is None or len(gdf) == 0:
            return
        m = ~gdf.geometry.is_empty & gdf.geometry.notna()
        g = gdf[m]
        if len(g):
            g.plot(ax=ax, aspect=None, **kw)

    ref_ok = ref is not None and len(ref) > 0

    # 放大窗口：优先对准"修复前差异最大的一块"
    if zoom_center is None:
        band_b, _ = mismatch_band(before, ref, key)
        best = None
        if band_b is not None:
            ps = sorted(geom_parts(band_b), key=lambda p: -p.area)
            if ps:
                best = ps[0]
        if best is not None:
            try:
                b = best.bounds
                cx0, cy0 = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
                span = max(b[2] - b[0], b[3] - b[1], 3000.0)
                zoom_center = (cx0, cy0)
                zoom_size = min(max(float(span) * 1.8, 6000.0), 15000.0)
            except Exception:
                best = None
        if zoom_center is None:
            x0, y0, x1, y1 = before.total_bounds
            zoom_center = ((x0 + x1) / 2, (y0 + y1) / 2)
            zoom_size = max(float(max(x1 - x0, y1 - y0)) / 4.0, 6000.0)
    try:
        cx, cy = float(zoom_center[0]), float(zoom_center[1])
    except Exception:
        cx = cy = float("nan")
    if not (np.isfinite(cx) and np.isfinite(cy)):
        x0, y0, x1, y1 = before.total_bounds
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    if not np.isfinite(zoom_size) or zoom_size <= 0:
        zoom_size = 9000.0
    win = box(cx - zoom_size / 2, cy - zoom_size / 2, cx + zoom_size / 2, cy + zoom_size / 2)

    fig, axes = plt.subplots(2, 2, figsize=(16, 11), dpi=110, facecolor="white",
                             gridspec_kw=dict(wspace=0.06, hspace=0.12))
    seq = [("修复前（原始 SHP）", before, axes[0, 0], axes[1, 0]),
           ("修复后（几何修复结果）", after, axes[0, 1], axes[1, 1])]

    def set_lim(ax, b):
        try:
            x0, y0, x1, y1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
            if not (np.isfinite(x0) and np.isfinite(y0) and np.isfinite(x1)
                    and np.isfinite(y1)):
                return
            if x1 <= x0:
                x1 = x0 + 1000.0
            if y1 <= y0:
                y1 = y0 + 1000.0
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_aspect("equal", adjustable="box")
        except Exception:
            pass

    ov_bounds = before.total_bounds
    for title, gdf, ax_all, ax_zoom in seq:
        band, band_area = mismatch_band(gdf, ref, key) if ref_ok else (None, 0.0)
        # 先定坐标范围，再画图：否则 geopandas 在退化范围上设置 box aspect 会抛错
        set_lim(ax_all, ov_bounds)
        set_lim(ax_zoom, win.bounds)
        try:
            draw_gdf(ax_all, gdf, edgecolor="#8a94a6", linewidth=0.3,
                     facecolor="#eef3f8", alpha=1.0)
            if ref_ok:
                draw(ax_all, union_all(list(ref.geometry)).boundary, ref.crs,
                     color="#d62728", linewidth=0.6)
            if band is not None:
                ps = sorted(geom_parts(band), key=lambda p: -p.area)
                if ps:
                    gpd.GeoSeries(ps, crs=gdf.crs).plot(
                        ax=ax_all, facecolor="#ff3b30", edgecolor="none", alpha=0.75,
                        aspect=None)
        except Exception as e:
            log(f"    (全图面板绘制异常: {e})")
        sub = (f"{title}\n与参考层逐要素不重合面积 {band_area / 1e6:.4f} km²"
               f"（红色=缝隙/错位带）") if ref_ok else title
        ax_all.set_title(sub, fontsize=11)
        ax_all.set_axis_off()

        # ---- 放大 ----
        z_area = 0.0
        try:
            sub_g = gdf[gdf.intersects(win)]
            if len(sub_g):
                sub_g = sub_g[~sub_g.geometry.is_empty]
            draw_gdf(ax_zoom, sub_g, edgecolor="#8a94a6", linewidth=0.4,
                     facecolor="#f2f7fc", alpha=1.0)
            if ref_ok:
                draw(ax_zoom, union_all(list(ref.geometry)).intersection(win).boundary,
                     ref.crs, color="#d62728", linewidth=1.6, linestyle=(0, (5, 3)))
            if band is not None:
                zw = band.intersection(win)
                z_area = float(zw.area)
                ps = sorted(geom_parts(zw), key=lambda p: -p.area)
                if ps:
                    gpd.GeoSeries(ps, crs=gdf.crs).plot(
                        ax=ax_zoom, facecolor="#ff3b30", edgecolor="none", alpha=0.8,
                        aspect=None)
        except Exception as e:
            log(f"    (放大面板绘制异常: {e})")
        ax_zoom.set_title(f"局部放大（{zoom_size / 1000:.1f} km 视窗）"
                          f"  红色虚线=参考层边界，橙色=错位带 {z_area / 1e6:.5f} km²",
                          fontsize=9.5)
        set_lim(ax_zoom, win.bounds)
        ax_zoom.set_axis_off()

    handles = [Patch(facecolor="#eef3f8", edgecolor="#8a94a6", label="本图层多边形"),
               Patch(facecolor="none", edgecolor="#d62728", linestyle="--",
                     label="参考层(权威)边界"),
               Patch(facecolor="#ff3b30", alpha=0.75, edgecolor="none",
                     label="两图层不重合区域（缝隙/错位带）")]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=10)
    fig.suptitle("行政区划图层 · 细小间隙 / 错位修复前后对比", fontsize=14, y=0.97)
    fig.savefig(out_png, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log(f"  对比图已输出: {out_png}")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main(argv=None):
    t0 = time.time()
    ap = argparse.ArgumentParser(
        description="行政区划矢量图层细小间隙/碎屑/重叠修复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="待修复图层 (.shp/.geojson)")
    ap.add_argument("--output", default=None, help="输出前缀，默认 <input>_fixed")
    ap.add_argument("--key", "--layer-key", dest="key", default=None,
                    help="标识字段（如 乡镇码）；用于同码优先的缝隙归属与面积统计")
    ap.add_argument("--mode", choices=["topo", "snap-ref"], default="topo",
                    help="topo=自洽修复；snap-ref=配准式修复(需 --ref)")
    ap.add_argument("--ref", default=None, help="权威参考图层（snap-ref 模式必填）")
    ap.add_argument("--ref-key", default=None, help="参考层的匹配字段（默认同 --layer-key）")
    ap.add_argument("--ref-key-trim", type=int, default=None,
                    help="匹配时把参考层键值截断为前 N 位，如 9 表示 4602000012 → 460200001")
    ap.add_argument("--ref-filter", default=None, metavar="COL:PREFIX",
                    help="只保留参考层中 str(COL) 以 PREFIX 开头的行，"
                         "用于从全岛图层中裁出目标县市，如 --ref-filter XZQDM:4602")
    ap.add_argument("--ref-encoding", default="gbk", help="参考层属性编码，默认 gbk")
    ap.add_argument("--conflate-tol", type=float, default=90.0,
                    help="配准容差 2r(m)：厚度小于它的差异视为配准噪声被抹平，默认 90")
    ap.add_argument("--conflate-mode", choices=["preserve", "force"], default="preserve",
                    help="preserve=保留厚度>2r 的真实改动(默认)；force=几何完全采用参考边界")
    ap.add_argument("--snap-tol", type=float, default=None,
                    help="邻居顶点吸附容差(m)，默认 30（约等于 1 像素）")
    ap.add_argument("--max-gap-width", type=float, default=None,
                    help="判定为'细小间隙'的最大宽度(m)，默认 = 2×snap_tol")
    ap.add_argument("--min-part-area", type=float, default=0.0,
                    help="小于该面积的独立部件视为碎屑并入邻居(m²)，默认 0=不处理")
    ap.add_argument("--min-fill-area", type=float, default=10.0,
                    help="小于该面积的缺口视为噪点不填充(m²)，默认 10")
    ap.add_argument("--min-hole-area", type=float, default=1.0,
                    help="小于该面积的内部孔洞判为布尔运算伪影并删除(m²)，默认 1")
    ap.add_argument("--max-fill-gaps", type=int, default=3000,
                    help="单次最多填充的缺口个数，防止病态数据导致组合爆炸，默认 3000")
    ap.add_argument("--dissolve-by", default=None, help="修复后按该字段融合（如 乡镇码）")
    ap.add_argument("--grid", type=float, default=0.001,
                    help="精度归一网格(m)，默认 0.001；设 0 关闭")
    ap.add_argument("--snap-passes", type=int, default=2, help="邻居吸附迭代轮数")
    ap.add_argument("--no-snap", action="store_true", help="跳过邻居顶点吸附")
    ap.add_argument("--force-snap", action="store_true",
                    help="snap-ref 模式下也强制执行邻居顶点吸附（默认跳过：配准后边界"
                         "已与权威骨架重合，再吸附易产生自相交）")
    ap.add_argument("--domain-from", choices=["union", "ref"], default="ref",
                    help="约束域的来源：union=本图层合并；ref=参考层合并")
    ap.add_argument("--diagnose", action="store_true", help="只诊断，不写出结果")
    ap.add_argument("--no-plot", action="store_true", help="不出对比图")
    ap.add_argument("--report-dir", default=None, help="报告输出目录，默认与输出同目录")
    args = ap.parse_args(argv)

    in_path = os.path.abspath(args.input)
    stem = os.path.splitext(in_path)[0]
    out_base = os.path.abspath(args.output) if args.output else stem + "_fixed"
    rep_dir = os.path.abspath(args.report_dir) if args.report_dir else os.path.dirname(out_base)
    os.makedirs(rep_dir, exist_ok=True)
    os.makedirs(os.path.dirname(out_base) or ".", exist_ok=True)

    log("=" * 78)
    log("  行政区划图层细小间隙 / 碎屑 / 重叠 —— 一体化修复")
    log("=" * 78)
    log(f"  输入      : {in_path}")
    log(f"  模式      : {args.mode}")

    gdf = read_layer(in_path)
    snap_tol, unit = auto_tolerance(gdf, args.snap_tol)
    max_gap = args.max_gap_width if args.max_gap_width else snap_tol * 2.0
    log(f"  CRS       : {gdf.crs}   单位: {unit}")
    log(f"  容差      : snap_tol={snap_tol:.2f}  max_gap_width={max_gap:.2f}"
        f"  conflate_tol(2r)={args.conflate_tol:.2f}")

    if args.key and args.key not in gdf.columns:
        log(f"  ⚠ 字段 {args.key} 不存在，忽略（可用字段: {list(gdf.columns)}）")
        args.key = None

    # ---------------- 参考层 ----------------
    ref = None
    ref_key = args.ref_key or args.key
    if args.ref:
        ref = read_layer(os.path.abspath(args.ref))
        if ref.crs is not None and gdf.crs is not None and ref.crs != gdf.crs:
            ref = ref.to_crs(gdf.crs)
        if args.ref_filter:
            col, _, pref = args.ref_filter.partition(":")
            if col in ref.columns:
                n0 = len(ref)
                ref = ref[ref[col].astype(str).str.startswith(pref)].reset_index(drop=True)
                log(f"  参考层过滤        : {col} 前缀 {pref} → {n0} → {len(ref)} 个要素")
            else:
                log(f"  ⚠ 参考层无字段 {col}，--ref-filter 被忽略")
        if args.ref_key_trim and ref_key and ref_key in ref.columns:
            ref[ref_key] = ref[ref_key].astype(str).str[:args.ref_key_trim]
            log(f"  参考层键截断      : {ref_key} → 前 {args.ref_key_trim} 位")
        if ref_key and ref_key not in ref.columns:
            log(f"  ⚠ 参考层无字段 {ref_key}，无法按码匹配，改用空间求交匹配")
            ref_key = None

    # 参考层按业务键融合（用于面积对比 / 校验 / 出图）
    ref_t = None
    if ref is not None and ref_key and args.key and ref_key in ref.columns:
        tmp = ref.copy()
        tmp[args.key] = tmp[ref_key].astype(str)
        ref_t = tmp.dissolve(by=args.key).reset_index()
        keep_cols = [c for c in (args.key, "geometry") if c in ref_t.columns]
        ref_t = gpd.GeoDataFrame(ref_t[keep_cols], geometry="geometry", crs=ref.crs)
        log(f"  参考层业务聚合    : {len(ref)} → {len(ref_t)} 个 {args.key}")
    if args.mode == "snap-ref" and ref is None:
        raise SystemExit("✗ snap-ref 模式必须提供 --ref")

    # ---------------- 域 ----------------
    domain = None
    if args.domain_from == "ref" and ref is not None:
        domain = union_all([g for g in ref.geometry if g is not None and not g.is_empty])
        log(f"  约束域    : 参考层合并，{domain.area / 1e6:.4f} km²")
    else:
        domain = union_all([g for g in gdf.geometry if g is not None and not g.is_empty])
        log(f"  约束域    : 本图层合并，{domain.area / 1e6:.4f} km²")

    # ---------------- ① 诊断 ----------------
    diag_before = diagnose(gdf, args.key, snap_tol, max_gap, domain)
    if args.diagnose:
        log("")
        log("（--diagnose 模式：仅诊断，未修改数据）")
        log(f"耗时 {time.time() - t0:.1f}s")
        return 0

    labels = list(gdf[args.key].astype(str)) if args.key else None

    # ---------------- ② 几何净化 ----------------
    with Section("② 几何净化（有效性修复 + 精度归一 + 去空/零面积）"):
        raw = list(gdf.geometry)
        cleaned, bad = [], 0
        for g in raw:
            g2 = repair_validity(g)
            if g2 is None or g2.is_empty or g2.area <= 0:
                bad += 1
                cleaned.append(EMPTY)
                continue
            cleaned.append(precision_normalize(g2, args.grid))
        log(f"  修复无效/退化几何 : {bad} 个")
        total_before = sum(g.area for g in raw)
        total_after = sum(g.area for g in cleaned)
        log(f"  面积守恒校验      : {total_before / 1e6:.6f} → {total_after / 1e6:.6f} km²"
            f"（差 {(total_after - total_before):.3f} m²）")
        gdf_c = gdf.copy()
        gdf_c["geometry"] = cleaned
        gdf_c = gdf_c[~gdf_c.geometry.is_empty].reset_index(drop=True)
        labels = list(gdf_c[args.key].astype(str)) if args.key else None

    # ---------------- ③ 按字段融合（消除图层内部"假缝隙"） ----------------
    if args.dissolve_by:
        with Section(f"③ 按 {args.dissolve_by} 融合同码多部件（消除过度分割）"):
            before_n = len(gdf_c)
            agg = {c: "first" for c in gdf_c.columns if c not in ("geometry", args.dissolve_by)}
            gdf_c = gdf_c.dissolve(by=args.dissolve_by, aggfunc=agg).reset_index()
            log(f"  要素数 {before_n} → {len(gdf_c)}")
            if args.key and args.key in gdf_c.columns:
                labels = list(gdf_c[args.key].astype(str))
            elif args.key:
                labels = list(gdf_c[args.dissolve_by].astype(str))

    # 原始归属快照（用于争议区裁定；此时行序 = 后续所有步骤的行序）
    orig_snapshot = list(gdf_c.geometry) if args.dissolve_by else None

    # ---------------- ④ 配准式修复 ----------------
    conflate_rows = []
    ref_geoms = None
    if args.mode == "snap-ref":
        with Section(f"④ 形态学配准（抹平 <{args.conflate_tol:.0f}m 的配准噪声，保留真实改动）"):
            r = args.conflate_tol / 2.0
            geoms = list(gdf_c.geometry)
            ref_geoms = []
            for i, geom in enumerate(geoms):
                match = None
                if ref_key and labels is not None:
                    sel = ref[ref[ref_key].astype(str) == str(labels[i])]
                    if len(sel):
                        match = union_all([g for g in sel.geometry if g is not None and not g.is_empty])
                if match is None or match.is_empty:      # 无码可用 → 空间求交
                    cand = [(geom.intersection(g).area, g) for g in ref.geometry
                            if g is not None and not g.is_empty and g.intersects(geom)]
                    match = max(cand, key=lambda x: x[0])[1] if cand else None
                ref_geoms.append(match)
            matched = sum(1 for g in ref_geoms if g is not None and not g.is_empty)
            log(f"  参考匹配          : {matched}/{len(geoms)} 个要素成功匹配权威边界")
            geoms = conflate_to_reference(geoms, ref_geoms, r, conflate_rows,
                                          force=(args.conflate_mode == "force"))
            log(f"  配准模式          : {args.conflate_mode}"
                f"{'（几何完全采用权威边界）' if args.conflate_mode == 'force' else '（保留 >2r 的真实改动）'}")
            if conflate_rows:
                log(f"  平均抹平噪声面积  : "
                    f"{np.mean([x['noise_removed'] for x in conflate_rows]):,.0f} m²/要素")
            gdf_c = gdf_c.copy()
            gdf_c["geometry"] = geoms
            gdf_c = gdf_c[~gdf_c.geometry.is_empty].reset_index(drop=True)

    # ---------------- ⑤ 邻居顶点吸附 ----------------
    do_snap = (args.force_snap or args.mode == "topo") and not args.no_snap
    if do_snap:
        with Section("⑤ 邻居顶点吸附（令共享边界严格重合）"):
            geoms = snap_neighbors(list(gdf_c.geometry), snap_tol,
                                   passes=args.snap_passes, labels=labels,
                                   grid=args.grid)
            gdf_c = gdf_c.copy()
            gdf_c["geometry"] = geoms
    else:
        log("")
        log("▶ ⑤ 邻居顶点吸附：已跳过"
            "（snap-ref 模式下配准已替代吸附；如需强制，加 --force-snap）")

    # ---------------- ⑥ 分区归一：消除缝隙与重叠 ----------------
    gc.collect()
    with Section("⑥ 分区归一（缝隙填充 + 重叠剔除 + 争议区裁定）"):
        geoms = list(gdf_c.geometry)
        orig_for_resolve = orig_snapshot if orig_snapshot is not None else list(gdf.geometry)
        if len(orig_for_resolve) != len(geoms):
            orig_for_resolve = list(geoms)
        geoms, ov_area, frag_log, rest_area, rest_cnt = resolve_partition(
            geoms, domain, labels,
            orig_geoms=orig_for_resolve, ref_geoms=ref_geoms,
            tol=snap_tol, max_gap_width=max_gap,
            min_area=args.min_fill_area, max_frags=args.max_fill_gaps,
            grid=max(args.grid, 0.001), min_hole_area=args.min_hole_area)
        thin = [x for x in frag_log if x["source"].startswith("thin-gap")]
        blob = [x for x in frag_log if x["source"].startswith("contested-blob")]
        log(f"  争议区处理        : 共 {len(frag_log)} 个")
        log(f"      · 细小间隙(<{max_gap:.0f}m 宽) : {len(thin):6d} 个，"
            f"合计 {sum(x['gap_area'] for x in thin):14,.1f} m²")
        log(f"      · 大块争议(≥{max_gap:.0f}m 宽) : {len(blob):6d} 个，"
            f"合计 {sum(x['gap_area'] for x in blob):14,.1f} m²")
        log(f"  裁定依据分布      : "
            f"{dict(Counter(x['source'].split('/')[-1] for x in frag_log))}")
        for row in sorted(frag_log, key=lambda x: -x["gap_area"])[:10]:
            log(f"      · {row['gap_area']:12.1f} m²  宽 {row['width']:7.1f} m  "
                f"→ 归【{row['owner_label']}】（当前争议方 {row['dominant_label']}，"
                f"依据 {row['source']}）")
        if rest_cnt:
            log(f"  ⚠ 未能归属的残余: {rest_cnt} 个，合计 {rest_area:,.1f} m²（见报告）")
        gdf_c = gdf_c.copy()
        gdf_c["geometry"] = geoms
        gdf_c = gdf_c[~gdf_c.geometry.is_empty].reset_index(drop=True)

    # ---------------- ⑦ 碎屑合并 ----------------
    if args.min_part_area > 0:
        with Section(f"⑦ 碎屑部件并入邻居（面积 < {args.min_part_area:,.0f} m²）"):
            geoms = list(gdf_c.geometry)
            merged = 0
            for i, g in enumerate(geoms):
                ps = parts_of(g)
                if len(ps) < 2:
                    continue
                big = max(ps, key=lambda p: p.area)
                small = [p for p in ps if p.area < args.min_part_area]
                if not small:
                    continue
                keep = [p for p in ps if p not in small]
                tgt, best = None, -1
                for p in small:
                    for j, gg in enumerate(geoms):
                        if j == i:
                            continue
                        bl = shared_border_len(p, gg, snap_tol)
                        if bl > best:
                            best, tgt = bl, j
                    if tgt is not None and best > 0:
                        geoms[tgt] = unary_union([geoms[tgt], p])
                        merged += 1
                    else:
                        keep.append(p)
                geoms[i] = unary_union(keep) if keep else geoms[i]
            log(f"  并入邻居的碎屑部件: {merged}")
            gdf_c = gdf_c.copy()
            gdf_c["geometry"] = geoms
            gdf_c = gdf_c[~gdf_c.geometry.is_empty].reset_index(drop=True)

    # ---------------- ⑧ 写出修复结果（先落盘，保证成果不丢） ----------------
    st_ref = None
    if ref is not None:
        st_ref = ref_t if ref_t is not None else ref
    with Section("⑧ 写出修复结果"):
        gdf_c["面积km2"] = (gdf_c.geometry.area / 1e6).round(4)
        drop_existing(out_base)
        gdf_c.to_file(out_base + ".shp", encoding="utf-8")
        gdf_c.to_file(out_base + ".geojson", driver="GeoJSON", encoding="utf-8")
        log(f"  SHP     : {out_base}.shp")
        log(f"  GeoJSON : {out_base}.geojson")

        tbl = area_table(gdf, gdf_c, args.key, st_ref)
        csv_path = os.path.join(rep_dir, os.path.basename(out_base) + "_面积变化表.csv")
        tbl.to_csv(csv_path, encoding="utf-8-sig")
        log(f"  面积表  : {csv_path}")
        log("")
        try:
            log(tbl.to_string())
        except Exception:
            pass

    # ---------------- ⑨ 校验 + 报告 ----------------
    with Section("⑨ 修复结果校验"):
        v = {}
        try:
            v = verify(gdf_c, st_ref, args.key if ref_t is not None else ref_key,
                       args.key, domain)
            print_verify(v)
        except Exception as e:
            log(f"  ⚠ 校验过程异常（数据结果不受影响）: {e}")
            v = {"verify_error": str(e)}

    with Section("⑩ 报告 / 对比图"):
        # 报告
        rep = os.path.join(rep_dir, os.path.basename(out_base) + "_QA报告.md")
        with open(rep, "w", encoding="utf-8") as f:
            f.write(f"# 几何修复 QA 报告\n\n")
            f.write(f"- 输入文件：`{in_path}`\n")
            f.write(f"- 输出文件：`{out_base}.shp` / `.geojson`\n")
            f.write(f"- 坐标系：`{gdf.crs}`（单位 {unit}）\n")
            f.write(f"- 修复模式：`{args.mode}`"
                    f"{f'，参考层 `{os.path.abspath(args.ref)}`' if args.ref else ''}\n")
            f.write(f"- 配准模式：`{args.conflate_mode}`\n")
            f.write(f"- 容差：snap_tol={snap_tol:.2f} m，max_gap_width={max_gap:.2f} m，"
                    f"conflate_tol={args.conflate_tol:.2f} m\n")
            f.write(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("## 1. 修复前诊断\n\n```\n")
            for k, val in diag_before.items():
                f.write(f"{k}: {val}\n")
            f.write("```\n\n")
            f.write("## 2. 修复后校验\n\n```\n")
            for k, val in v.items():
                f.write(f"{k}: {val}\n")
            f.write("```\n\n")
            f.write("## 3. 争议区（缝隙/重叠）归属明细\n\n")
            if frag_log:
                f.write("| 争议区面积(m²) | 宽度(m) | 类型/裁定依据 | 归属要素 | 原属要素 |\n")
                f.write("|---|---:|---|---|---|\n")
                for row in sorted(frag_log, key=lambda x: -x["gap_area"]):
                    f.write(f"| {row['gap_area']:,.1f} | {row['width']:.1f} | {row['source']} "
                            f"| {row['owner_label']} | {row['dominant_label']} |\n")
            else:
                f.write("未检测到需要处理的争议区（图层本身已是合法分区）。\n")
            f.write(f"\n未能归属的残余：{rest_cnt} 个，合计 {rest_area:,.1f} m²\n")
            f.write("\n## 4. 面积变化表\n\n")
            f.write(df_to_md(tbl))
            f.write("\n")
        log(f"  报告    : {rep}")

        if not args.no_plot:
            try:
                png = os.path.join(rep_dir, os.path.basename(out_base) + "_修复前后对比.png")
                plot_before_after(gdf, gdf_c, st_ref, png, key=args.key)
            except Exception as e:
                log(f"  ⚠ 出图失败（不影响数据结果）: {e}")

    log("")
    log("=" * 78)
    log(f"完成，耗时 {time.time() - t0:.1f}s。输出前缀：{out_base}")
    log("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
