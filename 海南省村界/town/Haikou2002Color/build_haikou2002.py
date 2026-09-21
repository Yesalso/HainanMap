# -*- coding: utf-8 -*-
"""
按 Haikou_2002_a.png 的色块，更新海口乡镇图层（写回 town/Hainan2002/Hainan_town.shp）。

规则（来自用户）：
 1. 色块对应 9 个乡镇：
      #00A2E8 新海乡 / #7F7F7F 薛样乡 / #880015 美安镇 / #3F48CC 东营镇 /
      #B5E61D 桂林洋镇 / #22B14C 演海镇 / #C3C3C3 美仁坡乡 / #73FBFD 新民乡 /
      #FFAEC9 谭文镇
 2. 新海乡、西秀镇、长流镇的边界按图重画（西部块整体按 2002 图重切）。
 3. 其余色块都是"在现有乡镇边界内新增"：从现乡镇中切出色块范围，父级乡镇保留其余部分。
 4. 海岸线、县市边界一律不改动 —— 新单元只取现乡镇范围内的部分，
    图里画在海上的部分（图海岸比现海岸靠外）直接丢弃。

技术要点：
  * 配准 30 m/px，偏移 (0,310)，网格 2036×2097（= 海口 bbox）。
  * 分区在栅格上做，再按"像素边界格点图"精确矢量化 + 逐弧平滑 + polygonize，
    相邻单元共用同一条线，无缝无重叠。
  * 未涉及的乡镇几何原样保留（不重采样、不改顶点）。

输出：town/Hainan2002/Hainan_town.shp（海口块 41 → 50 单元）
"""

import json
import os
import shutil

import numpy as np
import geopandas as gpd
import pandas as pd
from PIL import Image
from scipy import ndimage
from rasterio.features import rasterize
from rasterio.transform import Affine
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union
from shapely.validation import make_valid

# ==================== 配置 ====================
BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
WORK = os.path.join(TOWN, "Haikou2002Color")
os.makedirs(WORK, exist_ok=True)

TARGET_CRS = "EPSG:32649"
HAIKOU = "4601"

Wr, Hr, OX, OY = 2036, 2097, 0, 310

COLORS = [
    ("新海乡", "00A2E8"),
    ("薛样乡", "7F7F7F"),
    ("美安镇", "880015"),
    ("东营镇", "3F48CC"),
    ("桂林洋镇", "B5E61D"),
    ("演海镇", "22B14C"),
    ("美仁坡乡", "C3C3C3"),
    ("新民乡", "73FBFD"),
    ("谭文镇", "FFAEC9"),
]

# 色块 → 父级现乡镇（侦察得出；演海镇跨 三江镇/演丰镇）
PARENTS = {
    "新海乡": ["西秀镇", "长流镇"],
    "薛样乡": ["城西镇"],
    "美安镇": ["石山镇"],
    "东营镇": ["灵山镇"],
    "桂林洋镇": ["演丰镇"],
    "演海镇": ["三江镇", "演丰镇"],
    "美仁坡乡": ["龙泉镇"],
    "新民乡": ["甲子镇"],
    "谭文镇": ["三门坡镇"],
}

WEST = ["新海乡", "长流镇", "西秀镇"]        # 西部块：按图重画
WEST_PARENTS = ["西秀镇", "长流镇"]

MIN_ZONE_PX = 25
SMOOTH_WIN = 9
SIMP_TOL = 5.0

SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
# ==============================================


def sanitize(g):
    if g is None or g.is_empty:
        return g
    if not g.is_valid:
        try:
            g = make_valid(g)
        except Exception:
            g = g.buffer(0)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else g.buffer(0)


def as_multi(g):
    """shapefile 只支持单一几何类型：统一成 MultiPolygon。"""
    g = sanitize(g)
    if g is None or g.is_empty:
        return g
    if g.geom_type == "Polygon":
        return MultiPolygon([g])
    if g.geom_type == "MultiPolygon":
        return g
    return sanitize(g.buffer(0))


def write_shp(gdf, path, encoding="utf-8"):
    tmp = path[:-4] + "__tmp.shp"
    for ext in SIDECARS:
        p = tmp[:-4] + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    gdf.to_file(tmp, encoding=encoding)
    for ext in SIDECARS:
        s, d = tmp[:-4] + ext, path[:-4] + ext
        if os.path.exists(s):
            os.replace(s, d)


