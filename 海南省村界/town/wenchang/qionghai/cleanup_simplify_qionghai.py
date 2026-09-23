# -*- coding: utf-8 -*-
"""琼海市乡镇边界：细缝清理 + 拓扑一致的简化/平滑
流程：
  1) 读入所有乡镇，构建节点化边界网络(unary_union) —— 使共享边“边匹配”
  2) 网络 DP 简化 (tolerance) + Chaikin 曲线平滑
  3) 重新节点化并 polygonize 得到面片
  4) 面片按与原乡镇的最大重叠归属（保证无缝无压盖）
  5) 细缝清理：面积 < SLIVER 的碎块并入公共边最长的邻镇
  6) 输出 shp + QA 报告 + 前后对比图
"""
from __future__ import annotations
import os
import numpy as np
import geopandas as gpd
import shapely
from shapely.ops import unary_union, polygonize
from shapely.geometry import LineString, MultiLineString, Polygon, MultiPolygon
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_final.shp")
OUT_SHP = os.path.join(HERE, "qionghai2002_simplified.shp")
OUT_TXT = os.path.join(HERE, "qionghai2002_simplified_QA.txt")

TOL = 50.0            # DP 简化容差 m
SLIVER = 300.0        # 细缝清理阈值 m²
CHAIKIN_ITERS = 2
ATTRS = ["CODE", "TOWN", "CITY", "EN", "N_FEAT", "SOURCE"]

log_lines = []
def log(m=""):
    print(m, flush=True)
    log_lines.append(str(m))

def polys(geom):
    if geom is None or geom.is_empty:
        return []
    return list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]

def chaikin(coords, iters):
    c = np.asarray(coords, float)
    if len(c) < 3:
        return c
    closed = np.allclose(c[0], c[-1])
    for _ in range(iters):
        if len(c) < 3:
            break
        q = 0.75 * c[:-1] + 0.25 * c[1:]
        r = 0.25 * c[:-1] + 0.75 * c[1:]
        new = np.empty((2 * (len(c) - 1), 2))
        new[0::2] = q
        new[1::2] = r
        if closed:
            new = np.vstack([new, new[0]])
        else:
            new = np.vstack([c[0], new, c[-1]])
        c = new
    return c

def smooth_line(ls):
    return LineString(chaikin(np.asarray(ls.coords), CHAIKIN_ITERS))

