# -*- coding: utf-8 -*-
"""
海南乡镇边界拓扑重建修复 v4（终版）
====================================
根因（诊断结论）：
  相邻乡镇对共享边界各画一遍，但两侧顶点密度/亚米级坐标不完全一致
  （例：崖城画 3 个点、梅山同段画 2 个点），unary_union 无法识别为同一条线，
  同段边界留下两条"几乎重合"的线 → 6,533 个零面积退化面 + 环互相亲附 →
  polygonize 产生跨镇巨型面片（崖城+梅山并成 441 km²）+ 30-80m 真偏移带。

修复管线：
  1) 全部边界 unary_union 节点化；
  2) set_precision 吸附到 2m 网格后再次 union：亚米级差异塌缩，
     共享边只剩一条线（退化面 6,533 → 23，跨镇面片 → 0）；
  3) polygonize 重建面片：每片领土唯一归属；偏移双线间的窄带
     成为独立"无主面片"（缝隙型）或被单镇覆盖（重叠型）；
  4) 归属：面片代表点落在哪个原乡镇内 → 归哪个镇（吸附仅引入 ≤1m 错位，
     干净拓扑下不会跨镇，无需裁剪）；
     无主窄带 → 按"共享边最长"吸收进邻镇 → 偏移带单线化；
     大块无主面 → 保留为洞；
  5) 逐镇 union 重组 → 共享边两侧顶点逐点一致，数据中每段边界只存一次。

安全：只写新文件 Hainan_town_topo.shp（临时文件 + os.replace 原子替换），
      原 Hainan_town_codefix.shp 全程只读。
"""
import os
import json
import numpy as np
import geopandas as gpd
import shapely
from shapely.ops import unary_union, polygonize
from shapely import STRtree

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
SRC  = os.path.join(BASE, "Hainan_town_codefix.shp")
DST  = os.path.join(BASE, "Hainan_town_topo.shp")
REPORT = os.path.join(BASE, "fix_report.json")

GRID             = 2.0    # 吸附网格（米）：消除亚米级坐标/顶点密度差异
SLIVER_WIDTH_M   = 150.0  # 窄带判定：有效宽度 2*面积/周长 < 150m
SLIVER_AREA_MAX  = 5e5    # 且面积 < 0.5 km²
OFFSET_LO, OFFSET_HI = 25.0, 100.0   # "真偏移 30-80m" 统计区间

# ---------- 读入 ----------
gdf = gpd.read_file(SRC, encoding="utf-8")
orig_crs = gdf.crs
gdf = gdf.to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
n_feat = len(gdf)
polys = list(gdf.geometry)
print(f"读入 {n_feat} 个要素")

per_poly_old = sum(g.boundary.length for g in polys)
net_old = unary_union([g.boundary for g in polys])
net_old_len = net_old.length
print(f"[修复前] 逐面边界总长 {per_poly_old:,.0f} m | 唯一线网 {net_old_len:,.0f} m")

# ---------- Step 1+2: 节点化 + 网格吸附去重 ----------
snapped = shapely.set_precision(net_old, GRID)
parts = [g for g in (snapped.geoms if snapped.geom_type == "MultiLineString"
                     else [snapped]) if g.length > 0]
net = unary_union(parts)
net_lines = net.geoms if net.geom_type == "MultiLineString" else [net]
print(f"吸附 {GRID}m 网格后：线网 {net_old_len:,.0f} → {net.length:,.0f} m")

# ---------- Step 3: 面片化 ----------
faces_all = list(polygonize(net_lines))
faces = [f for f in faces_all if f.area >= 1.0]
n_deg = len(faces_all) - len(faces)
print(f"面片化：{len(faces_all)} 个（有效 {len(faces)}，退化 {n_deg} 丢弃；"
      f"修复前退化为 6,533）")

def band_width(f):
    bl = f.boundary.length
    return 2.0 * f.area / bl if bl > 0 else 0.0

# ---------- Step 4: 面片归属（代表点包含，无裁剪） ----------
tree = STRtree(polys)
assigned = {}
leftovers = []
n_point = n_fallback = 0

for f in faces:
    rp = f.representative_point()
    # 注意：不用 tree.query(rp, predicate=...)——其方向语义易踩坑，手动判定包含
    owners = [int(i) for i in tree.query(rp) if polys[i].contains(rp)]
    if len(owners) == 1:
        assigned.setdefault(owners[0], []).append(f)
        n_point += 1
        continue
    if len(owners) > 1:   # 原数据本身互相压盖：取重叠面积大者
        best_i, best_a = -1, 0.0
        for i in owners:
            ia = polys[i].intersection(f).area
            if ia > best_a:
                best_a, best_i = ia, i
        assigned.setdefault(best_i, []).append(f)
        n_point += 1
        continue
    # 代表点无主：可能是 1m 吸附缝隙里的正常面片，按最大重叠兜底
    best_i, best_a = -1, 0.0
    for i in tree.query(f):
        ia = polys[i].intersection(f).area
        if ia > best_a:
            best_a, best_i = ia, i
    if best_i >= 0 and best_a >= 0.5 * f.area:
        assigned.setdefault(best_i, []).append(f)
        n_fallback += 1
    else:
        leftovers.append(f)

print(f"代表点归属 {n_point} 个，重叠兜底 {n_fallback} 个，待吸收残余 {len(leftovers)} 个")