def backup(path):
    ok = False
    for ext in SIDECARS:
        s = path[:-4] + ext
        if os.path.exists(s):
            shutil.copy2(s, path[:-4] + "_before_haikou2002" + ext)
            ok = True
    if ok:
        print("备份 →", path[:-4] + "_before_haikou2002.shp")


# ---------------------------------------------------------------- 色块分区
def build_zone_image(a_img):
    H, W, _ = a_img.shape
    cls = np.zeros((H, W), np.int8)
    for k, (nm, hx) in enumerate(COLORS, 1):
        rgb = np.array([int(hx[i:i + 2], 16) for i in (0, 2, 4)])
        cls[np.abs(a_img - rgb).max(2) <= 20] = k
    zones = np.zeros((H, W), np.int32)
    zname = {}
    nid = 0
    st4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    for k, (nm, _) in enumerate(COLORS, 1):
        lab, n = ndimage.label(cls == k, structure=st4)
        sizes = ndimage.sum(np.ones_like(lab), lab, index=list(range(1, n + 1)))
        for i, a in enumerate(sizes, 1):
            if a < MIN_ZONE_PX:
                continue
            nid += 1
            zones[lab == i] = nid
            zname[nid] = nm
    return zones, zname


def nearest_zone_ids(src_mask, zones):
    _, (iy, ix) = ndimage.distance_transform_edt(~src_mask, return_indices=True)
    return zones[iy, ix]


# ---------------------------------------------------------------- 矢量化
def build_graph(vert, hedge):
    from collections import defaultdict
    nb = defaultdict(list)
    ys, xs = np.nonzero(vert)
    for r, c1 in zip(ys.tolist(), xs.tolist()):
        c = c1 + 1
        nb[(c, r)].append((c, r + 1))
        nb[(c, r + 1)].append((c, r))
    ys, xs = np.nonzero(hedge)
    for r1, c in zip(ys.tolist(), xs.tolist()):
        r = r1 + 1
        nb[(c, r)].append((c + 1, r))
        nb[(c + 1, r)].append((c, r))
    for k in list(nb):
        nb[k] = list(dict.fromkeys(nb[k]))
    return nb


def trace_arcs(nb):
    is_node = {k: len(v) != 2 for k, v in nb.items()}
    used = set()
    arcs = []

    def walk(start, first):
        path = [start, first]
        used.add((start, first)); used.add((first, start))
        prev, cur = start, first
        while not is_node.get(cur, True):
            if cur == start:
                break
            nxt = [k for k in nb[cur] if k != prev]
            if not nxt:
                break
            k = nxt[0]
            used.add((cur, k)); used.add((k, cur))
            path.append(k)
            prev, cur = cur, k
        return path

    for s in [k for k, v in is_node.items() if v]:
        for j in nb[s]:
            if (s, j) not in used:
                arcs.append(walk(s, j))
    for s in [k for k, v in is_node.items() if not v]:
        for j in nb[s]:
            if (s, j) not in used:
                p = walk(s, j)
                if len(p) >= 4:
                    arcs.append(p if p[0] == p[-1] else p + [s])
    return arcs


