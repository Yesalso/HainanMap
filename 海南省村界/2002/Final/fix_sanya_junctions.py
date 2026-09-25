# -*- coding: utf-8 -*-
"""
三亚区域交界节点吸附修复 v2（终版）
==========================================
源：Hainan_town_topo.shp → 输出 Hainan_town_topo_v3.shp（不覆盖原文件）

真实缺陷（diag_sanya5/6/7/9 诊断链结论）：
  47 处交界簇中，多数边界在交界处以锐角收敛属正常拓扑；
  真正缺陷是部分参与镇的顶点未收敛到公共节点（悬挂 40~165m），
  形成可见的 30-80m 偏移双线/缺口。

本版修正：
  - 离散度/节点计算只统计【参与镇对】在【簇心 250m 内】的顶点
    （旧版把 1000m 内路过要素的远顶点计入，导致误判）；
  - 吸附只动参与镇、距簇心 200m 内的最近 1 个顶点；
  - 每次移动后校验 valid + 面积守恒，失败自动回退。
安全：临时文件 + os.replace 原子替换，原文件全程只读。
"""
import os
import json
import numpy as np
import geopandas as gpd
import shapely
from shapely.geometry import Polygon, MultiPolygon
from shapely import STRtree

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
SRC  = os.path.join(BASE, "Hainan_town_topo.shp")
DST  = os.path.join(BASE, "Hainan_town_topo_v3.shp")
REPORT = os.path.join(BASE, "fix_sanya_junction_report.json")

STEP = 10.0            # 边界采样步长 m
OFF_LO, OFF_HI = 25.0, 100.0
MIN_RUN = 60.0
CLUSTER_RADIUS = 250.0
REAL_SPREAD = 15.0     # 参与镇顶点离散度阈值
VTX_RADIUS = 250.0     # 参与镇顶点纳入半径
NODE_TOL = 25.0        # 节点共识半径
SNAP_MAX = 200.0       # 允许吸附的最大移动距离
MOVE_MIN = 5.0         # 距节点小于此值不动

# ---------- 读入 ----------
gdf = gpd.read_file(SRC, encoding="utf-8")
orig_crs = gdf.crs
gdf = gdf.to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
polys = list(gdf.geometry)
mask = gdf["CITY"].astype(str).str.contains("三亚", na=False).to_numpy()
sanya_ids = [i for i in range(len(gdf)) if mask[i]]
other_ids = [i for i in range(len(gdf)) if not mask[i]]
names = gdf["TOWN"].tolist()
print(f"读入 {len(gdf)} 要素（三亚 {len(sanya_ids)}，邻市 {len(other_ids)}）")

borders = {i: polys[i].boundary for i in range(len(gdf))}

