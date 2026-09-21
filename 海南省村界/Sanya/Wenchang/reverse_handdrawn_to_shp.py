# -*- coding: utf-8 -*-
r"""
手绘地图反向映射 → 修改 SHP（一键流程）
======================================================================
把"在空白底图上人工改线后的手绘地图 PNG"反向映射回乡镇级 SHP，并处理
栅格矢量化必然带来的细缝/碎屑/错位/越界问题，最终把目标县市的新界线
合并进 Hainan_town.shp（非目标县市要素逐字节不变）。

依据《手绘地图映射要点》与三亚经验（reverse_sanya_2002.py + fix_slivers.py）：
  正向：SHP --(Hainan.py, EPSG:32649, 1px=20m)--> 空白底图PNG --(人工改线)--> 手绘图
  反向：手绘图 --(像素配准)--> 线网 --(洪泛+轮廓+顶点吸附)--> 多边形
        --(形态学配准去噪声/保留真实改动)--> 无缝合法分区 --> 裁回原外轮廓 --> 合并

关键纪律：
  * 外轮廓（海岸线/县界）永远以原 SHP 为权威，手绘图只提供内部乡镇界线；
  * 差异厚度 < 2r 判为配准噪声抹平，> 2r 判为真实人工改动保留；
  * 名单外要素逐字节不变；争议区归属以原始图层为先；
  * 首次运行自动备份原 SHP，之后始终以备份为基准（可重复执行、幂等）。

准备文件后直接执行：  python reverse_handdrawn_to_shp.py
"""
from __future__ import annotations

import os
import shutil
import time
from collections import Counter

import numpy as np
import cv2
import pandas as pd
import geopandas as gpd
import shapely
from shapely import (get_parts, make_valid, set_precision, snap, unary_union,
                     STRtree)
from shapely.geometry import Polygon, MultiLineString
from shapely.validation import make_valid as _make_valid

try:
    from shapely import polygonize as _polygonize
except ImportError:                                        # pragma: no cover
    from shapely.ops import polygonize as _polygonize

# ====================================================================== #
# 配置区（换县市只需改这里）
# ====================================================================== #
CONFIG = dict(
    # 待修改的 SHP（会被就地重写）
    SHP_PATH   = r"D:\Windows\Documents\海南省村界\海南省村界\town\Hainan2002\Hainan_town.shp",
    # 权威原始 SHP（反向映射/外轮廓的基准）。留 None 则首次运行时自动备份 SHP_PATH。
    # 指定后脚本始终以它为基准，保证可重复执行且不叠加改动。
    BASE_SHP   = r"D:\Windows\Documents\海南省村界\海南省村界\town\Hainan2002\Hainan_town_before_wenchang.shp",
    # 手绘地图（人工改线后的 PNG；图幅须由同一 SHP + Hainan.py 生成）
    IMG_2002   = r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\Wenchang2.png",
    # 可选：另一年份同框图（仅用于借出"文字掩膜"，没有就置 None）
    IMG_2007   = None,
    # 权威参考层（配准用；通常为最新行政区划 海南村界.shp）
    REF_PATH   = r"D:\Windows\Documents\海南省村界\海南省村界\海南村界.shp",
    REF_ENCODING = "gbk",

    # 目标县市：SHP 中行政区代码前缀 / 市名 / 英文名
    COUNTY_PREFIX = "469005",
    CITY_NAME     = "文昌市",
    EN_NAME       = "Wenchang",

    TARGET_CRS  = "EPSG:32649",   # 正向制图投影（UTM 49N）

    # 反向映射参数
    MIN_REGION_PX = 400,          # 最小白色区域(px²)
    MIN_KEEP_M2   = 0.5e6,        # 过滤 <0.5 km² 碎屑
    SNAP_GAP_M    = 40.0,         # 缝隙并入邻居的搜索半径(m)

    # 修复参数
    SNAP_TOL      = 30.0,         # 约 1 像素(m)
    MAX_GAP_WIDTH = 60.0,         # 细小间隙宽度上限(m)
    CONFLATE_TOL  = 90.0,         # 配准容差 2r(m)
    GRID          = 0.001,        # 精度归一网格(m)
    MIN_HOLE_AREA = 1.0,          # 微孔洞阈值(m²)
    MIN_FILL_AREA = 10.0,         # 最小可归属缺口(m²)

    # 输出
    OUT_DIR          = r"D:\Windows\Documents\海南省村界\海南省村界\Wenchang",
    NO_PLOT          = False,
    WRITE_INTERMEDIATE = False,   # True 时额外写出 reverse/fixed 中间 SHP，False 只就地改 Hainan_town.shp
)

