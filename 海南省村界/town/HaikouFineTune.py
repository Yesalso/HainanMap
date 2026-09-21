# -*- coding: utf-8 -*-
"""
海口乡镇图层：以"海南村界.shp 原始画法"为底（按 9 位行政区码归并出 41 个
乡镇/街道的平滑 dissolve 边界），再对照 Haikou_2002.png 手绘界线做逐边界微调。

做法（全部矢量处理，不整幅重画）：
  1. base = 将海口村级面按 HainanMap.xlsx 归并（同 HainanTownShp.py 画法）→
     41 个平滑自然的多边形；
  2. 把 base 边界投影到 2002 图像素网格，逐条边界判定：
       保留  —— base 界线距 2002 线网 ≤1.2px(≈36m)：原矢量线原样保留；
       微调  —— 2002 图明显重画：沿手绘线骨架提取中心路径替换该段；
  3. 外部边界：与邻县(澄迈/定安/文昌)交界的陆地边界一律保留 base（与邻居严丝合缝），
     面向海域的海岸线则按 2002 图微调；
  4. 重连线网 → polygonize 出封闭面 → 按 base 像素归属回填 41 个单元的 CODE/名称。

输出：
  town/海口2002.shp      微调后的海口乡镇（CODE 与原 9 位码一致）
  town/Hainan_town.shp   恢复后的全岛图层中，海口部分替换为微调结果
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from collections import defaultdict

import cv2
import numpy as np
import pandas as pd
import geopandas as gpd
from affine import Affine
from rasterio.features import rasterize
from shapely.geometry import LineString, Point
from shapely.ops import polygonize, unary_union
from shapely.validation import make_valid
from skimage.morphology import skeletonize

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN_DIR = os.path.join(BASE, "town")

SHP_PATH = os.path.join(BASE, "海南村界.shp")
MAP_XLSX = os.path.join(BASE, "HainanMap.xlsx")
IMG_2002 = os.path.join(TOWN_DIR, "Haikou_2002.png")

OUT_SHP = os.path.join(TOWN_DIR, "海口2002.shp")
SANYA_SHP = os.path.join(TOWN_DIR, "三亚2002.shp")
HAINAN_TOWN = os.path.join(TOWN_DIR, "Hainan_town.shp")
HAINAN_TOWN_OLD = os.path.join(TOWN_DIR, "Hainan_town_before_finetune.shp")
HTS_SCRIPT = os.path.join(BASE, "HainanTownShp.py")

SHP_ENCODING = "gbk"
OUT_ENCODING = "utf-8"
TARGET_CRS = "EPSG:32649"
HAIKOU_PREFIX = "4601"

CANVAS_W, CANVAS_H = 2968, 2432
OX0, OY0 = 0, 310

AGREE_R = 1.2
AGREE_FRAC = 0.8
CORRIDOR_R = 8
CORRIDOR_R_OUT = 14
NODE_SNAP_R = 2.5
SIMP_TOL = 22.0
AREA_SLIVER_KM2 = 0.02
SEA_CHECK_R = 60.0
SAMPLE_M = 15.0


def _load_hts():
    spec = importlib.util.spec_from_file_location("hts", HTS_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hts"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_code_map():
    hts = _load_hts()
    hm = pd.read_excel(MAP_XLSX, header=None, dtype=str)
    out = {}
    for _, r in hm.iterrows():
        code = str(r[0]).strip()
        if not code or code == "nan":
            continue
        town = str(r[4]).strip() if pd.notna(r[4]) else ""
        city = str(r[3]).strip() if pd.notna(r[3]) else ""
        en = str(r[2]).strip() if pd.notna(r[2]) else ""
        out[code] = {
            "town": hts.to_simple(town) if town and town.lower() != "nan" else "",
            "city": hts.to_simple(city) if city and city.lower() != "nan" else "",
            "en": en,
        }
    return out


def build_base_haikou():
    """按原始画法归并海口村级面 -> 41 个乡镇/街道(EPSG:32649)。"""
    hts = _load_hts()
    code_map = load_code_map()

    gdf = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING).to_crs(TARGET_CRS)
    gdf = gdf[~gdf["XZQMC"].astype(str).str.startswith("三沙市")].copy()
    gdf["CODE"] = gdf["XZQDM"].astype(str).str[:9]
    excl = gdf["XZQMC"].astype(str).str.contains(
        "|".join(hts.EXCLUDE_NAME_KEYWORDS), regex=True)
    gdf = gdf[~excl].copy()
    try:
        gdf["geometry"] = gdf.geometry.make_valid()
    except Exception:
        pass

    hk = gdf[gdf["CODE"].str.startswith(HAIKOU_PREFIX)].copy()
    hk["geometry"] = hk.geometry.buffer(0)
    n_feat = hk.groupby("CODE").size().rename("N_FEAT")
    units = hk.dissolve(by="CODE", aggfunc="first").reset_index()
    group_names = hk.groupby("CODE")["XZQMC"].apply(list)

    def resolve_name(code, names):
        info = code_map.get(code, {})
        raw = info.get("town", "")
        if not raw or len(raw) <= 1:
            raw = hts.derive_name(names)
        return hts.to_simple(raw)

    units["TOWN"] = [resolve_name(c, group_names.get(c, [])) for c in units["CODE"]]
    units["CITY"] = [code_map.get(c, {}).get("city", "海口市") for c in units["CODE"]]
    units["EN"] = [code_map.get(c, {}).get("en", "") for c in units["CODE"]]
    units = units.merge(n_feat, on="CODE", how="left")
    units["AREA_KM2"] = (units.geometry.area / 1e6).round(3)
    units = units.sort_values("CODE").reset_index(drop=True)
    return units, code_map


def build_others():
    gdf = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING).to_crs(TARGET_CRS)
    gdf = gdf[~gdf["XZQMC"].astype(str).str.startswith("三沙市")].copy()
    gdf["CODE"] = gdf["XZQDM"].astype(str).str[:9]
    gdf = gdf[~gdf["CODE"].str.startswith(HAIKOU_PREFIX)].copy()
    try:
        gdf["geometry"] = gdf.geometry.make_valid()
    except Exception:
        pass
    return unary_union(gdf.geometry.buffer(0))


def geo2px(minx, maxy, sx, sy, x, y):
    xx = np.round((np.asarray(x, dtype=np.float64) - minx) / sx - 0.5).astype(int)
    yy = np.round((maxy - np.asarray(y, dtype=np.float64)) / sy - 0.5).astype(int)
    return xx, yy


def px2geo(minx, maxy, sx, sy, px, py):
    return minx + (px + 0.5) * sx, maxy - (py + 0.5) * sy


def img2edges(lab):
    H, W = lab.shape
    b = np.zeros((H, W), bool)
    if H > 1:
        b[1:, :] |= lab[1:, :] != lab[:-1, :]
    if W > 1:
        b[:, 1:] |= lab[:, 1:] != lab[:, :-1]
    b |= (lab > 0) & (
        (np.arange(H)[:, None] == 0)
        | (np.arange(H)[:, None] == H - 1)
        | (np.arange(W)[None, :] == 0)
        | (np.arange(W)[None, :] == W - 1))
    return b


def distance_to_mask(mask):
    src = (~mask).astype(np.uint8)
    return cv2.distanceTransform(src, cv2.DIST_L2, 5)


def make_corridor(pts_px, r, shape):
    corr = np.zeros(shape, np.uint8)
    pts = np.asarray(pts_px, dtype=np.int32)
    x = np.clip(pts[:, 0], 0, shape[1] - 1)
    y = np.clip(pts[:, 1], 0, shape[0] - 1)
    for cx, cy in zip(x, y):
        cv2.circle(corr, (int(cx), int(cy)), int(r), 1, -1)
    return corr > 0


def nearest_arr(arr, py, px, rad):
    """arr 中距离 (px,py) ≤ rad 的最近像素，返回 (py,px) 或 None。"""
    y0, y1 = max(0, py - rad), min(arr.shape[0], py + rad + 1)
    x0, x1 = max(0, px - rad), min(arr.shape[1], px + rad + 1)
    if y0 >= y1 or x0 >= x1:
        return None
    win = arr[y0:y1, x0:x1]
    if not win.any():
        return None
    ys, xs = np.nonzero(win)
    j = int(np.argmin((xs - (px - x0)) ** 2 + (ys - (py - y0)) ** 2))
    return y0 + int(ys[j]), x0 + int(xs[j])


def edge_distance(edge_px, shape):
    """边线像素 → 像素场：线上=0，其余=欧氏距离(px)。"""
    r = np.zeros(shape, np.uint8)
    pts = np.array(edge_px, dtype=np.int32).reshape(-1, 1, 2)
    cv2.polylines(r, [pts], False, 1, 1)
    return distance_to_mask(r > 0)


def follow_skel_path(thin, dbase, a_px, b_px, band):
    """
    在骨架像素图上做带权最短路：a_px→b_px，代价=步长+0.05*距base边线^2，
    使路径尽量贴着 base 边线走。返回像素列表或 None（不连通）。
    """
    band_mask = dbase <= band
    ys, xs = np.nonzero(thin & band_mask)
    if len(xs) < 3:
        return None
    pts = [(int(x), int(y)) for x, y in zip(xs, ys)]
    index = {(y, x): i for i, (x, y) in enumerate(pts)}
    adj = [[] for _ in pts]
    for i, (x, y) in enumerate(pts):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                j = index.get((y + dy, x + dx))
                if j is not None:
                    adj[i].append(j)
    st = min(range(len(pts)), key=lambda i: (pts[i][0] - a_px[0]) ** 2
             + (pts[i][1] - a_px[1]) ** 2)
    tg = min(range(len(pts)), key=lambda i: (pts[i][0] - b_px[0]) ** 2
             + (pts[i][1] - b_px[1]) ** 2)

    import heapq
    INF = float("inf")
    dist = [INF] * len(pts)
    prev = [-1] * len(pts)
    dist[st] = 0.0
    pq = [(0.0, st)]
    while pq:
        d, i = heapq.heappop(pq)
        if d > dist[i]:
            continue
        if i == tg:
            break
        xi, yi = pts[i]
        for j in adj[i]:
            xj, yj = pts[j]
            nd = d + 1.0 + 0.05 * dbase[yj, xj] ** 2
            if nd < dist[j] - 1e-9:
                dist[j] = nd
                prev[j] = i
                heapq.heappush(pq, (nd, j))
    if st != tg and prev[tg] == -1:
        return None
    ids = []
    i = tg
    while i != -1:
        ids.append(i)
        i = prev[i]
    ids.reverse()
    return [pts[k] for k in ids]


def centerline_from_png(edge_geo, edge_px, a_px, b_px, thin, minx, maxy, sx, sy):
    """沿手绘线网(thin)提取 a→b 中心路径(geo)。失败返回 None。"""
    band = 40
    dbase = edge_distance(edge_px, thin.shape)
    path = follow_skel_path(thin, dbase, a_px, b_px, band)
    if path is None or len(path) < 3:
        return None
    line = LineString([px2geo(minx, maxy, sx, sy, float(x), float(y)) for x, y in path])
    if line.length < 1e-6:
        return None
    try:
        line = line.simplify(SIMP_TOL, preserve_topology=True)
    except Exception:
        pass
    return line


def main():
    # ---------- 1. base（原始画法） ----------
    units, code_map = build_base_haikou()
    assert len(units) > 20, "海口乡镇数量异常"
    uu = unary_union(list(units.geometry)).buffer(0)
    others_u = build_others()
    print(f"海口 base 单元：{len(units)}，面积 {uu.area / 1e6:.2f} km²")

    # ---------- 2. 参考帧 / 2002 图 ----------
    minx, miny, maxx, maxy = uu.bounds
    g02 = cv2.imdecode(np.fromfile(IMG_2002, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert g02.shape == (CANVAS_H, CANVAS_W), g02.shape
    Wr = int(round((maxx - minx) / 30.0))
    Hr = int(round((maxy - miny) / 30.0))
    sx = (maxx - minx) / Wr
    sy = (maxy - miny) / Hr
    assert 2000 <= Wr <= 2100 and 2000 <= Hr <= 2100, (Wr, Hr)

    base_lab = rasterize(
        [(g, i + 1) for i, g in enumerate(units.geometry)],
        out_shape=(Hr, Wr), transform=Affine(sx, 0, minx, 0, -sy, maxy),
        fill=0, dtype="int32")
    B0 = img2edges(base_lab)
    dark_full = (g02 < 128)

    best_off, best_ov = (OX0, OY0), -1
    for dy_off in range(OY0 - 6, OY0 + 7):
        for dx_off in range(OX0 - 4, OX0 + 5):
            if dx_off < 0 or dy_off < 0 or dy_off + Hr > CANVAS_H or dx_off + Wr > CANVAS_W:
                continue
            crop = dark_full[dy_off:dy_off + Hr, dx_off:dx_off + Wr]
            ov = int((crop & B0).sum())
            if ov > best_ov:
                best_ov, best_off = ov, (dx_off, dy_off)
    OX, OY = best_off
    print(f"图纸偏移：({OX},{OY})，与 base 边界重合像素 {best_ov}")

    dark = dark_full[OY:OY + Hr, OX:OX + Wr].copy()
    dP = distance_to_mask(dark)
    thin = skeletonize(dark)

    # ---------- 3. 边界线网：去重 + 链路化成“段(arm)” ----------
    raw_net = unary_union(list(units.geometry.boundary))
    raw_edges = list(raw_net.geoms) if raw_net.geom_type == "MultiLineString" else [raw_net]

    def canon(pts):
        pts = list(pts)
        if len(pts) < 2:
            return None
        a, b = pts[0], pts[-1]
        if (a[0], a[1]) > (b[0], b[1]):
            pts.reverse()
        return tuple((round(x, 2), round(y, 2)) for x, y in pts)

    uniq = {}
    for e in raw_edges:
        k = canon(e.coords)
        if k is None:
            continue
        if k not in uniq:
            uniq[k] = e
    edges = list(uniq.values())
    print(f"原始边 {len(raw_edges)} → 去重后 {len(edges)}")

    endmap = defaultdict(list)
    for i, e in enumerate(edges):
        endmap[e.coords[0]].append(i)
        endmap[e.coords[-1]].append(i)

    def deg(c):
        return len({e for e in endmap[c]})

    seen = set()
    arms = []
    for i0 in range(len(edges)):
        if i0 in seen:
            continue
        a0, b0 = edges[i0].coords[0], edges[i0].coords[-1]
        da, db = deg(a0), deg(b0)
        if da >= 3 or db == 1:
            start, first = a0, i0
        elif db >= 3 or da == 1:
            start, first = b0, i0
        else:
            start, first = a0, i0
        cur_pt, cur = start, first
        pts = []
        closed = False
        while True:
            if cur in seen:
                closed = True
                break
            seen.add(cur)
            seg = list(edges[cur].coords)
            if seg[0] != cur_pt:
                seg.reverse()
            if pts and seg[0] == pts[-1]:
                pts.extend(seg[1:])
            else:
                pts.extend(seg)
            cur_pt = seg[-1]
            if deg(cur_pt) >= 3 or deg(cur_pt) == 1:
                break
            nxt = [j for j in endmap[cur_pt] if j not in seen]
            if not nxt:
                break
            cur = nxt[0]
        if closed and pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        if len(pts) >= 2:
            arms.append(LineString(pts))
    print(f"链路化“段(arm)”数量：{len(arms)}")

    # 节点位置（吸附到手绘线）
    nodes = {}
    # ---------- 4. 逐段判定 ----------
    def pin(ln, pa, pb):
        co = list(ln.coords)
        if len(co) >= 2:
            co[0] = pa
            co[-1] = pb
            return LineString(co)
        return ln

    lines_out = []
    n_int = n_sea = n_moved = 0
    for arm in arms:
        n_seg = max(2, int(np.ceil(arm.length / SAMPLE_M)))
        s_ls = [arm.interpolate(arm.length * i / n_seg) for i in range(n_seg + 1)]
        gx = [p.x for p in s_ls]
        gy = [p.y for p in s_ls]
        px, py = geo2px(minx, maxy, sx, sy, gx, gy)
        px = np.clip(px, 0, Wr - 1)
        py = np.clip(py, 0, Hr - 1)
        dvals = np.array([float(dP[yy, xx]) for yy, xx in zip(py, px)])
        support = float((dvals <= AGREE_R).mean())

        sides = set()
        for xx, yy in zip(px, py):
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx0, ny0 = int(xx + dx), int(yy + dy)
                if 0 <= ny0 < Hr and 0 <= nx0 < Wr:
                    v = int(base_lab[ny0, nx0])
                    if v:
                        sides.add(v)

        a = arm.coords[0]
        b = arm.coords[-1]
        a_px = (int(round((a[0] - minx) / sx - 0.5)), int(round((maxy - a[1]) / sy - 0.5)))
        b_px = (int(round((b[0] - minx) / sx - 0.5)), int(round((maxy - b[1]) / sy - 0.5)))
        edge_px = list(zip(px.tolist(), py.tolist()))

        if len(sides) >= 2 and support >= AGREE_FRAC:
            lines_out.append(arm)
            n_int += 1
        elif len(sides) >= 2:
            repl = centerline_from_png(arm, edge_px, a_px, b_px, thin,
                                       minx, maxy, sx, sy)
            if repl is not None and repl.length > arm.length * 0.3:
                lines_out.append(pin(repl, a, b))
                n_moved += 1
                print(f"  微调内部段 {sorted(sides)} support={support:.2f} "
                      f"len={arm.length:.0f}m")
            else:
                lines_out.append(arm)
                n_int += 1
        elif len(sides) == 1:
            n_sea += 1
            ptsl = list(arm.coords)
            dx, dy = ptsl[-1][0] - ptsl[0][0], ptsl[-1][1] - ptsl[0][1]
            L = (dx * dx + dy * dy) ** 0.5 or 1.0
            nx, ny = -dy / L, dx / L
            mid = arm.interpolate(arm.length / 2)
            g1 = (mid.x + SEA_CHECK_R * nx, mid.y + SEA_CHECK_R * ny)
            g2 = (mid.x - SEA_CHECK_R * nx, mid.y - SEA_CHECK_R * ny)
            p1px, p1py = geo2px(minx, maxy, sx, sy, g1[0], g1[1])
            p2px, p2py = geo2px(minx, maxy, sx, sy, g2[0], g2[1])
            p1px_i, p1py_i = int(np.asarray(p1px).reshape(-1)[0]), int(np.asarray(p1py).reshape(-1)[0])
            p2px_i, p2py_i = int(np.asarray(p2px).reshape(-1)[0]), int(np.asarray(p2py).reshape(-1)[0])
            s1 = int(base_lab[p1py_i, p1px_i]) if (0 <= p1py_i < Hr and 0 <= p1px_i < Wr) else 0
            s2 = int(base_lab[p2py_i, p2px_i]) if (0 <= p2py_i < Hr and 0 <= p2px_i < Wr) else 0
            out_pt = Point(g1 if s1 == 0 else (g2 if s2 == 0 else mid.coords[0]))
            if others_u.intersects(out_pt.buffer(30)) or others_u.intersects(mid):
                lines_out.append(arm)   # 邻县边界 → 严丝合缝
            elif support >= AGREE_FRAC:
                lines_out.append(arm)
            else:
                repl = centerline_from_png(arm, edge_px, a_px, b_px, thin,
                                           minx, maxy, sx, sy)
                if repl is not None and repl.length > arm.length * 0.3:
                    lines_out.append(pin(repl, a, b))
                    n_moved += 1
                    print(f"  微调海域段 support={support:.2f} len={arm.length:.0f}m")
                else:
                    lines_out.append(arm)
        else:
            lines_out.append(arm)

    print(f"边界统计：内部/保留 {n_int}，外部 {n_sea}，微调 {n_moved}")

    # ---------- 5. polygonize → 归属回填 ----------
    merged = unary_union(lines_out)
    faces = [f for f in polygonize(merged) if not f.is_empty]
    kept = []
    for f in faces:
        inter = f.intersection(uu)
        if not inter.is_empty and inter.area > 0:
            kept.append(f)
    print(f"polygonize 面数 {len(faces)}，与海口相交保留 {len(kept)}")

    def face_to_label(f):
        fr = rasterize([(f, 1)], out_shape=(Hr, Wr),
                       transform=Affine(sx, 0, minx, 0, -sy, maxy), fill=0,
                       dtype="uint8")
        m = fr > 0
        if not m.any():
            return 0
        counts = np.bincount(base_lab[m].ravel(), minlength=len(units) + 1)
        counts[0] = 0
        if counts.sum() == 0:
            return 0
        return int(np.argmax(counts))

    labeled = []
    for f in kept:
        lab = face_to_label(f)
        if lab == 0:
            rep = f.representative_point()
            lab = 1 + int(min(range(len(units)),
                              key=lambda i: units.geometry.iloc[i].distance(rep)))
        labeled.append((lab, f))
    # 碎面并入相邻面
    for _ in range(3):
        changed = False
        for i, (li, fi) in enumerate(labeled):
            if 0 < fi.area / 1e6 < AREA_SLIVER_KM2:
                best_j, best_len = None, 0.0
                for j, (lj, fj) in enumerate(labeled):
                    if j == i or not fi.intersects(fj):
                        continue
                    L = fi.intersection(fj).length
                    if L > best_len:
                        best_len, best_j = L, j
                if best_j is not None and best_len > 0:
                    labeled[best_j] = (labeled[best_j][0],
                                       unary_union([labeled[best_j][1], fi]))
                    labeled.pop(i)
                    changed = True
                    break
        if not changed:
            break

    # 按 CODE 归组
    groups = defaultdict(list)
    for lab, f in labeled:
        groups[units.CODE.iloc[lab - 1]].append(f)
    nf = dict(zip(units.CODE, units.N_FEAT))
    rows = []
    for code, fs in groups.items():
        geom = make_valid(unary_union(fs))
        if geom.is_empty or geom.area <= 0:
            continue
        info = code_map.get(code, {})
        rows.append({
            "CODE": code,
            "TOWN": info.get("town", ""),
            "CITY": info.get("city", "海口市"),
            "EN": info.get("en", ""),
            "N_FEAT": int(nf.get(code, 0)),
            "AREA_KM2": round(geom.area / 1e6, 3),
            "geometry": geom,
        })

    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=TARGET_CRS)
    out = out[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
    out = out.sort_values("CODE").reset_index(drop=True)
    print(f"输出单元数：{len(out)}")

    # 诊断：base 41 码 vs 输出码
    base_codes = set(units.CODE)
    out_codes = set(out["CODE"])
    miss = base_codes - out_codes
    extra = out_codes - base_codes
    if miss:
        print("缺失单元：", sorted(miss))
        for _, r in units[units.CODE.isin(miss)].iterrows():
            print(f"   {r['CODE']} {r['TOWN']} base面积={r['AREA_KM2']} km²")
    if extra:
        print("额外单元：", sorted(extra))

    # ---------- 6. 质量检查 ----------
    out["geometry"] = out.geometry.apply(lambda g: make_valid(g).buffer(0))
    bad = int((~out.geometry.is_valid).sum())
    print(f"有效几何：{len(out) - bad}/{len(out)}")
    fin_u = unary_union(list(out.geometry)).buffer(0)
    ov = 0.0
    for i, g in enumerate(out.geometry):
        for h in out.geometry.iloc[i + 1:]:
            if g.intersects(h):
                ov += g.intersection(h).area
    print(f"内部重叠：{ov / 1e6:.4f} km²")
    if not others_u.is_empty:
        ov2 = fin_u.intersection(others_u).area
        print(f"与邻县重叠：{ov2 / 1e6:.4f} km²")
    print(f"面积 base={uu.area/1e6:.2f} km²  final={fin_u.area/1e6:.2f} km²"
          f"  差值={(fin_u.area - uu.area)/1e6:+.2f} km²")

    src_prj = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING, rows=1).crs
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = OUT_SHP[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    final = out.to_crs(src_prj)
    final.to_file(OUT_SHP, encoding=OUT_ENCODING)
    print(f"已输出：{OUT_SHP}")

    if os.environ.get("HFT_SKIP_RESTORE") == "1":
        print("跳过全岛重建（调试模式）")
        return

    # ---------- 7. 重建全岛图层 ----------
    if os.path.exists(HAINAN_TOWN) and not os.path.exists(HAINAN_TOWN_OLD):
        shutil.copyfile(HAINAN_TOWN, HAINAN_TOWN_OLD)
        print(f"备份当前全岛图层：{HAINAN_TOWN_OLD}")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.check_call([sys.executable, "-X", "utf8", HTS_SCRIPT],
                          cwd=BASE, stdout=subprocess.DEVNULL, env=env)

    hainan = gpd.read_file(HAINAN_TOWN, encoding=OUT_ENCODING)
    is_hk = (hainan["CODE"].astype(str).str.startswith(HAIKOU_PREFIX)
             | (hainan["CITY"].astype(str) == "海口市"))
    is_sy = (hainan["CODE"].astype(str).str.startswith("4602")
             | (hainan["CITY"].astype(str) == "三亚市"))
    sanya = gpd.read_file(SANYA_SHP, encoding=OUT_ENCODING)
    keep = hainan[~(is_hk | is_sy)].copy()
    merged = gpd.GeoDataFrame(
        pd.concat([keep, sanya.to_crs(hainan.crs),
                   final.to_crs(hainan.crs)], ignore_index=True),
        crs=hainan.crs)
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = HAINAN_TOWN[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    merged.to_file(HAINAN_TOWN, encoding=OUT_ENCODING)
    print(f"全岛图层：{HAINAN_TOWN}  {len(keep)} + {len(sanya)} + {len(final)}"
          f" = {len(merged)}")


if __name__ == "__main__":
    main()