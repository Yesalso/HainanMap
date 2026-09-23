# -*- coding: utf-8 -*-
"""分析 qionghai2002_final.shp 中嘉积镇边界：
   结构异常（飞地/空洞/留尾巴/凸起）+ 边界线锯齿(一折一拐)程度。
"""
from __future__ import annotations
import os, math
import numpy as np
import geopandas as gpd
import cv2
from shapely.ops import unary_union
from shapely.geometry import Polygon, LineString

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_final.shp")
TOWN = "嘉积镇"
OUT_TXT = os.path.join(HERE, "jiaji_final_boundary_report.txt")
OUT_PNG = os.path.join(HERE, "jiaji_final_boundary_check.png")

CELL = 10.0
WIDTH_THR = 60.0
AREA_MIN = 3000.0

log_lines = []
def log(m=""):
    print(m, flush=True)
    log_lines.append(str(m))

def parts_of(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]

def ring_arrays(geom):
    """返回多边形所有环（外环+内环）的 Nx2 数组。"""
    rings = []
    for p in parts_of(geom):
        rings.append(np.asarray(p.exterior.coords, float))
        for h in p.interiors:
            rings.append(np.asarray(h.coords, float))
    return rings

def turn_angles(c):
    """每个顶点的转角(度)：0=直行，180=原路折返(尖刺)。"""
    p0, p1, p2 = c[:-2], c[1:-1], c[2:]
    v1 = p1 - p0
    v2 = p2 - p1
    n1 = np.linalg.norm(v1, axis=1)
    n2 = np.linalg.norm(v2, axis=1)
    ok = (n1 > 0) & (n2 > 0)
    cosv = np.ones(len(p1))
    cosv[ok] = np.sum(v1[ok] * v2[ok], 1) / (n1[ok] * n2[ok])
    cosv = np.clip(cosv, -1, 1)
    return np.degrees(np.arccos(cosv)), n1, n2