BACKUP_SUFFIX = "_before_reverse"     # 原 SHP 备份后缀（首次自动生成）


# ====================================================================== #
# 基础工具
# ====================================================================== #
EMPTY = Polygon()


def log(msg=""):
    print(msg, flush=True)


def parts_of(geom):
    return [p for p in get_parts(geom) if p.geom_type in ("Polygon", "MultiPolygon")]


def to_polygonal(geom):
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


def repair_validity(geom, grid=0.001):
    """修复非法几何：set_precision / make_valid(structure|linework) / buffer(0)
    候选中挑面积最接近、部件最少者。"""
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
        except TypeError:
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


def precision_normalize(geom, grid):
    if grid and grid > 0:
        try:
            g = to_polygonal(set_precision(geom, grid))
            if g is not None and not g.is_empty and g.area > 0 and g.is_valid:
                return g
        except Exception:
            pass
    return geom


def open_morph(geom, r):
    """形态学开运算：保留厚度 > 2r 的部分。"""
    if r <= 0 or geom.is_empty:
        return geom
    try:
        g = geom.buffer(-r, quad_segs=16, join_style=1).buffer(r, quad_segs=16, join_style=1)
        return g if not g.is_empty else EMPTY
    except Exception:
        return geom


def union_all(geoms):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    return unary_union(geoms) if geoms else EMPTY


def sanitize(g):
    if g is None or g.is_empty:
        return None
    g = repair_validity(g)
    if g is None:
        return None
    try:
        return g.buffer(0)
    except Exception:
        return g


def drop_micro_holes(geom, min_hole_area):
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


# ====================================================================== #
# 读取
# ====================================================================== #
def read_layer(path, encoding="utf-8"):
    last = None
    for enc in (encoding, "gbk", "utf-8", None):
        try:
            kw = {} if enc is None else {"encoding": enc}
            return gpd.read_file(path, **kw)
        except Exception as e:
            last = e
    raise RuntimeError(f"读取失败: {path} -> {last}")


def pick_code_col(gdf):
    for c in ("CODE", "XZQDM", "乡镇码", "XZQDM9", "code"):
        if c in gdf.columns:
            return c
    raise ValueError(f"未找到行政区代码字段，现有: {list(gdf.columns)}")