# ---------- 偏移段事件（三亚内部 + 三亚↔邻市，仅用于发现交界簇） ----------
def runs(bi, bj):
    L = bi.length
    if L == 0:
        return []
    ds = np.arange(0, L, STEP)
    pts = [bi.interpolate(d) for d in ds]
    dists = shapely.distance(np.array(pts, dtype=object), bj)
    out, k = [], 0
    while k < len(dists):
        if OFF_LO <= dists[k] <= OFF_HI:
            k0 = k
            while k < len(dists) and OFF_LO <= dists[k] <= OFF_HI:
                k += 1
            rl = (k - k0) * STEP
            if rl >= MIN_RUN:
                out.append((rl, bi.interpolate(ds[(k0 + k - 1) // 2])))
        else:
            k += 1
    return out

tree_o = STRtree([borders[i] for i in other_ids])
events = []
for pos, i in enumerate(sanya_ids):
    for j in sanya_ids:
        if j <= i:
            continue
        if borders[i].intersection(borders[j]).length > 50:
            for rl, p in runs(borders[i], borders[j]):
                events.append((rl, p, i, j))
    for bpos in tree_o.query(borders[i]):
        j = other_ids[int(bpos)]
        if borders[i].intersection(borders[j]).length > 50:
            for rl, p in runs(borders[i], borders[j]):
                events.append((rl, p, i, j))
print(f"偏移段事件 {len(events)} 个")

# ---------- 聚类（记录参与镇） ----------
clusters = []  # [center, {feature_ids}]
for rl, p, i, j in events:
    for c in clusters:
        if c[0].distance(p) < CLUSTER_RADIUS:
            c[1].update((i, j))
            break
    else:
        clusters.append([p, {i, j}])
print(f"聚类后 {len(clusters)} 处交界位置")

# ---------- 顶点工具 ----------
def ring_verts(geom):
    out = []
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    for pt in parts:
        out.append(np.array(pt.exterior.coords))
        for r in pt.interiors:
            out.append(np.array(r.coords))
    return np.vstack(out)

VERTS = {i: ring_verts(polys[i]) for i in range(len(gdf))}

def cluster_vertices(center, ids):
    """每个参与镇距簇心 VTX_RADIUS 内的最近顶点；无则不纳入"""
    near = []
    for i in ids:
        v = VERTS[i]
        d = np.hypot(v[:, 0] - center.x, v[:, 1] - center.y)
        k = int(np.argmin(d))
        if d[k] <= VTX_RADIUS:
            near.append((i, float(d[k]), v[k]))
    return near

def analyze(center, ids):
    """返回 (离散度, 参与镇最近顶点列表, 节点N 或 None)"""
    near = cluster_vertices(center, ids)
    if len(near) < 2:
        return 0.0, near, None
    near.sort(key=lambda t: t[1])
    base = near[0][2]
    group = np.array([v for _, _, v in near if np.hypot(*(v - base)) <= NODE_TOL])
    node = group.mean(axis=0)
    pts = np.array([v for _, _, v in near])
    dd = 0.0
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            dd = max(dd, float(np.hypot(*(pts[a] - pts[b]))))
    return dd, near, node

real_clusters = []
for center, ids in clusters:
    dd, near, node = analyze(center, ids)
    if dd > REAL_SPREAD and node is not None:
        real_clusters.append((center, ids, dd, near, node))
print(f"真偏移节点（参与镇顶点离散度>{REAL_SPREAD:.0f}m）{len(real_clusters)} 处")
for center, ids, dd, near, node in real_clusters:
    inv = [names[i] for i in ids]
    print(f"  ({center.x:.0f},{center.y:.0f}) 离散{dd:.0f}m 参与镇{inv} "
          f"顶点距簇心{[f'{names[i]}:{d0:.0f}' for i, d0, _ in near]}")

# ---------- 吸附（仅改三亚要素） ----------
def move_vertex(geom, old, new):
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([move_vertex(g, old, new) for g in geom.geoms])
    rings = [geom.exterior] + list(geom.interiors)
    new_rings, changed = [], False
    for r in rings:
        a = np.array(r.coords)
        m = (a[:, 0] == old[0]) & (a[:, 1] == old[1])
        if m.any():
            a[m, 0], a[m, 1] = new[0], new[1]
            changed = True
        new_rings.append(a)
    if not changed:
        return geom
    return Polygon(new_rings[0], new_rings[1:])

new_geoms = polys[:]
orig_area = {i: polys[i].area for i in range(len(gdf))}
snaps, skipped = [], []

for center, ids, dd, near, node in real_clusters:
    nx, ny = float(node[0]), float(node[1])
    node_pt = shapely.Point(nx, ny)
    for i, d0, v in near:
        v_pt = shapely.Point(float(v[0]), float(v[1]))
        others = [j for j in ids if j != i]
        # 已在节点上
        if v_pt.distance(node_pt) <= MOVE_MIN:
            continue
        # 已连接：候选顶点本身已落在其他参与镇边界上（T 型交界），不动，
        # 否则会把已连通的边界顶点拉走、产生横穿邻线的尖刺
        conn = min(borders[j].distance(v_pt) for j in others)
        if conn <= 5.0:
            continue
        # 吸附目标：节点 与 其他参与镇边界最近点 中距 v 最近者（最小扰动）
        cands = [(nx, ny)]
        for j in others:
            npair = shapely.ops.nearest_points(borders[j], v_pt)
            cands.append((npair[0].x, npair[0].y))
        target = min(cands, key=lambda c: v_pt.distance(shapely.Point(c[0], c[1])))
        mv = float(np.hypot(*(v - np.array(target))))
        if mv <= MOVE_MIN:
            continue
        if mv > SNAP_MAX:
            skipped.append({"cluster": [round(center.x), round(center.y)],
                            "feature": names[i],
                            "vertex_dist_to_center": round(d0),
                            "reason": f"距吸附目标 {mv:.0f}m 超过上限 {SNAP_MAX:.0f}m"})
            continue
        if not mask[i]:
            skipped.append({"cluster": [round(center.x), round(center.y)],
                            "feature": names[i],
                            "reason": "非三亚要素，不修改"})
            continue
        old = (float(v[0]), float(v[1]))
        g_new = move_vertex(new_geoms[i], old, target)
        if (not g_new.is_valid) or abs(g_new.area - orig_area[i]) > 1e5:
            skipped.append({"cluster": [round(center.x), round(center.y)],
                            "feature": names[i],
                            "reason": "吸附后无效或面积突变，已回退"})
            continue
        new_geoms[i] = g_new
        VERTS[i] = ring_verts(g_new)
        borders[i] = g_new.boundary
        snaps.append({"feature": names[i],
                      "cluster": [round(center.x), round(center.y)],
                      "moved_m": round(mv, 1),
                      "from": [round(old[0], 1), round(old[1], 1)],
                      "to": [round(target[0], 1), round(target[1], 1)]})

print(f"吸附顶点 {len(snaps)} 个；未处理 {len(skipped)} 个")

# ---------- 应用并验证 ----------
gdf["geometry"] = [g.buffer(0) for g in new_geoms]
print(f"有效几何 {int(gdf.geometry.is_valid.sum())}/{len(gdf)}")

area_before = np.array([orig_area[i] for i in sanya_ids])
area_after = np.array([gdf.geometry.iloc[i].area for i in sanya_ids])
print(f"三亚要素面积最大变化 {np.abs(area_after - area_before).max():.1f} m²")

# 复测：仅参与镇、仅 250m 内顶点
residual = []
for center, ids, dd, near, node in real_clusters:
    dd2, near2, _ = analyze(center, ids)
    if dd2 > REAL_SPREAD:
        residual.append({"cluster": [round(center.x), round(center.y)],
                         "spread_m": round(dd2),
                         "towns": [f"{names[i]}:{d0:.0f}m" for i, d0, _ in near2]})
print(f"复测残余偏移节点 {len(residual)} 处")
for r in residual:
    print(f"  {r['cluster']} 离散{r['spread_m']}m {r['towns']}")

# ---------- 写出新文件 ----------
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
tmp = DST[:-4] + "__tmp.shp"
gdf.to_crs(orig_crs).to_file(tmp, encoding="utf-8")
for ext in SIDECARS:
    s = tmp[:-4] + ext
    if os.path.exists(s):
        os.replace(s, DST[:-4] + ext)
assert not any(os.path.exists(tmp[:-4] + e) for e in SIDECARS)
print(f"\n✅ 已写出：{DST}\n   原文件未做任何修改：{SRC}")

report = {
    "source": SRC, "output": DST,
    "offset_events": len(events), "junction_locations": len(clusters),
    "real_junction_nodes": len(real_clusters),
    "vertices_snapped": len(snaps),
    "skipped": skipped,
    "residual_nodes": residual,
    "snaps": snaps,
}
with open(REPORT, "w", encoding="utf-8") as fp:
    json.dump(report, fp, ensure_ascii=False, indent=2)
print(f"报告已写：{REPORT}")