def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    rows = g[g["TOWN"] == TOWN]
    log("=" * 70)
    log(f"嘉积镇边界体检  ——  {os.path.basename(SHP)}")
    log("=" * 70)
    log(f"要素数：{len(rows)}  （属性 N_FEAT={list(rows['N_FEAT'])}）")

    jj = unary_union(list(rows.geometry)).buffer(0)
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    log(f"合并后几何类型：{jj.geom_type}；分块数：{len(parts)}；"
        f"总面积 {jj.area/1e6:.4f} km²；总周长 {jj.length/1e3:.3f} km")

    # ---------- 1) 分块 / 飞地 ----------
    log("\n[1] 分块（连片）结构与飞地判断")
    others = unary_union(list(g[g["TOWN"] != TOWN].geometry)).buffer(0)
    parts_info = []
    for k, p in enumerate(parts):
        share = 0.0
        try:
            share = p.boundary.intersection(others.boundary).length
        except Exception:
            pass
        tl = p.boundary.length
        ratio = share / tl if tl else 0.0
        parts_info.append((k, p, share, tl, ratio))
        log(f"  块{k}: 面积 {p.area/1e6:.4f} km² | 周长 {tl/1e3:.3f} km | "
            f"与邻镇公共边 {share/1e3:.3f} km ({ratio*100:.1f}%)")
    if len(parts) > 1:
        log(f"  → 该镇为 {len(parts)} 块不连片（MultiPolygon）。")
    detached = [x for x in parts_info if x[4] < 0.5]
    if detached:
        log("  ⚠ 疑似飞地/孤块（公共边占比<50%，说明被邻镇包围或近乎孤悬）：")
        for k, p, sh, tl, r in detached:
            log(f"      块{k} 面积 {p.area/1e6:.4f} km²，公共边仅占 {r*100:.1f}%")
    else:
        log("  ✓ 各分块与邻镇均有较长公共边，无典型孤悬飞地。")

    # ---------- 2) 空洞 ----------
    log("\n[2] 内部空洞（洞=飞地的一种反向表现）")
    holes = []
    for p in parts:
        for h in p.interiors:
            hp = Polygon(h)
            if hp.area >= AREA_MIN:
                holes.append(hp)
    if holes:
        log(f"  ⚠ 发现 {len(holes)} 个面积≥{AREA_MIN:.0f} m² 的空洞：")
        for i, h in enumerate(holes):
            log(f"      洞{i}: 面积 {h.area:.0f} m²，周长 {h.length:.0f} m")
    else:
        log("  ✓ 无面积≥3000 m² 的内部空洞。")

    # ---------- 3) 留尾巴 / 细窄凸起 ----------
    log(f"\n[3] 留尾巴 / 细窄凸起（局部宽度<{WIDTH_THR:.0f} m）")
    minx, miny, maxx, maxy = jj.bounds
    W = int((maxx - minx) / CELL) + 1
    H = int((maxy - miny) / CELL) + 1
    mask = np.zeros((H, W), np.uint8)

    def to_px(xs, ys):
        xs = np.asarray(xs, float); ys = np.asarray(ys, float)
        return np.stack([(xs - minx) / CELL, (maxy - ys) / CELL], 1).astype(np.int32)

    for p in parts:
        cv2.fillPoly(mask, [to_px(p.exterior.xy[0], p.exterior.xy[1])], 1)
        for h in p.interiors:
            cv2.fillPoly(mask, [to_px(h.xy[0], h.xy[1])], 0)

    dt = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 3) * CELL
    thin = ((mask == 1) & (dt < WIDTH_THR / 2)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(thin * 255, 8)
    tails = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA] * CELL * CELL
        if a >= AREA_MIN:
            tails.append((i, a, st[i, cv2.CC_STAT_WIDTH] * CELL, st[i, cv2.CC_STAT_HEIGHT] * CELL))
    if tails:
        log(f"  ⚠ 发现 {len(tails)} 片细窄区域（可能是留尾巴或细缝）：")
        for i, a, w, h in tails:
            log(f"      片{i}: 面积 {a:.0f} m²，外接尺寸 {w:.0f}×{h:.0f} m")
    else:
        log(f"  ✓ 无面积≥{AREA_MIN:.0f} m² 的细窄区域，未见明显留尾巴。")

    # 形态学开运算：buffer(-30)+buffer(+30) 残差 = 细窄凸出部分
    core = jj.buffer(-WIDTH_THR / 2).buffer(WIDTH_THR / 2)
    sliver = jj.difference(core)
    if sliver.area >= AREA_MIN:
        log(f"  开运算残差（局部宽度<{WIDTH_THR:.0f} m 的部分）合计 {sliver.area:.0f} m² "
            f"（占全镇 {sliver.area/jj.area*100:.3f}%）")
    else:
        log("  开运算残差：可忽略。")

    # ---------- 4) 边界线锯齿/一折一拐程度 ----------
    log("\n[4] 边界线平滑度（是否一折一拐、非平滑曲线）")
    all_ang, all_seg = [], []
    n_vert = 0
    for p in parts:
        for ring in [p.exterior] + list(p.interiors):
            c = np.asarray(ring.coords, float)
            if len(c) < 4:
                continue
            n_vert += len(c) - 1
            ang, n1, n2 = turn_angles(c)
            all_ang.append(ang)
            all_seg.append(n1)
    ang = np.concatenate(all_ang) if all_ang else np.array([])
    seg = np.concatenate(all_seg) if all_seg else np.array([])

    log(f"  顶点总数：{n_vert}")
    if len(seg):
        log(f"  线段长度：中位数 {np.median(seg):.2f} m，均值 {seg.mean():.2f} m，"
            f"最短 {seg.min():.2f} m，<1m 占 {(seg<1).mean()*100:.1f}%，"
            f"<5m 占 {(seg<5).mean()*100:.1f}%，<20m 占 {(seg<20).mean()*100:.1f}%")
    if len(ang):
        log(f"  顶点转角：中位数 {np.median(ang):.1f}°，均值 {ang.mean():.1f}°")
        for thr in (30, 60, 90, 120, 150):
            log(f"    转角>{thr}° 的顶点占比：{(ang>thr).mean()*100:5.1f}%")
        log(f"  → 转角>90°（接近直角急拐）占 {(ang>90).mean()*100:.1f}%；"
            f">120°（尖锐折返/毛刺）占 {(ang>120).mean()*100:.1f}%")

    # 平滑度指标
    if jj.area > 0:
        sinu = jj.length / (2 * math.sqrt(math.pi * jj.area))
        log(f"  曲折度 sinuosity = 周长/(等面积圆周长) = {sinu:.3f}  (1=光滑圆，越大越曲折)")
    conv = jj.area / jj.convex_hull.area if jj.convex_hull.area else 0
    log(f"  凸度 area/convex_hull = {conv:.3f}  (1=凸且饱满，越小凹陷越多)")

    # 毛刺检测：单点外凸，相邻两点几乎在同侧且该点到连线距离小
    log("\n  毛刺/尖刺（伸出后立即折返的尖角）检测：")
    spikes = 0
    for p in parts:
        c = np.asarray(p.exterior.coords, float)
        if len(c) < 4:
            continue
        p0, p1, p2 = c[:-2], c[1:-1], c[2:]
        base = np.linalg.norm(p2 - p0, axis=1)
        v = p1 - p0
        w = p2 - p0
        cross = np.abs(v[:, 0] * w[:, 1] - v[:, 1] * w[:, 0])
        height = np.divide(cross, base, out=np.zeros_like(cross), where=base > 0)
        # 尖刺：突出高度小但两侧都回折（转角大）
        ang, n1, n2 = turn_angles(c)
        m = (height < 8.0) & (ang > 100) & (base < 60.0)
        spikes += int(m.sum())
    log(f"    疑似毛刺顶点数：{spikes}")

    # 高频抖动：比较原边界与轻度平滑(每点取邻域均值)后的长度
    def smooth_ring(c, it=1):
        c = np.asarray(c, float)
        for _ in range(it):
            if len(c) < 5:
                break
            c = 0.25 * np.roll(c, 1, 0) + 0.5 * c + 0.25 * np.roll(c, -1, 0)
        return c
    L0 = Ls = 0.0
    for p in parts:
        for ring in [p.exterior] + list(p.interiors):
            c = np.asarray(ring.coords, float)
            if len(c) < 4:
                continue
            L0 += LineString(c).length
            Ls += LineString(smooth_ring(c, 2)).length
    if Ls:
        log(f"  平滑前后周长：{L0/1e3:.3f} km → {Ls/1e3:.3f} km，"
            f"抖动削减 {100*(L0-Ls)/L0:.1f}% (越大说明锯齿/噪声越重)")

    # ---------- 5) 出图 ----------
    img = np.full((int((maxy - miny) / 8), int((maxx - minx) / 8), 3), 255, np.uint8)
    Hh, Ww = img.shape[:2]
    gw, gh = maxx - minx, maxy - miny

    def to_pts(geom):
        out = []
        for p in parts_of(geom):
            cc = np.array(p.exterior.coords)
            pts = np.stack([(cc[:, 0] - minx) / gw * Ww, (maxy - cc[:, 1]) / gh * Hh], 1)
            if len(pts) >= 3:
                out.append(pts.astype(np.int32))
        return out

    for _, r in g.iterrows():
        col = (0, 0, 255) if r["TOWN"] == TOWN else (150, 200, 150)
        for pts in to_pts(r.geometry):
            cv2.polylines(img, [pts], True, col, 2, cv2.LINE_AA)
    for p in parts:
        for h in p.interiors:
            cc = np.array(h.coords)
            pts = np.stack([(cc[:, 0] - minx) / gw * Ww, (maxy - cc[:, 1]) / gh * Hh], 1).astype(np.int32)
            cv2.polylines(img, [pts], True, (255, 0, 0), 2, cv2.LINE_AA)
    cv2.imencode(".png", img)[1].tofile(OUT_PNG)
    log(f"\n出图：{OUT_PNG}   (红=嘉积镇，浅绿=邻镇)")

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"报告：{OUT_TXT}")

if __name__ == "__main__":
    main()