# ====================================================================== #
# 步骤 1：反向映射（PNG → 乡镇多边形）
# ====================================================================== #
def reverse_map(base, cfg):
    """base: 目标县市在 TARGET_CRS 下的要素（用于取四至、栅格化权威边界）。
    返回 (polygons, code_col)：每个多边形 + 其继承的 9 位乡镇码。"""
    crs = cfg["TARGET_CRS"]
    img_path = cfg["IMG_2002"]
    code_col = pick_code_col(base)
    base = base.copy()
    base["乡镇码"] = base[code_col].astype(str).str[:9]

    # 图幅：与 Hainan.py 出图同框
    minx, miny, maxx, maxy = base.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"无法读取图像: {img_path}")
    H, W = img.shape
    log(f"  图幅 {W}×{H}，地理 {geo_w:.1f}×{geo_h:.1f} m，"
        f"≈{geo_w/W:.2f} m/像素")

    def px2geo(i, j):
        return minx + (i + 0.5) * geo_w / W, maxy - (j + 0.5) * geo_h / H

    outer = union_all(list(base.geometry)).buffer(0)

    # 逐乡镇栅格化权威边界（不能先 union 再取 boundary，会抵消共享边）
    def rasterize_rings(geom):
        mask = np.zeros((H, W), np.uint8)
        ring = geom.boundary
        lines = list(ring.geoms) if ring.geom_type == "MultiLineString" else [ring]
        for ln in lines:
            coords = np.array(ln.coords)
            if len(coords) < 2:
                continue
            pts = np.stack([(coords[:, 0] - minx) / geo_w * W,
                            (maxy - coords[:, 1]) / geo_h * H], 1).astype(np.int32)
            cv2.polylines(mask, [pts], False, 255, 1, cv2.LINE_8)
        return (mask == 255).astype(np.uint8)

    town_geoms = list(base.dissolve(by="乡镇码").geometry)
    shp_lines = np.zeros((H, W), np.uint8)
    for t in town_geoms:
        shp_lines = np.maximum(shp_lines, rasterize_rings(t))

    dark = (img == 0).astype(np.uint8)

    # 文字掩膜（可选）：2007 黑像素 − SHP 边界栅格
    if cfg.get("IMG_2007"):
        img7 = cv2.imdecode(np.fromfile(cfg["IMG_2007"], dtype=np.uint8),
                            cv2.IMREAD_GRAYSCALE)
        if img7 is not None and img7.shape == (H, W):
            text_mask = ((img7 == 0).astype(np.uint8) & (shp_lines == 0)).astype(np.uint8)
            dark = (dark & (text_mask == 0)).astype(np.uint8)
            log(f"  文字掩膜: {int(text_mask.sum())} px 已剔除")

    # 线网 = 手绘黑线 ∪ SHP 权威边界（兜底海岸线）
    lines = np.maximum(dark, shp_lines).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    net = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, kernel, iterations=2)
    net_thick = cv2.dilate(net, kernel, iterations=1)

    # 白色区域 8 连通洪泛
    white = (net_thick == 0).astype(np.uint8) * 255
    n_reg, reg_label, reg_stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    log(f"  白色区域 {n_reg - 1} 个")

    polys = []
    for i in range(1, n_reg):
        if int(reg_stats[i, cv2.CC_STAT_AREA]) < cfg["MIN_REGION_PX"]:
            continue
        x0 = int(reg_stats[i, cv2.CC_STAT_LEFT]); y0 = int(reg_stats[i, cv2.CC_STAT_TOP])
        wb = int(reg_stats[i, cv2.CC_STAT_WIDTH]); hb = int(reg_stats[i, cv2.CC_STAT_HEIGHT])
        rmask = (reg_label[y0:y0 + hb, x0:x0 + wb] == i).astype(np.uint8) * 255
        contours, _ = cv2.findContours(rmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        c = max(contours, key=cv2.contourArea)
        if c.shape[0] < 3:
            continue
        ring = []
        for pt in c[:, 0, :]:
            ix, iy = int(pt[0]) + x0, int(pt[1]) + y0
            best, bd = None, 9
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    yy, xx = iy + dy, ix + dx
                    if 0 <= yy < H and 0 <= xx < W and net[yy, xx]:
                        d = dx * dx + dy * dy
                        if d < bd:
                            bd, best = d, (xx, yy)
            ring.append(px2geo(*best) if best else px2geo(ix, iy))
        if len(ring) < 3:
            continue
        if abs(ring[0][0] - ring[-1][0]) > 1e-9 or abs(ring[0][1] - ring[-1][1]) > 1e-9:
            ring.append(ring[0])
        p = Polygon(ring).buffer(0)
        if p.is_empty or p.area <= 0:
            continue
        polys.append(p)

    # 与权威陆域求交（外轮廓以 SHP 为准）
    land = []
    for p in polys:
        inter = p.intersection(outer).buffer(0)
        if inter.is_empty or inter.area <= 0:
            continue
        frac = inter.area / p.area
        if inter.area > 2e6 and frac < 0.3 and p.area > 20e6:
            continue
        if inter.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        land.append(inter)

    kept = sorted([p for p in land if p.area > cfg["MIN_KEEP_M2"]], key=lambda p: -p.area)
    # 缝隙填充：把 outer − covered 的碎块并入最大重叠邻居
    covered = union_all(kept)
    missing = outer.difference(covered)
    if not missing.is_empty:
        frags = [f for f in (list(missing.geoms) if missing.geom_type == "MultiPolygon"
                             else [missing]) if f.area > 0]
        for f in sorted(frags, key=lambda p: -p.area):
            fb = f.buffer(cfg["SNAP_GAP_M"], quad_segs=1)
            best, bestov = None, 0.0
            for k, p in enumerate(kept):
                ov = p.intersection(fb).area
                if ov > bestov:
                    bestov, best = ov, k
            if best is not None:
                kept[best] = kept[best].union(f)
    # 剩余缺口 = 真实离岛，补回
    leftover = outer.difference(union_all(kept))
    if not leftover.is_empty:
        lf = list(leftover.geoms) if leftover.geom_type == "MultiPolygon" else [leftover]
        kept.extend(p for p in lf if p.area > 0)

    # 属性继承：最大面积重叠 → 乡镇码
    towns = base.dissolve(by="乡镇码").reset_index()
    rows = []
    for p in kept:
        best_code, best_ov = None, 0.0
        for _, r in towns.iterrows():
            ov = p.intersection(r.geometry).area
            if ov > best_ov:
                best_ov, best_code = ov, r["乡镇码"]
        rows.append((str(best_code), p))
    log(f"  反向映射得到 {len(rows)} 个分区")
    return rows, code_col


# ====================================================================== #
# 步骤 2：几何修复（配准 + 分区归一）
# ====================================================================== #
def _owner_by_overlap(frag, cand_geoms, tree, thresh=0.5):
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


def remove_overlaps(geoms, tol):
    order = sorted(range(len(geoms)), key=lambda i: -geoms[i].area)
    out = [EMPTY] * len(geoms)
    occ = None
    for i in order:
        g = geoms[i]
        if occ is not None and not occ.is_empty:
            g = g.difference(occ)
        g = repair_validity(g) if (g is not None and not g.is_empty and not g.is_valid) else g
        out[i] = g if g is not None else EMPTY
        occ = out[i] if occ is None else unary_union([occ, out[i]])
    return out


def resolve_partition(geoms, domain, labels, orig_geoms, ref_geoms, tol,
                      max_gap_width, min_area=1.0, max_frags=20000,
                      grid=0.001, min_hole_area=1.0):
    """把一组多边形规整成 domain 的精确分区（无重叠、无空洞）。
    争议区归属回退链：原始图层 → 参考层 → 最长公共边界 → 放弃。"""
    n = len(geoms)
    log_rows = []
    if n == 0:
        return geoms, 0.0, log_rows, 0.0, 0
    orig = list(orig_geoms)
    refs = list(ref_geoms or []) or None
    t_orig = t_now = t_ref = None
    try:
        t_orig = STRtree([g for g in orig if g is not None and not g.is_empty])
        t_now = STRtree([g for g in geoms if g is not None and not g.is_empty])
        if refs:
            t_ref = STRtree([g for g in refs if g is not None and not g.is_empty])
    except Exception:
        pass

    frags = []
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
    if domain is not None and not domain.is_empty:
        try:
            rest = domain.difference(unary_union(geoms))
        except Exception:
            rest = None
        if rest is not None and not rest.is_empty:
            for p in parts_of(rest):
                if p.area >= min_area:
                    frags.append((p, None))
    if len(frags) > max_frags:
        frags.sort(key=lambda x: -x[0].area)
        dropped = frags[max_frags:]
        frags = frags[:max_frags]
    else:
        dropped = []

    try:
        bnd_buf_now = [g.boundary.buffer(tol / 2, quad_segs=6) if not g.is_empty else EMPTY
                       for g in geoms]
    except Exception:
        bnd_buf_now = None
    give = {}
    for frag, conflicter in frags:
        j = _owner_by_overlap(frag, orig, t_orig)
        src = "orig-owner"
        if j is None and refs:
            j = _owner_by_overlap(frag, refs, t_ref)
            src = "ref-owner"
        if j is None:
            j = _owner_by_border(frag, geoms, labels, tol, True, t_now, bnd_buf_now)
            src = "longest-border"
        if j is None:
            dropped.append(frag)
            continue
        give.setdefault(j, []).append(frag)
        w = 2.0 * frag.area / frag.length if frag.length > 0 else 0.0
        log_rows.append(dict(gap_area=float(frag.area), width=float(w),
                             source=("thin-gap" if w <= max_gap_width else "contested-blob")
                                    + "/" + src,
                             owner=int(j),
                             owner_label=str(labels[j]) if labels is not None else "",
                             dominant=int(conflicter) if conflicter is not None else -1,
                             dominant_label=(str(labels[conflicter])
                                             if (labels is not None and conflicter is not None)
                                             else "无归属(空洞)")))

    all_given = {j: union_all(ps) for j, ps in give.items()}
    total_given = union_all(list(all_given.values())) if all_given else EMPTY
    out = []
    for i in range(n):
        g = geoms[i]
        plus = all_given.get(i)
        try:
            if plus is not None and not plus.is_empty:
                g = union_all([g, plus])
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

    out = [drop_micro_holes(g, min_hole_area) if (g is not None and not g.is_empty) else EMPTY
           for g in out]
    out = [precision_normalize(g, grid) if (g is not None and not g.is_empty) else EMPTY
           for g in out]
    out = remove_overlaps(out, tol)
    out = [drop_micro_holes(g, min_hole_area) if (g is not None and not g.is_empty) else EMPTY
           for g in out]
    out = [precision_normalize(g, grid) if (g is not None and not g.is_empty) else EMPTY
           for g in out]

    if domain is not None and not domain.is_empty:
        try:
            t_now2 = STRtree([g for g in out if g is not None and not g.is_empty])
            bnd_buf2 = [g.boundary.buffer(tol / 2, quad_segs=6) if not g.is_empty else EMPTY
                        for g in out]
        except Exception:
            t_now2, bnd_buf2 = None, None
        micro = []
        for p in parts_of(domain.difference(union_all(out))):
            if p.area <= 0 or p.area < max(min_area, 1.0):
                continue
            j = _owner_by_border(p, out, labels, tol, True, t_now2, bnd_buf2)
            if j is not None:
                micro.append((p, j))
        for p, j in micro:
            try:
                out[j] = precision_normalize(unary_union([out[j], p]), grid) or out[j]
            except Exception:
                pass

    cov = union_all(out)
    left_area, left_cnt = 0.0, 0
    if domain is not None and not domain.is_empty:
        try:
            lp = [p for p in parts_of(domain.difference(cov)) if p.area > 0]
            left_area = float(sum(p.area for p in lp))
            left_cnt = len(lp)
        except Exception:
            pass
    left_area += float(sum(p.area for p in dropped))
    return out, float(total_given.area), log_rows, left_area, left_cnt + len(dropped)


def conflate_to_reference(geoms, ref_geoms, r):
    """A_new = R_A ∪ opening(A\\R_A, r) \\ opening(R_A\\A, r)"""
    out = []
    for a, rr in zip(geoms, ref_geoms):
        if a is None or a.is_empty:
            out.append(a)
            continue
        if rr is None or rr.is_empty:
            out.append(a)
            continue
        try:
            grow = a.difference(rr)
            keep_grow = open_morph(grow, r)
            miss = rr.difference(a)
            keep_miss = open_morph(miss, r)
            new = unary_union([rr, keep_grow]).difference(keep_miss)
            new = repair_validity(new)
            out.append(new if (new is not None and not new.is_empty) else a)
        except Exception:
            out.append(a)
    return out


def fix_county(rev_rows, base, cfg, ref_gdf):
    """rev_rows: [(乡镇码, polygon)]。返回修复后的 GeoDataFrame（乡镇码+geometry）。"""
    code_col = pick_code_col(base)
    base = base.copy()
    base["乡镇码"] = base[code_col].astype(str).str[:9]
    towns = base.dissolve(by="乡镇码").reset_index()

    # 反向结果按乡镇码融合
    rev = gpd.GeoDataFrame({"乡镇码": [r[0] for r in rev_rows]},
                           geometry=[r[1] for r in rev_rows],
                           crs=cfg["TARGET_CRS"])
    rev["geometry"] = rev.geometry.apply(sanitize)
    rev = rev[rev.geometry.notna() & (~rev.geometry.is_empty)]
    gdf = rev.dissolve(by="乡镇码", aggfunc="first").reset_index()

    labels = list(gdf["乡镇码"].astype(str))
    geoms = [sanitize(g) for g in gdf.geometry]

    # 参考层：按乡镇码聚合（同名匹配；匹配不到则空间求交）
    ref_geoms = []
    if ref_gdf is not None and len(ref_gdf):
        ref_geoms = []
        for i, g in enumerate(geoms):
            sel = ref_gdf[ref_gdf["乡镇码"].astype(str) == labels[i]]
            if len(sel):
                ref_geoms.append(union_all([x for x in sel.geometry
                                            if x is not None and not x.is_empty]))
            else:
                cand = [(g.intersection(r).area, r) for r in ref_gdf.geometry
                        if r is not None and not r.is_empty and r.intersects(g)]
                ref_geoms.append(max(cand)[1] if cand else None)
    else:
        ref_geoms = [None] * len(geoms)

    # 原始归属快照（配准前）
    orig_snapshot = [sanitize(g) for g in geoms]

    # 形态学配准
    r = cfg["CONFLATE_TOL"] / 2.0
    geoms = conflate_to_reference(geoms, ref_geoms, r)

    # 约束域：参考层合并（否则本层合并）
    if ref_gdf is not None and len(ref_gdf):
        domain = union_all(list(ref_gdf.geometry))
    else:
        domain = union_all([sanitize(g) for g in base.geometry])

    geoms, ov, frag_log, rest_area, rest_cnt = resolve_partition(
        geoms, domain, labels, orig_geoms=orig_snapshot, ref_geoms=ref_geoms,
        tol=cfg["SNAP_TOL"], max_gap_width=cfg["MAX_GAP_WIDTH"],
        min_area=cfg["MIN_FILL_AREA"], grid=cfg["GRID"],
        min_hole_area=cfg["MIN_HOLE_AREA"])

    thin = [x for x in frag_log if x["source"].startswith("thin-gap")]
    blob = [x for x in frag_log if x["source"].startswith("contested-blob")]
    log(f"  争议区 {len(frag_log)} 个（细缝 {len(thin)} / 大块 {len(blob)}），"
        f"裁定依据 {dict(Counter(x['source'].split('/')[-1] for x in frag_log))}")
    if rest_cnt:
        log(f"  ⚠ 未归属残余 {rest_cnt} 个，合计 {rest_area:.1f} m²")

    res = gpd.GeoDataFrame({"乡镇码": labels, "geometry": geoms},
                           crs=cfg["TARGET_CRS"])
    res = res[res.geometry.notna() & (~res.geometry.is_empty)].copy()
    res["geometry"] = res.geometry.apply(sanitize)
    res = res[res.geometry.notna() & (~res.geometry.is_empty)].reset_index(drop=True)
    return res


# ====================================================================== #
# 步骤 3：裁回原外轮廓 + 合并
# ====================================================================== #
def clip_to_base_outer(wc, base_outer, gap_m=40.0):
    wc = wc.copy()
    wc["geometry"] = wc.geometry.apply(sanitize)
    wc = gpd.clip(wc, base_outer).reset_index(drop=True)
    covered = union_all(list(wc.geometry))
    missing = base_outer.difference(covered)
    geoms = list(wc.geometry)
    n_fill = 0
    if not missing.is_empty:
        frags = list(missing.geoms) if missing.geom_type == "MultiPolygon" else [missing]
        for f in frags:
            if f.area <= 0:
                continue
            fb = f.buffer(gap_m, quad_segs=1)
            best, bo = -1, -1.0
            for i, g in enumerate(geoms):
                a = g.intersection(fb).area
                if a > bo:
                    bo, best = a, i
            if best >= 0:
                geoms[best] = geoms[best].union(f)
                n_fill += 1
    wc["geometry"] = [sanitize(g) for g in geoms]
    wc = wc[wc.geometry.notna() & (~wc.geometry.is_empty)].copy()
    log(f"  裁回外轮廓：缺口补位 {n_fill} 块，"
        f"越界 {union_all(list(wc.geometry)).difference(base_outer).area:.3f} m²")
    return wc


def merge_into_shp(base, wc, cfg):
    code_col = pick_code_col(base)
    base = base.copy()
    is_t = base[code_col].astype(str).str.startswith(cfg["COUNTY_PREFIX"])
    keep = base[~is_t].copy()
    # 乡镇名：沿用原 SHP 中同码的 TOWN（无则用代码）
    name_map = {}
    if "TOWN" in base.columns:
        for c, n in zip(base[code_col].astype(str).str[:9], base["TOWN"].astype(str)):
            name_map.setdefault(c, n)
    fixed = wc.copy()
    fixed["CODE"] = fixed["乡镇码"].astype(str)
    fixed["TOWN"] = fixed["乡镇码"].astype(str).map(lambda c: name_map.get(c, c))
    fixed["CITY"] = cfg["CITY_NAME"]
    fixed["EN"] = cfg["EN_NAME"]
    fixed["N_FEAT"] = 0
    fixed["AREA_KM2"] = (fixed.geometry.area / 1e6).round(3)
    cols = [c for c in keep.columns if c != base.geometry.name]
    fixed = fixed[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2",
                   base.geometry.name]]
    fixed.columns = cols + [base.geometry.name]
    merged = gpd.GeoDataFrame(pd.concat([keep, fixed], ignore_index=True),
                              geometry=base.geometry.name, crs=base.crs)
    return merged