def smooth_pts(pts, win):
    a = np.array(pts, dtype=np.float64)
    n = len(a)
    if n < 5:
        return a
    k = max(1, win // 2)
    out = a.copy()
    cs = np.cumsum(np.vstack([np.zeros((1, 2)), a]), axis=0)
    for i in range(1, n - 1):
        lo = max(0, i - k)
        hi = min(n, i + k + 1)
        out[i] = (cs[hi] - cs[lo]) / (hi - lo)
    return out


# ---------------------------------------------------------------- 主流程
def main():
    gdf = gpd.read_file(HT, encoding="utf-8")
    g = gdf.to_crs(TARGET_CRS)
    g["CODE"] = g["CODE"].astype(str)
    is_hk = g["CODE"].str.startswith(HAIKOU) | (g["CITY"].astype(str) == "海口市")
    hk = g[is_hk].copy().reset_index(drop=True)
    keep_geo = gdf[~is_hk].copy().reset_index(drop=True)
    print(f"全岛 {len(gdf)}：海口 {len(hk)} + 其余 {len(keep_geo)}")

    town2i = {t: i + 1 for i, t in enumerate(hk["TOWN"])}
    for nm, ps in PARENTS.items():
        for p in ps:
            assert p in town2i, f"未找到乡镇 {p}"
    minx, miny, maxx, maxy = unary_union(list(hk.geometry)).bounds
    sx = (maxx - minx) / Wr
    sy = (maxy - miny) / Hr
    print(f"bbox {minx:.1f},{miny:.1f} → {maxx:.1f},{maxy:.1f}  格网 {sx:.3f}×{sy:.3f} m")

    base_lab = rasterize([(gg, i + 1) for i, gg in enumerate(hk.geometry)],
                         out_shape=(Hr, Wr),
                         transform=Affine(sx, 0, minx, 0, -sy, maxy), fill=0,
                         dtype="int32")

    aimg = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
    acrop = aimg[OY:OY + Hr, OX:OX + Wr]
    cls = np.zeros((Hr, Wr), np.int8)                 # 每像素的颜色类别 1..9
    for k, (nm, hx) in enumerate(COLORS, 1):
        rgb = np.array([int(hx[i:i + 2], 16) for i in (0, 2, 4)])
        cls[np.abs(acrop - rgb).max(2) <= 20] = k
    newid = {nm: 100 + i for i, (nm, _) in enumerate(COLORS)}

    # ---------- 图黑线围出的连通域（图上每个乡镇/岛屿；按色块归属整块，消除色块边缘与黑线间的细缝） ----------
    bimg = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002.png")).convert("RGB")).astype(np.int32)
    dark = (bimg.sum(2) < 3 * 128)[OY:OY + Hr, OX:OX + Wr]
    st4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    cells, ncell = ndimage.label((dark == 0).astype(np.uint8), structure=st4)
    cell_area = ndimage.sum(np.ones_like(cells), cells, index=list(range(ncell + 1)))
    border = (set(cells[0, :]) | set(cells[-1, :])
              | set(cells[:, 0]) | set(cells[:, -1]))
    sea_ids = {c for c in border if c != 0}

    # 预统计每个连通域：颜色像素数、落在西部块内的像素数、与原西秀/长流的重叠
    ncol = len(COLORS)
    cell_color = np.zeros((ncell + 1, ncol + 1), np.int64)
    inW = np.isin(base_lab, [town2i[t] for t in WEST_PARENTS])
    cell_inW = np.zeros(ncell + 1, np.int64)
    cell_nx = np.zeros(ncell + 1, np.int64)
    cell_nl = np.zeros(ncell + 1, np.int64)
    valid_cells = [c for c in range(1, ncell + 1)
                   if c not in sea_ids and cell_area[c] >= MIN_ZONE_PX]
    for c in valid_cells:
        m = cells == c
        cell_color[c] = np.bincount(cls[m].ravel(), minlength=ncol + 1)
        mW = m & inW
        cell_inW[c] = int(mW.sum())
        if cell_inW[c]:
            v = base_lab[mW]
            cell_nx[c] = int((v == town2i["西秀镇"]).sum())
            cell_nl[c] = int((v == town2i["长流镇"]).sum())

    # ---------- 生成新标签图 ----------
    new_lab = base_lab.copy()
    report = {}
    TMP = {"长流镇": 200, "西秀镇": 201}
    TMP["新海乡"] = newid["新海乡"]
    name_tmp = {v: k for k, v in TMP.items()}

    # (a) 西部块：新海乡 / 长流镇 / 西秀镇 按图黑线围出的连通域重切
    for c in valid_cells:
        mW = (cells == c) & inW
        if int(mW.sum()) < MIN_ZONE_PX:
            continue
        if cell_color[c, 1] / float(cell_area[c]) > 0.5:   # 该连通域即新海乡
            tgt = TMP["新海乡"]
        else:                                              # 否则按与现乡镇重叠归 西秀镇/长流镇
            tgt = TMP["西秀镇"] if cell_nx[c] >= cell_nl[c] else TMP["长流镇"]
        new_lab[mW] = tgt
    # 西部块内未被连通域覆盖的像素（黑线/碎块）归最近西部单元，保证整块被切满
    wmask = np.isin(new_lab, list(TMP.values()))
    if (~wmask & inW).any():
        _, (iy, ix) = ndimage.distance_transform_edt(~wmask, return_indices=True)
        near = new_lab[iy, ix]
        rem = inW & ~wmask
        new_lab[rem] = near[rem]
    print(f"西部块（西秀镇/长流镇/新海乡）按图重切，涉及栅格 {int(inW.sum())} px")

    # (b) 其余色块：整块连通域按颜色归属（色块占比≥0.3 即整块归该新乡镇），再裁剪到父级
    for nm, ps in PARENTS.items():
        if nm == "新海乡":
            continue
        k = [i for i, (n2, _) in enumerate(COLORS, 1) if n2 == nm][0]
        pmask = np.isin(base_lab, [town2i[p] for p in ps])
        used = 0
        for c in valid_cells:
            if cell_color[c, k] == 0:
                continue
            if cell_color[c, k] / float(cell_area[c]) < 0.3:
                continue
            mm = (cells == c) & pmask
            new_lab[mm] = newid[nm]
            used += int(mm.sum())
        report[nm] = dict(used_px=used)

    # ---------- 矢量化（只取"涉及新单元"的边界）----------
    NEW_IDS = set(newid.values()) | set(TMP.values())
    isNew = np.isin(new_lab, list(NEW_IDS))
    pnew = np.pad(isNew, 1, constant_values=False)
    plab = np.pad(np.where(isNew, new_lab, -1), 1, constant_values=-2)

    vm = plab[:, 1:] != plab[:, :-1]
    vm &= (pnew[:, 1:] | pnew[:, :-1])
    hm = plab[1:, :] != plab[:-1, :]
    hm &= (pnew[1:, :] | pnew[:-1, :])
    vert = vm
    hedge = hm
    print("边界格点边数 vert/hedge:", int(vert.sum()), int(hedge.sum()))

    nb = build_graph(vert, hedge)
    arcs = trace_arcs(nb)
    print("弧段数", len(arcs))

    def px2geo(x, y):
        return minx + (x - 1) * sx, maxy - (y - 1) * sy

    lines = []
    for p in arcs:
        if len(p) < 2:
            continue
        sm = smooth_pts(p, SMOOTH_WIN)
        ls = LineString([px2geo(float(a), float(b)) for a, b in sm])
        if len(p) > 2:
            try:
                ls = ls.simplify(SIMP_TOL, preserve_topology=False)
            except Exception:
                pass
        if ls.length > 1e-6:
            lines.append(ls)
    net = unary_union(lines)
    faces = [f for f in polygonize(net) if (not f.is_empty) and f.area > 0]
    print("polygonize 面数", len(faces))

    def face_label(f):
        fr = rasterize([(f, 1)], out_shape=(Hr, Wr),
                       transform=Affine(sx, 0, minx, 0, -sy, maxy), fill=0, dtype="uint8")
        m = fr > 0
        if not m.any():
            return 0, 0.0
        vals, cnt = np.unique(new_lab[m], return_counts=True)
        sel = np.isin(vals, list(NEW_IDS))
        if not sel.any():
            return 0, 0.0
        vals, cnt = vals[sel], cnt[sel]
        j = int(np.argmax(cnt))
        return int(vals[j]), float(cnt[j]) / float(cnt.sum())

    id2name = {v: k for k, v in newid.items()}
    id2name.update(name_tmp)
    groups = {}
    for f in faces:
        L, frac = face_label(f)
        if L in NEW_IDS and frac > 0.5:
            groups.setdefault(L, []).append(f)

    new_geoms = {}
    for L, fs in groups.items():
        gg = sanitize(unary_union(fs))
        if gg is not None and (not gg.is_empty) and gg.area > 0:
            new_geoms[id2name[L]] = gg
    print("\n新单元几何（km²）：")
    for nm, _ in COLORS:
        if nm in new_geoms:
            print(f"  {nm:6s} {new_geoms[nm].area/1e6:8.3f}")
        else:
            print(f"  {nm:6s}  !! 未生成")
    missing = [nm for nm, _ in COLORS if nm not in new_geoms]
    assert not missing, f"缺失新单元：{missing}"

    # ---------- 组成新海口图层 ----------
    inW_u = unary_union([hk.geometry[i] for i in hk.index
                         if hk.TOWN[i] in WEST_PARENTS]).buffer(0)
    # 西部块：裁剪到西部块并重组成严丝合缝的三块（补上栅格平滑造成的海岸细缝）
    if all(nm in new_geoms for nm in ("新海乡", "长流镇", "西秀镇")):
        g_xh = sanitize(new_geoms["新海乡"].intersection(inW_u))
        g_cl = sanitize(new_geoms["长流镇"].intersection(inW_u).difference(g_xh))
        g_xx = sanitize(new_geoms["西秀镇"].intersection(inW_u)
                        .difference(g_xh).difference(g_cl))
        rest = sanitize(inW_u.difference(unary_union([g_xh, g_cl, g_xx])))
        if rest is not None and not rest.is_empty and rest.area > 0:
            for part in getattr(rest, "geoms", [rest]):
                rep = part.representative_point()
                d = {"新海乡": g_xh.distance(rep), "长流镇": g_cl.distance(rep),
                     "西秀镇": g_xx.distance(rep)}
                tgt = min(d, key=d.get)
                if tgt == "新海乡":
                    g_xh = sanitize(unary_union([g_xh, part]))
                elif tgt == "长流镇":
                    g_cl = sanitize(unary_union([g_cl, part]))
                else:
                    g_xx = sanitize(unary_union([g_xx, part]))
        new_geoms["新海乡"], new_geoms["长流镇"], new_geoms["西秀镇"] = g_xh, g_cl, g_xx
    # 其余新乡镇：裁剪到父级并集；再整体外扩 SNAP_TOL。
    # 依据 2002海口转换总结.txt：手绘线配准/量化误差（色块边缘内缩约半个线宽）属"窄差异"，
    # 一律并入新乡镇 —— 使新界线落到父级界线/黑线中心，消除母乡镇细条。
    SNAP_TOL = 90.0
    for nm, ps in PARENTS.items():
        if nm == "新海乡":
            continue
        pidx = [int(hk.index[hk.TOWN == p][0]) for p in ps]
        pu = unary_union([hk.geometry[k].buffer(0) for k in pidx]).buffer(0)
        ng = sanitize(new_geoms[nm].intersection(pu))
        add = pu.intersection(ng.buffer(SNAP_TOL))
        ng = sanitize(unary_union([ng, add]))
        new_geoms[nm] = ng

    rows = []
    geom_now = {}
    for i, r in hk.iterrows():
        g0 = r.geometry
        if r["TOWN"] in WEST_PARENTS and r["TOWN"] in new_geoms:
            g0 = new_geoms[r["TOWN"]]
        rows.append(dict(CODE=r["CODE"], TOWN=r["TOWN"], CITY=r["CITY"], EN=r["EN"],
                         N_FEAT=int(r["N_FEAT"])))
        geom_now[i] = g0

    for nm, ps in PARENTS.items():
        if nm == "新海乡":
            continue
        ngeom = new_geoms[nm]
        for p in ps:
            j = int(hk.index[hk.TOWN == p][0])
            sub = geom_now[j].intersection(ngeom)
            if (not sub.is_empty) and sub.area > 0:
                geom_now[j] = sanitize(geom_now[j].difference(sub))
    for i, r in hk.iterrows():
        rows[i]["geometry"] = geom_now[i]

    k = 0
    for nm, _ in COLORS:
        k += 1
        rows.append(dict(CODE="HK%02d" % k, TOWN=nm, CITY="海口市", EN="Haikou",
                         N_FEAT=0, geometry=new_geoms[nm]))

    out_hk = gpd.GeoDataFrame(rows, geometry="geometry", crs=TARGET_CRS)
    out_hk["geometry"] = out_hk.geometry.apply(as_multi)

    # ---------- 清理退化碎屑：<0.001 km² 的孤立小块并入最近单元（仅新增/重画单元） ----------
    MIN_PART = 1000.0        # m²
    NEAR_M = 50.0            # 与其它单元的距离阈值
    geoms = {i: sanitize(out_hk.geometry[i]) for i in out_hk.index}
    clean_idx = [i for i in out_hk.index if str(out_hk.CODE[i]).startswith("HK")]
    drops = []
    for i in clean_idx:
        gm = geoms[i]
        ps = list(gm.geoms) if gm.geom_type == "MultiPolygon" else [gm]
        big = [p for p in ps if p.area >= MIN_PART]
        small = [p for p in ps if p.area < MIN_PART]
        if not big:                                  # 全是小块则保留最大块
            big = [max(ps, key=lambda p: p.area)]
            small = [p for p in ps if p is not big[0]]
        geoms[i] = MultiPolygon(big) if len(big) > 1 else big[0]
        drops.extend(small)
    for p in drops:
        near = [j for j in geoms if geoms[j].distance(p) <= NEAR_M]
        j = min(near or list(geoms), key=lambda j: geoms[j].distance(p))
        geoms[j] = sanitize(unary_union([geoms[j], p]))
    for i in out_hk.index:
        out_hk.at[i, "geometry"] = as_multi(geoms[i])

    out_hk["AREA_KM2"] = (out_hk.geometry.area / 1e6).round(3)
    out_hk = out_hk[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
    print(f"\n新海口单元数 {len(out_hk)}（原 41 + 新 9）")
    print(out_hk[out_hk.CODE.astype(str).str.startswith("HK")][
        ["CODE", "TOWN", "AREA_KM2"]].to_string(index=False))
    print("\n面积最小的 8 个：")
    print(out_hk.nsmallest(8, "AREA_KM2")[["CODE", "TOWN", "AREA_KM2"]].to_string(index=False))

    # ---------- 质量检查 ----------
    uu_old = unary_union(list(hk.geometry)).buffer(0)
    uu_new = unary_union(list(out_hk.geometry)).buffer(0)
    print(f"\n海口总面积 old={uu_old.area/1e6:.3f}  new={uu_new.area/1e6:.3f} km²"
          f"  差={(uu_new.area-uu_old.area)/1e6:+.4f}")
    ov = 0.0
    gl = list(out_hk.geometry)
    for a in range(len(gl)):
        for b in range(a + 1, len(gl)):
            if gl[a].intersects(gl[b]):
                ov += gl[a].intersection(gl[b]).area
    print(f"内部重叠 {ov/1e6:.6f} km²")
    print(f"无效几何 {int((~out_hk.geometry.is_valid).sum())}/{len(out_hk)}")
    print(f"与原海口轮廓对称差 {uu_old.symmetric_difference(uu_new).area/1e6:.6f} km²（应≈0）")
    others = unary_union(g[~is_hk].geometry).buffer(0)
    print(f"与邻县重叠 {uu_new.intersection(others).area/1e6:.6f} km²")

    print("\n海口各单元改动量：")
    nchg = 0
    for i, r in hk.iterrows():
        d = r.geometry.symmetric_difference(geom_now[i]).area
        if d > 1e-6:
            nchg += 1
            print(f"  {r['CODE']} {r['TOWN']:8s} 减少 {d/1e6:7.3f} km²"
                  f"  ({r.geometry.area/1e6:.2f} → {geom_now[i].area/1e6:.2f})")
    print(f"  共改动 {nchg}/41")

    # ---------- 写回 ----------
    if not os.path.exists(HT[:-4] + "_before_haikou2002.shp"):
        backup(HT)
    merged = gpd.GeoDataFrame(
        pd.concat([out_hk.to_crs(gdf.crs), keep_geo], ignore_index=True), crs=gdf.crs)
    merged["geometry"] = merged.geometry.apply(as_multi)
    write_shp(merged, HT)
    print(f"\n已写回 {HT}：{len(merged)} 单元（海口 {len(out_hk)} + 其他 {len(keep_geo)}）")

    np.save(os.path.join(WORK, "new_lab.npy"), new_lab)
    with open(os.path.join(WORK, "build_log.json"), "w", encoding="utf-8") as fp:
        json.dump({"report": report,
                   "new_areas": {k2: round(v.area / 1e6, 3) for k2, v in new_geoms.items()},
                   "total_old": round(uu_old.area / 1e6, 3),
                   "total_new": round(uu_new.area / 1e6, 3)}, fp,
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