# 残余吸收：缝隙型偏移带并入共享边最长的邻镇；大块无主面保留为洞
# 注意：吸附后窄带与原多边形边界有 ≤1m 错位，touches 会落空，用缓冲容差判定
n_absorbed = n_hole = 0
gap_bands = []          # 缝隙型真偏移带记录
for l in leftovers:
    w = band_width(l)
    if w < SLIVER_WIDTH_M and l.area < SLIVER_AREA_MAX:
        scored = []
        for i in tree.query(l.buffer(10.0)):
            i = int(i)
            inter = polys[i].intersection(l.buffer(5.0))
            if inter.length > 1.0:
                scored.append((inter.length, -i))   # -i：并列时取索引小者，结果确定
        if scored:
            best_i = -max(scored)[1]
            assigned.setdefault(best_i, []).append(l)
            n_absorbed += 1
            if OFFSET_LO <= w <= OFFSET_HI:
                gap_bands.append((w, l.area, list(l.bounds), best_i))
    else:
        n_hole += 1

print(f"残余窄带吸收 {n_absorbed} 个（其中 25-100m 缝隙型偏移带 {len(gap_bands)} 处）；"
      f"保留大块无主面（洞）{n_hole} 个")

# ---------- Step 5: 逐镇重组 ----------
new_geoms, kept_orig = [], []
for i in range(n_feat):
    fs = assigned.get(i)
    if fs:
        g = unary_union(fs)
        if g.geom_type == "GeometryCollection":
            from shapely.geometry import MultiPolygon
            ps = [p for p in g.geoms if p.geom_type == "Polygon"]
            g = MultiPolygon(ps) if len(ps) > 1 else (ps[0] if ps else None)
        new_geoms.append(g if g is not None and g.area > 0 else polys[i])
    else:
        new_geoms.append(polys[i])
        kept_orig.append(str(gdf.iloc[i].get("TOWN", i)))
if kept_orig:
    print(f"⚠️ 无面片归属、沿用原几何：{kept_orig}")
else:
    print("所有乡镇均获得重组几何")

gdf["geometry"] = new_geoms
gdf["geometry"] = gdf.geometry.buffer(0)

# ---------- 面积守恒 ----------
old_area = gpd.GeoSeries(polys, crs=gdf.crs).area.values
new_area = gdf.geometry.area.values
d_km2 = (new_area - old_area) / 1e6
imax = int(np.argmax(np.abs(d_km2)))
print(f"乡镇面积变化(km²)：均值 {d_km2.mean():+.5f}，最大 {d_km2[imax]:+.4f}"
      f"（{gdf.iloc[imax]['TOWN']}）")
for i in np.where(np.abs(d_km2) > 0.5)[0]:
    print(f"  ⚠️ {gdf.iloc[i]['TOWN']}: {d_km2[i]:+.3f} km²")

if "AREA_KM2" in gdf.columns:
    gdf["AREA_KM2"] = (new_area / 1e6).round(4)

# ---------- 验证新拓扑 ----------
net_new = unary_union([g.boundary for g in gdf.geometry])
per_poly_new = sum(g.boundary.length for g in gdf.geometry)
lines_new = net_new.geoms if net_new.geom_type == "MultiLineString" else [net_new]
faces_new = list(polygonize(lines_new))
deg_new = sum(1 for f in faces_new if f.area < 1.0)
sliver_new = sum(1 for f in faces_new
                 if 1.0 <= f.area < SLIVER_AREA_MAX and band_width(f) < SLIVER_WIDTH_M)

polys_b = list(gdf.geometry)
tree_b = STRtree(polys_b)
resid_bands = 0
for f in faces_new:
    if f.area < 1.0 or not (OFFSET_LO <= band_width(f) <= OFFSET_HI):
        continue
    if len({int(i) for i in tree_b.query(f, predicate="touches")}) >= 2:
        resid_bands += 1

print(f"\n[修复后] 逐面边界总长 {per_poly_new:,.0f} m | 唯一线网 {net_new.length:,.0f} m")
print(f"[修复后] 线网变化 {net_new.length - net_old_len:+,.0f} m；"
      f"逐面总长变化 {per_poly_new - per_poly_old:+,.0f} m")
print(f"[修复后] 面片 {len(faces_new)}，退化 {deg_new}（修复前 6,533），"
      f"窄碎屑 {sliver_new}，两镇间残余窄带 {resid_bands}")
print(f"[修复后] 有效几何 {int(gdf.geometry.is_valid.sum())}/{n_feat}，要素数 {len(gdf)}")

# ---------- 写出新文件（临时 + os.replace，沙箱安全） ----------
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
    "grid_m": GRID,
    "features": n_feat,
    "per_poly_len_before": per_poly_old, "per_poly_len_after": per_poly_new,
    "network_len_before": net_old_len, "network_len_after": net_new.length,
    "degenerate_faces_before": 6533, "degenerate_faces_after": deg_new,
    "gap_type_offset_bands_fixed": len(gap_bands),
    "residual_inter_town_bands": resid_bands,
    "max_area_shift_km2": float(np.abs(d_km2).max()),
    "gap_bands_top": [
        {"width_m": round(w, 1), "area_m2": round(ar),
         "bounds": [round(v) for v in bb], "absorbed_into": str(gdf.iloc[i]["TOWN"])}
        for w, ar, bb, i in sorted(gap_bands, key=lambda x: -x[1])[:10]],
}
with open(REPORT, "w", encoding="utf-8") as fp:
    json.dump(report, fp, ensure_ascii=False, indent=2)
print(f"报告已写：{REPORT}")