# ====================================================================== #
# 校验 / 输出
# ====================================================================== #
def save_layer(gdf, base_path, encoding="utf-8"):
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".geojson"):
        p = base_path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    gdf.to_file(base_path + ".shp", encoding=encoding)
    gdf.to_file(base_path + ".geojson", driver="GeoJSON", encoding=encoding)


def render_lines(gdf, minx, maxy, geo_w, geo_h, W, H):
    m = np.zeros((H, W), np.uint8)
    for g in gdf.geometry:
        if g is None or g.is_empty:
            continue
        ring = g.boundary
        lines = list(ring.geoms) if ring.geom_type == "MultiLineString" else [ring]
        for ln in lines:
            coords = np.array(ln.coords)
            if len(coords) < 2:
                continue
            pts = np.stack([(coords[:, 0] - minx) / geo_w * W,
                            (maxy - coords[:, 1]) / geo_h * H], 1).astype(np.int32)
            cv2.polylines(m, [pts], False, 255, 1, cv2.LINE_8)
    return (m == 255)


def make_plot(img, before_gdf, after_gdf, minx, maxy, geo_w, geo_h, out_png):
    """半分辨率对比图：左=原SHP渲染，右=映射后SHP渲染（灰底=手绘线网）。"""
    H, W = img.shape
    dark = (img == 0)
    rb = render_lines(before_gdf, minx, maxy, geo_w, geo_h, W, H)
    rc = render_lines(after_gdf, minx, maxy, geo_w, geo_h, W, H)
    base = np.full((H, W, 3), 255, np.uint8)
    base[dark] = (232, 232, 232)

    def panel(old, new):
        im = base.copy()
        im[new & ~old] = (0, 90, 255)
        im[old & ~new] = (255, 200, 0)
        im[old & new] = (90, 90, 90)
        return im
    p_old = base.copy(); p_old[rb] = (0, 0, 0)
    p_diff = panel(rb, rc)
    canvas = cv2.hconcat([p_old, p_diff])
    canvas = cv2.resize(canvas, (W, H), interpolation=cv2.INTER_AREA)
    cv2.imencode(".png", canvas)[1].tofile(out_png)
    log(f"  对比图: {out_png}")


