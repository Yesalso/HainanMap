# -*- coding: utf-8 -*-
"""analyze_jiaji.py —— 分析 qionghai2002_final.shp 中嘉积镇边界异常：飞地/尾巴/凸起"""
from __future__ import annotations
import os
import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely.geometry import Polygon, MultiPolygon

HERE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(HERE, "qionghai2002_final.shp")

CELL = 10.0          # 光栅像元 m
WIDTH_THR = 60.0     # 局部宽度 < 60m 视为细窄(尾巴/细缝)
AREA_MIN = 1500.0    # 最小报告面积 m²


def parts_of(g):
    if g is None or g.is_empty:
        return []
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]


def log(m=""):
    print(m, flush=True)


def main():
    g = gpd.read_file(SHP, encoding="utf-8")
    town = "嘉积镇"
    rows = g[g["TOWN"] == town]
    log(f"[{town}] 要素数：{len(rows)}")
    if len(rows) == 0:
        raise SystemExit("未找到嘉积镇")
    jj = unary_union(list(rows.geometry))
    parts = sorted(parts_of(jj), key=lambda p: -p.area)
    log(f"总几何类型：{jj.geom_type}，分块数：{len(parts)}，面积 {jj.area/1e6:.4f} km²")

    # ---- 1) 分块 & 飞地判断（通过与他人共享边界长度）----
    others = unary_union(list(g[g["TOWN"] != town].geometry)).buffer(0)
    exclaves = []
    for k, p in enumerate(parts):
        share_len = 0.0
        try:
            share_len = p.boundary.intersection(others.boundary).length
        except Exception:
            pass
        total_len = p.boundary.length
        if share_len / total_len < 0.5:
            exclaves.append((k, p, share_len, total_len))
        log(f"  块{k}：{p.area/1e6:.4f} km²，周长 {total_len/1e3:.2f} km，"
            f"与外部乡镇公共边 {share_len/1e3:.2f} km ({share_len/total_len*100:.1f}%)")
    if exclaves:
        log("\n  ⚠ 疑似飞地（公共边占比 <50%，近乎被包围或孤悬）：")
        for k, p, sl, tl in exclaves:
            log(f"    块{k} {p.area/1e6:.4f} km²，公共边仅 {sl/tl*100:.1f}%")

    # ---- 2) 空洞 ----
    holes = []
    for p in parts:
        for h in p.interiors:
            hp = Polygon(h)
            if hp.area >= AREA_MIN:
                holes.append(hp)
    log(f"\n空洞（面积≥{AREA_MIN:.0f} m²）：{len(holes)} 个" if holes else "\n空洞：无")

    # ---- 3) 细窄尾巴/细缝检测（距离变换）----
    b = jj.bounds
    minx, miny, maxx, maxy = b
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

    dt = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 3)
    R = dt * CELL
    thin = (mask == 1) & (R < WIDTH_THR / 2)
    n, lab, st, _ = cv2.connectedComponentsWithStats(thin.astype(np.uint8) * 255, 8)

    tails = []
    for i in range(1, n):
        a = st[i, cv2.CC_STAT_AREA] * CELL * CELL
        if a < AREA_MIN:
            continue
        ys, xs = np.nonzero(lab == i)
        u = [Polygon([(minx + x * CELL, maxy - y * CELL),
                      (minx + (x + 1) * CELL, maxy - y * CELL),
                      (minx + (x + 1) * CELL, maxy - (y + 1) * CELL),
                      (minx + x * CELL, maxy - (y + 1) * CELL)])
             for y, x in zip(ys, xs)]
        tails.append((i, a, unary_union(u)))
    tails = [t for t in tails if t[1] >= AREA_MIN]
    log(f"\n细窄区域（局部宽度<{WIDTH_THR:.0f} m，面积≥{AREA_MIN:.0f} m²）：{len(tails)} 片")

    # ---- 4) 凸起检测：窄长尖角（凸包差分 + 细长率）----
    spikes = []
    for k, p in enumerate(parts):
        hull = p.convex_hull
        if hull.is_empty:
            continue
        diff = hull.difference(p)
        # 对 hull 上到多边形边界面积占比大的部位 => 计入容差描述
        if p.area <= 0:
            continue
        # 细长率（周长的窄带）: 面积 / (半周长)^2
        thin_ratio = p.area / (p.length / 2) ** 2
        if thin_ratio < 0.02:
            spikes.append((k, p, "细长率低(整体窄长)", thin_ratio))

    # 凸起：把多边形内部窄于 60m 且不与任何更宽核心 8邻接的顶点钳形量
    for i, (labid, a, geom) in enumerate(tails):
        # 尾巴若是贴壳体且伸出去，不在此标注，见出图
        pass

    # 更严格凸起：局部地块 w.r.t hull 缺口形状的“外凸尖刺”
    protrusions = []
    for k, p in enumerate(parts):
        hull = p.convex_hull
        if hull.is_empty or p.area <= 0:
            continue
        d = hull.difference(p).area
        if d / hull.area > 0.25:
            protrusions.append((k, "凹入>25%", d / hull.area))
    if protrusions:
        log("\n⚠ 相对凸包凹入较大（可能是锯齿/异常缺口）:")
        for k, msg, r in protrusions:
            log(f"    块{k}：{msg} {r*100:.1f}%")
    else:
        log("\n凸包凹入比：正常")

    if spikes:
        log("\n⚠ 整体细长块（可能有留尾巴嫌疑）：")
        for k, p, m, r in spikes:
            log(f"    块{k} {p.area/1e6:.4f} km²：{m} {r:.4f}")

    # ---- 5) 出图 ----
    img = None
    src = os.path.join(HERE, "Qionghai2002.png")
    if os.path.exists(src):
        img = cv2.imdecode(np.fromfile(src, dtype=np.uint8), cv2.IMREAD_COLOR)
    gw, gh = maxx - minx, maxy - miny
    if img is None or img.shape[0] == 0:
        img = np.full((int(gh / 30), int(gw / 30), 3), 255, np.uint8)
    Hh, Ww = img.shape[:2]

    def to_pts(geom, pad=0.0):
        out = []
        for p in parts_of(geom):
            c = np.array(p.exterior.coords)
            pts = np.stack([(c[:, 0] - minx) / gw * Ww, (maxy - c[:, 1]) / gh * Hh], 1)
            if len(pts) >= 3:
                out.append(pts.astype(np.int32))
        return out

    for _, r in g.iterrows():
        col = (0, 0, 255) if r["TOWN"] == town else (0, 170, 0)
        for pts in to_pts(r.geometry):
            cv2.polylines(img, [pts], True, col, 3, cv2.LINE_AA)
    for _, a, geom in tails:
        for pts in to_pts(geom):
            cv2.polylines(img, [pts], True, (255, 255, 0), 3, cv2.LINE_AA)  # 黄=细尾巴
    out_png = os.path.join(HERE, "jiaji_boundary_analysis.png")
    cv2.imencode(".png", img)[1].tofile(out_png)
    log(f"\n标注图：{out_png}")
    log(f"  红=嘉积镇  绿=其他乡镇  黄=细窄(尾巴/细缝)")


if __name__ == "__main__":
    main()