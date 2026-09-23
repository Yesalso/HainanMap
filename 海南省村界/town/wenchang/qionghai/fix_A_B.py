# -*- coding: utf-8 -*-
"""修正 qionghai2002_simplified.shp 两处问题：
   A) 嘉积镇/中原镇边界上的发夹形“直线凸起” -> 还原为直线
   B) 嘉积镇 1824 m² 飞地 -> 划入就近的温泉镇
输出：qionghai2002_corrected.shp
"""
from __future__ import annotations
import os
import numpy as np
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_simplified.shp")
OUT = os.path.join(HERE, "qionghai2002_corrected.shp")
OUT_TXT = os.path.join(HERE, "qionghai2002_corrected_QA.txt")

P_TOP = np.array([445009.23, 2126826.69])   # 发夹上端点(嘉积/中原共用)
P_BOT = np.array([445031.17, 2126246.49])   # 发夹下端点(嘉积/中原共用)
WITNESS = np.array([445004.41, 2126541.69]) # 位于发夹弧上的点
EXCLAVE_PT = (442102.15, 2128664.49)        # 飞地中心
EXCLAVE_TARGET = "温泉镇"

log_lines = []
def log(m=""):
    print(m, flush=True); log_lines.append(str(m))

def parts(x):
    return list(x.geoms) if x.geom_type.startswith("Multi") else [x]

def remove_arc(coords, pa, pb, witness, tol=0.5):
    """删除环中“含 witness 的 pa-pb 弧”的内部顶点，使 pa、pb 直接相连。"""
    c = np.asarray(coords, float)[:-1]  # open ring
    n = len(c)
    ia = int(np.argmin(np.hypot(c[:, 0]-pa[0], c[:, 1]-pa[1])))
    ib = int(np.argmin(np.hypot(c[:, 0]-pb[0], c[:, 1]-pb[1])))
    if np.hypot(*(c[ia]-pa)) > tol or np.hypot(*(c[ib]-pb)) > tol:
        return None
    if ia <= ib:
        fwd = list(range(ia, ib+1))
    else:
        fwd = list(range(ia, n)) + list(range(0, ib+1))
    fwd_set = set(fwd)
    w_in_fwd = any(np.hypot(*(c[k]-witness)) < tol for k in fwd)
    if w_in_fwd:
        remove = fwd_set - {ia, ib}
    else:
        remove = set(k for k in range(n) if k not in fwd_set)
    keep = [k for k in range(n) if k not in remove]
    new = c[keep]
    new = np.vstack([new, new[0]])
    return new

def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    area0 = g.geometry.area.sum()
    log("=" * 68)
    log("修正 A(直线凸起) + B(飞地)")
    log("=" * 68)

    geoms = list(g.geometry)

    # ---------- A: 修复发夹 ----------
    for i, r in enumerate(g.itertuples()):
        if r.TOWN not in ("嘉积镇", "中原镇"):
            continue
        polys = parts(geoms[i])
        newpolys = []
        fixed = 0
        for p in polys:
            c = np.asarray(p.exterior.coords, float)
            # 仅当该环包含发夹时处理
            has = (np.min(np.hypot(c[:, 0]-P_TOP[0], c[:, 1]-P_TOP[1])) < 0.5 and
                   np.min(np.hypot(c[:, 0]-P_BOT[0], c[:, 1]-P_BOT[1])) < 0.5)
            if not has:
                newpolys.append(p); continue
            nc = remove_arc(c, P_TOP, P_BOT, WITNESS)
            if nc is None:
                newpolys.append(p); continue
            holes = [np.asarray(h.coords) for h in p.interiors]
            newpolys.append(Polygon(nc, holes))
            fixed += 1
        geoms[i] = unary_union(newpolys) if len(newpolys) > 1 else newpolys[0]
        log(f"[A] {r.TOWN}: 处理 {fixed} 个含发夹的环")

    # ---------- B: 飞地划入温泉镇 ----------
    jj_i = list(g[g.TOWN == "嘉积镇"].index)[0]
    wx_i = list(g[g.TOWN == EXCLAVE_TARGET].index)[0]
    ex = None
    rest = []
    for p in parts(geoms[jj_i]):
        if p.area < 5000 and np.hypot(p.centroid.x-EXCLAVE_PT[0], p.centroid.y-EXCLAVE_PT[1]) < 50:
            ex = p
        else:
            rest.append(p)
    log(f"\n[B] 嘉积镇飞地：面积 {ex.area:.0f} m²，中心 ({ex.centroid.x:.0f},{ex.centroid.y:.0f})")
    geoms[jj_i] = unary_union(rest).buffer(0)
    geoms[wx_i] = unary_union([geoms[wx_i], ex]).buffer(0)
    log(f"[B] 已划入 {EXCLAVE_TARGET}")

    out = g.copy()
    out["geometry"] = geoms
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=g.crs)
    out["AREA_KM2"] = out.geometry.area / 1e6

    # ---------- QA ----------
    fin = list(out.geometry)
    u = unary_union(fin)
    log("\n[QA]")
    log(f"  面积 {area0/1e6:.4f} → {sum(x.area for x in fin)/1e6:.4f} km² "
        f"({100*(sum(x.area for x in fin)-area0)/area0:+.4f}%)")
    log(f"  并集-面积和 {abs(u.area-sum(x.area for x in fin)):.3f} m² (应≈0)")
    import itertools
    ov = sum(a.intersection(b).area for a, b in itertools.combinations(fin, 2)
             if a.intersects(b))
    log(f"  两两压盖 {ov:.3f} m²")
    log(f"  非法几何 {sum(1 for x in fin if not x.is_valid)}")
    jj = out[out.TOWN == "嘉积镇"].iloc[0].geometry
    log(f"  嘉积镇：{len(parts(jj))} 块，面积 {jj.area/1e6:.4f} km²")
    wx = out[out.TOWN == EXCLAVE_TARGET].iloc[0].geometry
    log(f"  {EXCLAVE_TARGET}：{len(parts(wx))} 块，面积 {wx.area/1e6:.4f} km²")

    # A 处是否已直
    zj = unary_union(list(out[out.TOWN == "中原镇"].geometry))
    jj_line = [np.asarray(p.exterior.coords) for p in parts(jj)]
    found = any(np.min(np.hypot(c[:, 0]-445004.41, c[:, 1]-2126541.69)) < 1 for c in jj_line)
    log(f"  A处发夹残留：{'是' if found else '否(已消除)'}")

    out.to_file(OUT, encoding="utf-8")
    log(f"\n输出：{OUT}")
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"报告：{OUT_TXT}")

if __name__ == "__main__":
    main()