def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    orig = [x.buffer(0) for x in g.geometry]
    n0 = len(g)
    area0 = sum(x.area for x in orig)
    log("=" * 72)
    log("琼海乡镇边界 细缝清理 + 简化平滑")
    log("=" * 72)
    log(f"输入：{os.path.basename(SHP)}  乡镇数 {n0}  总面积 {area0/1e6:.4f} km²")
    log(f"参数：DP容差 {TOL:.0f} m，细缝阈值 {SLIVER:.0f} m²，Chaikin {CHAIKIN_ITERS} 次")

    # ---- 1) 节点化边界网络 ----
    lines = []
    for geom in orig:
        for p in polys(geom):
            lines.append(p.exterior)
            lines.extend(p.interiors)
    net = unary_union(lines)
    net = net if net.geom_type.startswith("Multi") else MultiLineString([net])
    v_net0 = sum(len(x.coords) for x in net.geoms)
    log(f"\n[1] 边界网络：{len(net.geoms)} 条弧，{v_net0} 顶点")

    # ---- 2) 简化 + 平滑 ----
    simp = net.simplify(TOL, preserve_topology=True)
    simp = simp if simp.geom_type.startswith("Multi") else MultiLineString([simp])
    v_simp = sum(len(x.coords) for x in simp.geoms)
    log(f"[2] DP 简化后：{len(simp.geoms)} 条弧，{v_simp} 顶点 "
        f"(压缩 {100*(1-v_simp/v_net0):.1f}%)")

    smoothed = [smooth_line(x) for x in simp.geoms]
    sm = unary_union(smoothed)
    sm = sm if sm.geom_type.startswith("Multi") else MultiLineString([sm])
    v_sm = sum(len(x.coords) for x in sm.geoms)
    log(f"    Chaikin 平滑后：{len(sm.geoms)} 条弧，{v_sm} 顶点")

    # ---- 3) 多边形化 ----
    faces = [f for f in polygonize(sm) if f.area > 1.0]
    log(f"[3] 多边形化得到 {len(faces)} 个面片")

    # ---- 4) 面片归属（最大重叠）----
    tree = STRtree(orig)
    assign = [[] for _ in range(n0)]
    dropped_area = 0.0
    dropped = 0
    for f in faces:
        cand = tree.query(f)
        best, besta = -1, 0.0
        for i in cand:
            a = f.intersection(orig[i]).area
            if a > besta:
                besta, best = a, int(i)
        if best < 0 or besta < 0.5 * f.area:
            dropped += 1
            dropped_area += f.area
            continue
        assign[best].append(f)
    log(f"[4] 归属完成：{sum(len(a) for a in assign)} 面片入镇，"
        f"丢弃 {dropped} 面片 ({dropped_area/1e6:.4f} km²，多为空洞/外部)")

    towns = [unary_union(a).buffer(0) if a else Polygon() for a in assign]

    # ---- 5) 细缝清理：小块并入公共边最长的邻镇 ----
    def clean_slivers(towns):
        changed = True
        rounds = 0
        moved_total = 0.0
        while changed and rounds < 30:
            changed = False
            rounds += 1
            for i in range(len(towns)):
                parts = sorted(polys(towns[i]), key=lambda p: -p.area)
                if len(parts) <= 1:
                    continue
                for p in parts[1:]:
                    if p.area >= SLIVER:
                        continue
                    best_j, best_len = -1, 0.0
                    for j in range(len(towns)):
                        if j == i or towns[j].is_empty:
                            continue
                        L = p.boundary.intersection(towns[j].boundary).length
                        if L > best_len:
                            best_len, best_j = L, j
                    if best_j >= 0 and best_len > 0:
                        towns[best_j] = unary_union([towns[best_j], p]).buffer(0)
                        towns[i] = towns[i].difference(p.buffer(0.001)).buffer(0)
                        moved_total += p.area
                        changed = True
            # 重新拆分主体
        return towns, moved_total, rounds

    towns, moved, rounds = clean_slivers(towns)
    log(f"[5] 细缝清理：{rounds} 轮，重分配 {moved:.0f} m²")

    # ---- 6) 组装输出 ----
    recs = []
    for i, row in g.iterrows():
        rec = {a: row[a] for a in ATTRS}
        geom = towns[i]
        rec["AREA_KM2"] = round(geom.area / 1e6, 6)
        rec["geometry"] = geom
        recs.append(rec)
    out = gpd.GeoDataFrame(recs, crs=g.crs)
    out["AREA_KM2"] = out["geometry"].area / 1e6

    # ---- QA ----
    fin = [x for x in out.geometry]
    u = unary_union(fin)
    log("\n[QA] 拓扑与面积")
    log(f"  面积：{area0/1e6:.4f} → {sum(x.area for x in fin)/1e6:.4f} km² "
        f"(变化 {100*(sum(x.area for x in fin)-area0)/area0:+.4f}%)")
    log(f"  并集面积 {u.area/1e6:.4f} km²，与面积和差 "
        f"{abs(u.area-sum(x.area for x in fin)):.2f} m² (应≈0=无缝无压盖)")
    import itertools
    ov = 0.0
    for a, b in itertools.combinations(fin, 2):
        inter = a.intersection(b)
        if inter.area > 0.01:
            ov += inter.area
    log(f"  两两压盖合计 {ov:.2f} m²")
    log(f"  非法几何 {sum(1 for x in fin if not x.is_valid)}")

    # 嘉积镇专项
    jj = out[out["TOWN"] == "嘉积镇"].iloc[0].geometry
    jparts = polys(jj)
    log("\n[QA] 嘉积镇")
    log(f"  分块数 {len(jparts)}，面积 {jj.area/1e6:.4f} km²，周长 {jj.length/1e3:.3f} km")
    small = [p for p in jparts if p.area < SLIVER]
    log(f"  <{SLIVER:.0f} m² 碎块：{len(small)} 个，合计 "
        f"{sum(p.area for p in small):.0f} m²")
    nv = sum(len(p.exterior.coords) + sum(len(h.coords) for h in p.interiors)
             for p in jparts)
    log(f"  顶点数 {nv}")
    # 转角统计
    angs = []
    for p in jparts:
        for ring in [p.exterior] + list(p.interiors):
            c = np.asarray(ring.coords, float)
            if len(c) < 4:
                continue
            v1, v2 = c[1:-1] - c[:-2], c[2:] - c[1:-1]
            n1, n2 = np.linalg.norm(v1, axis=1), np.linalg.norm(v2, axis=1)
            ok = (n1 > 0) & (n2 > 0)
            cosv = np.ones(len(n1))
            cosv[ok] = np.sum(v1[ok] * v2[ok], 1) / (n1[ok] * n2[ok])
            angs.append(np.degrees(np.arccos(np.clip(cosv, -1, 1))))
    if angs:
        ang = np.concatenate(angs)
        log(f"  转角>90° 占比 {(ang>90).mean()*100:.1f}%，>120° 占比 {(ang>120).mean()*100:.1f}%")

    out.to_file(OUT_SHP, encoding="utf-8")
    log(f"\n输出：{OUT_SHP}")
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"报告：{OUT_TXT}")

if __name__ == "__main__":
    main()