# ====================================================================== #
# 主流程
# ====================================================================== #
def main():
    t0 = time.time()
    cfg = CONFIG
    crs = cfg["TARGET_CRS"]
    shp_path = cfg["SHP_PATH"]
    out_dir = cfg["OUT_DIR"] or os.path.dirname(shp_path)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(shp_path))[0]
    backup = os.path.join(os.path.dirname(shp_path), stem + BACKUP_SUFFIX + ".shp")
    rev_base = os.path.join(out_dir, f"{cfg['EN_NAME']}_reverse")
    fix_base = os.path.join(out_dir, f"{cfg['EN_NAME']}_reverse_fixed")

    log("=" * 78)
    log("  手绘地图反向映射 → 修改 SHP（一键流程）")
    log("=" * 78)

    # 0. 备份（首次）
    base_src = cfg.get("BASE_SHP")
    if base_src and os.path.exists(base_src):
        log(f"  基准原始 SHP: {os.path.basename(base_src)}")
    else:
        base_src = backup
        if not os.path.exists(backup):
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                src = shp_path[:-4] + ext
                if os.path.exists(src):
                    shutil.copyfile(src, backup[:-4] + ext)
            log(f"  已备份原 SHP → {os.path.basename(backup)}")
        else:
            log(f"  使用既有备份 {os.path.basename(backup)}（保证幂等）")

    base = read_layer(base_src, encoding="utf-8")
    code_col = pick_code_col(base)
    base = base.to_crs(crs)
    is_t = base[code_col].astype(str).str.startswith(cfg["COUNTY_PREFIX"])
    county = base[is_t].copy()
    if county.empty:
        raise SystemExit(f"✗ 未找到前缀 {cfg['COUNTY_PREFIX']} 的要素")
    log(f"  目标县市要素 {len(county)}（总 {len(base)}）")
    base_outer = union_all([sanitize(g) for g in county.geometry])

    # 参考层
    ref_gdf = None
    if cfg.get("REF_PATH") and os.path.exists(cfg["REF_PATH"]):
        ref = read_layer(cfg["REF_PATH"], encoding=cfg.get("REF_ENCODING", "gbk"))
        ref = ref.to_crs(crs)
        rc_key = pick_code_col(ref)
        ref["乡镇码"] = ref[rc_key].astype(str).str[:9]
        ref = ref[ref[rc_key].astype(str).str.startswith(cfg["COUNTY_PREFIX"])].copy()
        ref = ref[ref.geometry.notna() & (~ref.geometry.is_empty)].copy()
        ref["geometry"] = ref.geometry.apply(sanitize)
        ref_gdf = ref[["乡镇码", "geometry"]].copy()
        log(f"  参考层 {len(ref_gdf)} 个要素（前缀 {cfg['COUNTY_PREFIX']}）")

    # 1. 反向映射
    log("\n▶ 1 反向映射（PNG → 乡镇多边形）")
    rev_rows, _ = reverse_map(county, cfg)
    rev_gdf = gpd.GeoDataFrame({"乡镇码": [r[0] for r in rev_rows]},
                               geometry=[r[1] for r in rev_rows], crs=crs)
    if cfg.get("WRITE_INTERMEDIATE"):
        save_layer(rev_gdf, rev_base)
        log(f"  已写出反向结果: {rev_base}.shp")

    # 2. 几何修复
    log("\n▶ 2 几何修复（配准 + 分区归一）")
    fixed = fix_county(rev_rows, county, cfg, ref_gdf)
    if cfg.get("WRITE_INTERMEDIATE"):
        save_layer(fixed, fix_base)
        log(f"  已写出修复结果: {fix_base}.shp（{len(fixed)} 要素）")

    # 3. 裁回原外轮廓
    log("\n▶ 3 裁回原外轮廓 + 合并回 SHP")
    fixed_clip = clip_to_base_outer(fixed, base_outer, gap_m=cfg["SNAP_GAP_M"])
    merged = merge_into_shp(base, fixed_clip, cfg)
    save_layer(merged, shp_path[:-4])
    log(f"  已重写 {shp_path}（{len(merged)} 要素）")

    # 4. 校验
    log("\n▶ 4 校验")
    chk = read_layer(shp_path, encoding="utf-8")
    log(f"  要素数 {len(chk)}  非法 {int((~chk.geometry.is_valid).sum())}")
    is_c = chk[code_col].astype(str).str.startswith(cfg["COUNTY_PREFIX"])
    log(f"  目标县市 {int(is_c.sum())} 个，面积 "
        f"{chk[is_c].geometry.area.sum()/1e6:.3f} km² / 原 "
        f"{base_outer.area/1e6:.3f} km²")
    # 名单外对称差
    sd = 0.0
    b0 = base[~base[code_col].astype(str).str.startswith(cfg["COUNTY_PREFIX"])]
    c0 = chk[~is_c]
    for a, b in zip(b0.geometry, c0.geometry):
        sd += a.symmetric_difference(b).area
    log(f"  名单外对称差 {sd:.6f} m²（应为 0）")

    if not cfg["NO_PLOT"]:
        img = cv2.imdecode(np.fromfile(cfg["IMG_2002"], dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        minx, miny, maxx, maxy = county.total_bounds
        make_plot(img, county, fixed_clip, minx, maxy, maxx - minx, maxy - miny,
                  os.path.join(out_dir, f"{cfg['EN_NAME']}_mapping_result.png"))

    log(f"\n完成，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
