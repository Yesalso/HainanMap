# -*- coding: utf-8 -*-
"""
三亚市区域拓扑二次修复（源：Hainan_town_topo.shp，输出新文件，不覆盖原文件）
=====================================================================
诊断结论（diag_sanya2.py）：
  三亚 19 镇在 topo 文件中面片化得到 63 片（应≈19），
  42 个多余面片全部被单个镇 100% 覆盖（重叠型双线残留）：
  镇多边形轮廓在窄带两侧各描一遍线，宽 20~475m，
  其中约 24 个宽 20-100m —— 对应 "24 对线段偏移 30-80m"。

修复管线（与全岛 fix_topology.py 相同，仅重建三亚要素）：
  1) 全岛边界 2m 网格吸附 + unary_union 节点化（保证与邻市接边一致）；
  2) polygonize 重建面片；
  3) 面片归属：代表点包含 → 最大重叠兜底(≥0.5) → 窄带按共享边最长吸收；
  4) 仅对三亚 19 镇用归属面片 unary_union 重组 → 镇内/镇间双线溶解为单线；
     其余 302 个要素原样保留。
安全：写出 Hainan_town_topo_v2.shp（临时文件 + os.replace 原子替换），
      原 Hainan_town_topo.shp 全程只读。
"""
import os
import json
import numpy as np
import geopandas as gpd
import shapely
from shapely.ops import unary_union, polygonize
from shapely import STRtree

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
SRC    = os.path.join(BASE, "Hainan_town_topo.shp")
DST    = os.path.join(BASE, "Hainan_town_topo_v2.shp")
REPORT = os.path.join(BASE, "fix_sanya_report.json")

GRID            = 2.0
SLIVER_WIDTH_M  = 150.0
SLIVER_AREA_MAX = 5e5

# ---------- 读入 ----------
gdf = gpd.read_file(SRC, encoding="utf-8")
orig_crs = gdf.crs
gdf = gdf.to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
n_feat = len(gdf)
polys = list(gdf.geometry)
is_sanya = gdf["CITY"].astype(str).str.contains("三亚", na=False).to_numpy()
sanya_idx = [i for i in range(n_feat) if is_sanya[i]]
print(f"读入 {n_feat} 要素，其中三亚 {len(sanya_idx)} 个："
      f"{[gdf.iloc[i]['TOWN'] for i in sanya_idx]}")

# ---------- 修复前指标（三亚） ----------
per_poly_old = sum(polys[i].boundary.length for i in sanya_idx)
net_old = unary_union([polys[i].boundary for i in sanya_idx])
net_snap_old = shapely.set_precision(net_old, GRID)
net_snap_old = unary_union([g for g in (net_snap_old.geoms if net_snap_old.geom_type == "MultiLineString"
                                        else [net_snap_old]) if g.length > 0])
print(f"[修复前] 三亚逐面边界总长 {per_poly_old:,.0f} m | 2m吸附线网 {net_snap_old.length:,.0f} m")

# ---------- Step 1: 全岛线网节点化 ----------
net_all = unary_union([g.boundary for g in polys])
snapped = shapely.set_precision(net_all, GRID)
parts = [g for g in (snapped.geoms if snapped.geom_type == "MultiLineString"
                     else [snapped]) if g.length > 0]
net = unary_union(parts)
net_lines = net.geoms if net.geom_type == "MultiLineString" else [net]

# ---------- Step 2: 面片化 ----------
faces_all = list(polygonize(net_lines))
faces = [f for f in faces_all if f.area >= 1.0]
print(f"面片化：{len(faces_all)} 个（有效 {len(faces)}）")

def band_width(f):
    bl = f.boundary.length
    return 2.0 * f.area / bl if bl > 0 else 0.0

# ---------- Step 3: 面片归属 ----------
tree = STRtree(polys)
assigned = {}
n_point = n_fallback = n_absorb = n_hole = 0
absorbed_bands = []
leftovers = []

for f in faces:
    rp = f.representative_point()
    owners = [int(i) for i in tree.query(rp) if polys[i].contains(rp)]
    if len(owners) == 1:
        assigned.setdefault(owners[0], []).append(f)
        n_point += 1
        continue
    if len(owners) > 1:
        best_i, best_a = -1, 0.0
        for i in owners:
            ia = polys[i].intersection(f).area
            if ia > best_a:
                best_a, best_i = ia, i
        assigned.setdefault(best_i, []).append(f)
        n_point += 1
        continue
    leftovers.append(f)

for l in leftovers:
    best_i, best_a = -1, 0.0
    for i in tree.query(l):
        i = int(i)
        ia = polys[i].intersection(l).area
        if ia > best_a:
            best_a, best_i = ia, i
    if best_i >= 0 and best_a >= 0.5 * l.area:
        assigned.setdefault(best_i, []).append(l)
        n_fallback += 1
    else:
        w = band_width(l)
        if w < SLIVER_WIDTH_M and l.area < SLIVER_AREA_MAX:
            scored = []
            for i in tree.query(l.buffer(10.0)):
                i = int(i)
                inter = polys[i].intersection(l.buffer(5.0))
                if inter.length > 1.0:
                    scored.append((inter.length, -i))
            if scored:
                best_i = -max(scored)[1]
                assigned.setdefault(best_i, []).append(l)
                n_absorb += 1
                if 20.0 <= w <= 120.0:
                    absorbed_bands.append(
                        {"width_m": round(w, 1), "area_m2": round(l.area),
                         "bounds": [round(v) for v in l.bounds],
                         "absorbed_into": str(gdf.iloc[best_i]["TOWN"])})
        else:
            n_hole += 1

print(f"代表点归属 {n_point}，重叠兜底 {n_fallback}，窄带吸收 {n_absorb}，"
      f"保留洞 {n_hole}")
print(f"其中 20-120m 缝隙型偏移带吸收 {len(absorbed_bands)} 处")

# ---------- Step 4: 仅重组三亚要素 ----------
new_geoms = polys[:]
rebuilt = 0
for i in sanya_idx:
    fs = assigned.get(i)
    if not fs:
        print(f"⚠️ {gdf.iloc[i]['TOWN']} 无面片归属，沿用原几何")
        continue
    g = unary_union(fs)
    if g.geom_type == "GeometryCollection":
        ps = [p for p in g.geoms if p.geom_type == "Polygon"]
        from shapely.geometry import MultiPolygon
        g = MultiPolygon(ps) if len(ps) > 1 else (ps[0] if ps else None)
    if g is not None and g.area > 0:
        new_geoms[i] = g
        rebuilt += 1
print(f"三亚重组 {rebuilt}/{len(sanya_idx)} 镇")

gdf["geometry"] = new_geoms
gdf["geometry"] = gdf.geometry.buffer(0)

# ---------- 面积守恒（三亚） ----------
old_area = np.array([polys[i].area for i in sanya_idx])
new_area = np.array([gdf.geometry.iloc[i].area for i in sanya_idx])
d_km2 = (new_area - old_area) / 1e6
print(f"三亚面积变化(km²)：均值 {d_km2.mean():+.5f}，最大 |{np.abs(d_km2).max():+.4f}|")
for k in np.where(np.abs(d_km2) > 0.5)[0]:
    i = sanya_idx[int(k)]
    print(f"  ⚠️ {gdf.iloc[i]['TOWN']}: {d_km2[k]:+.3f} km²")

# 与非三亚邻市接边检查：三亚镇与邻市重叠面积不应增加
sanya_new = [gdf.geometry.iloc[i] for i in sanya_idx]
tree_nb = STRtree([polys[i] for i in range(n_feat) if not is_sanya[i]])
nb_ids = [i for i in range(n_feat) if not is_sanya[i]]
ov_old = ov_new = 0.0
for g in sanya_new:
    for j in tree_nb.query(g, predicate="intersects"):
        j = int(j)
        ov_new += g.intersection(polys[nb_ids[j]]).area
for g in [polys[i] for i in sanya_idx]:
    for j in tree_nb.query(g, predicate="intersects"):
        j = int(j)
        ov_old += g.intersection(polys[nb_ids[j]]).area
print(f"与邻市重叠面积：修复前 {ov_old/1e6:.4f} km² → 修复后 {ov_new/1e6:.4f} km²")

# ---------- 验证新拓扑（三亚） ----------
net_new = shapely.set_precision(
    unary_union([gdf.geometry.iloc[i].boundary for i in sanya_idx]), GRID)
net_new = unary_union([g for g in (net_new.geoms if net_new.geom_type == "MultiLineString"
                                   else [net_new]) if g.length > 0])
per_poly_new = sum(gdf.geometry.iloc[i].boundary.length for i in sanya_idx)
lines_new = net_new.geoms if net_new.geom_type == "MultiLineString" else [net_new]
faces_new = [f for f in polygonize(lines_new) if f.area >= 1.0]

# 残余重叠型双线：面片被某镇 100% 覆盖但镇被拆成多片 → 宽 20-120m 的残余
tree_b = STRtree([gdf.geometry.iloc[i] for i in sanya_idx])
residual = 0
for f in faces_new:
    w = band_width(f)
    if 20.0 <= w <= 120.0 and f.area < 2e5:
        residual += 1
print(f"\n[修复后] 三亚逐面边界总长 {per_poly_new:,.0f} m | 2m吸附线网 {net_new.length:,.0f} m")
print(f"[修复后] 三亚面片数 {len(faces_new)}（修复前 63）；"
      f"20-120m 残余窄带 {residual} 个（修复前 ~24）")
print(f"[修复后] 有效几何 {int(gdf.geometry.is_valid.sum())}/{n_feat}，要素数 {len(gdf)}")

# ---------- 写出新文件（临时 + os.replace，不覆盖原文件） ----------
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
tmp = DST[:-4] + "__tmp.shp"
gdf.to_crs(orig_crs).to_file(tmp, encoding="utf-8")
for ext in SIDECARS:
    s = tmp[:-4] + ext
    if os.path.exists(s):
        os.replace(s, DST[:-4] + ext)
assert not any(os.path.exists(tmp[:-4] + e) for e in SIDECARS)
print(f"\n✅ 已写出：{DST}\n   原文件未做任何修改：{SRC}")

# ---------- 报告 ----------
report = {
    "source": SRC, "output": DST,
    "grid_m": GRID,
    "features_total": n_feat, "sanya_towns": len(sanya_idx),
    "sanya_per_poly_len_before": per_poly_old,
    "sanya_per_poly_len_after": per_poly_new,
    "sanya_network_len_before": net_snap_old.length,
    "sanya_network_len_after": net_new.length,
    "sanya_faces_before": 63, "sanya_faces_after": len(faces_new),
    "residual_narrow_bands": residual,
    "gap_bands_absorbed": absorbed_bands,
    "max_sanya_area_shift_km2": float(np.abs(d_km2).max()),
    "neighbor_overlap_km2_before": ov_old / 1e6,
    "neighbor_overlap_km2_after": ov_new / 1e6,
}
with open(REPORT, "w", encoding="utf-8") as fp:
    json.dump(report, fp, ensure_ascii=False, indent=2)
print(f"报告已写：{REPORT}")
