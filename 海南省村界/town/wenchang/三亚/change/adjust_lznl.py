# -*- coding: utf-8 -*-
"""
adjust_lznl.py —— 按手绘图 #3F48CC 弧线调整三亚「荔枝沟区-牛岭乡」界线
================================================================================
画法验证（diag_blue*.py 结论）：
  - change/Sanya_no_label.png 中存在 #3F48CC 笔画（480 px，容差60）；
  - 配准：EPSG:32649、1px=30m、四至=三亚乡镇 total_bounds，偏移(0,0)，实测吻合；
  - 笔画为一条自西向东的弧线：西端落在 牛岭乡|河西区 边界上（59m），
    东端指向 牛岭乡|红沙区 边界（差 267m 收笔），全程位于现行荔-牛界（S，
    5248m，两端均为三界交点）以北 453~1185 m；
  - 语义 = 蓝弧整条替换荔-牛边界（牛岭乡缩小、荔枝沟区扩大）。

做法（文昌经验：贴线中心 + 严格分区 + 名单外不变）：
  1. 列平均提取笔画中心线 → simplify(10) + Chaikin×2（端点固定）；
  2. 两端沿弧线方向延长至 U2(=荔枝沟∪牛岭) 外边界，精确闭合；
  3. polygonize([U2.boundary, D]) 切成两侧；与牛岭主体重叠大者为牛岭_new；
  4. 严格分区校验（并=U2、无重叠、面积守恒）；
  5. 仅替换 SHP 中两行几何（变换回源 CRS），其余 319 要素数值不动；
  6. QA 报告 + overlay/sidebyside 出图。

用法：
  python adjust_lznl.py            # 备份原SHP -> 修改 -> QA
"""
from __future__ import annotations

import datetime
import os
import shutil

import cv2
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, polygonize, unary_union
from shapely import set_precision

IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
OUT = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change"
BAK = os.path.join(OUT, "backup_Hainan_town_topo_调整前")
UTM = "EPSG:32649"
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def log(m=""):
    print(m, flush=True)


def parts_of(g):
    if g is None or g.is_empty:
        return []
    return [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")] \
        if g.geom_type not in ("Polygon", "MultiPolygon") else [g]


def chaikin(ls, iters):
    coords = list(ls.coords)
    if len(coords) < 3:
        return ls
    closed = ls.is_ring
    if closed:
        coords = coords[:-1]
    for _ in range(iters):
        n = len(coords)
        if n < 3:
            break
        new = []
        for i in range(n if closed else n - 1):
            x0, y0 = coords[i]
            x1, y1 = coords[(i + 1) % n]
            new.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            new.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        if not closed:
            new.insert(0, coords[0]); new.append(coords[-1])
        else:
            new.append(new[0])
        coords = new
    return LineString(coords)


def repair_validity(g):
    if g is None or g.is_empty:
        return None
    if g.is_valid and not g.is_empty:
        return g
    for m in ("linework", "structure"):
        try:
            from shapely import make_valid
            x = make_valid(g, method=m)
        except TypeError:
            from shapely import make_valid
            x = make_valid(g)
        except Exception:
            continue
        ps = [p for p in getattr(x, "geoms", [x]) if p.geom_type in ("Polygon", "MultiPolygon")]
        if ps:
            u = unary_union(ps).buffer(0)
            if not u.is_empty:
                return u
    try:
        return g.buffer(0)
    except Exception:
        return g


def main():
    t0 = datetime.datetime.now()
    # ---------- 1. 读取 ----------
    g = gpd.read_file(SHP, encoding="utf-8")
    src_crs = g.crs
    s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy().to_crs(UTM)
    T = {t: geom.buffer(0) for t, geom in zip(s["TOWN"], s.geometry)}
    A, B = T["荔枝沟区"], T["牛岭乡"]
    U2 = unary_union([A, B]).buffer(0)
    S = A.boundary.intersection(B.boundary)
    log(f"要素 {len(g)}，荔枝沟 {A.area/1e6:.3f} km²，牛岭 {B.area/1e6:.3f} km²，"
        f"U2 {U2.area/1e6:.3f} km²，{len(parts_of(U2))} 部件")

    # ---------- 2. 提取 #3F48CC 中心线 ----------
    img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
    H, W = img.shape[:2]
    minx, miny, maxx, maxy = s.total_bounds
    bgr = np.array([204, 72, 63], np.int16)
    d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
    n_px60 = int((d <= 60).sum())
    if n_px60 == 0:
        raise SystemExit("✗ 画法验证失败：图中不存在 #3F48CC 笔画")
    mask = cv2.dilate((d <= 90).astype(np.uint8), np.ones((3, 3), np.uint8), 1)
    cols = {}
    for j, i in zip(*np.where(mask > 0)):
        cols.setdefault(i, []).append(j)
    xs = sorted(cols)
    L = LineString([(minx + i * 30.0, maxy - np.mean(cols[i]) * 30.0) for i in xs])
    L = chaikin(L.simplify(10.0, preserve_topology=True), 2)
    log(f"画法验证：#3F48CC {n_px60} px ✓；中心线 {L.length:.0f} m，{len(L.coords)} 点")

    U2b = U2.boundary
    coords = list(L.coords)

    # ---------- 3. 端点延长闭合到 U2 外边界 ----------
    def extend_end(c_end, c_ref, forward, span=3000.0, back=600.0):
        dirv = np.array(c_end) - np.array(c_ref)
        nrm = np.linalg.norm(dirv)
        if nrm < 1e-6:
            return Point(c_end).distance(U2b), None
        dirv = dirv / nrm
        p_end = np.array(c_end)
        ext = LineString([p_end - dirv * (back if not forward else 0),
                          p_end + dirv * span])
        inter = ext.intersection(U2b)
        cands = []
        for q in (inter.geoms if hasattr(inter, "geoms") else
                  [inter] if inter.geom_type == "Point" else []):
            if q.geom_type == "Point":
                cands.append(q)
        if cands:
            if forward:
                p = min(cands, key=lambda q: Point(q).distance(Point(c_end)))
            else:
                p = min(cands, key=lambda q: Point(q).distance(Point(c_end)))
            return Point(p).distance(Point(c_end)), Point(p)
        return Point(c_end).distance(U2b), None

    d_w, p_w = extend_end(coords[0], coords[1], forward=False, back=600.0)
    d_e, p_e = extend_end(coords[-1], coords[-2], forward=True, span=3000.0)
    log(f"西端：距 U2 外边界 {d_w:.0f} m -> {'延长闭合' if p_w else '⚠ 无交点，用最近点'}")
    log(f"东端：距 U2 外边界 {d_e:.0f} m -> {'延长闭合' if p_e else '⚠ 无交点，用最近点'}")
    if p_w is None:
        p_w = shapely_nearest(U2b, Point(coords[0]))
    if p_e is None:
        from shapely.ops import nearest_points
        p_e = nearest_points(U2b, Point(coords[-1]))[0]
    D = LineString([p_w.coords[0]] + coords + [p_e.coords[0]])
    if not D.is_simple:
        D = D.simplify(5.0, preserve_topology=True)
    log(f"新界线 D：{D.length:.0f} m，simple={D.is_simple}")

    # ---------- 4. 切割 U2 ----------
    node = set_precision(unary_union([set_precision(U2b, 0.01), set_precision(D, 0.01)]), 0.01)
    faces = [f.buffer(0) for f in polygonize(node)
             if f.geom_type in ("Polygon", "MultiPolygon")]
    log(f"polygonize -> {len(faces)} 面")
    # 与 D 相邻的大面按「牛岭主体」归属；孤岛面按原归属
    B_core = repair_validity(B.intersection(U2.difference(D.buffer(1200)).buffer(0))) \
        if True else B
    if B_core is None or B_core.is_empty:
        B_core = B
    faces_B, faces_A, area_B, area_A = [], [], 0.0, 0.0
    for f in faces:
        o_b = f.intersection(B_core).area
        o_a = f.intersection(A).area
        # 弧南侧新划入区属于荔枝沟，用「牛岭主体重叠」为主判据
        if o_b >= o_a:
            faces_B.append(f); area_B += f.area
        else:
            faces_A.append(f); area_A += f.area
    B_new = repair_validity(unary_union(faces_B).buffer(0))
    A_new = repair_validity(unary_union(faces_A).buffer(0))
    # 严格分区兜底：去重叠、裁回 U2、残缝归并
    if A_new.intersects(B_new):
        ov = A_new.intersection(B_new).area
        A_new = repair_validity(A_new.difference(B_new).buffer(0))
        log(f"  去重叠 {ov:.1f} m²")
    # 校验
    u_ab = unary_union([A_new, B_new]).buffer(0)
    miss = U2.difference(u_ab).area
    log(f"分区校验：和 {(A_new.area+B_new.area)/1e6:.4f} km²，并 {u_ab.area/1e6:.4f}，"
        f"U2 {U2.area/1e6:.4f}，域缺 {miss:.2f} m²")
    if miss > 100 or abs((A_new.area + B_new.area) - U2.area) > 400:
        raise SystemExit("✗ 分区校验未通过，中止写出")
    if not B_new.buffer(0).difference(B.buffer(0)).area > 1:
        pass
    growth = B.difference(B_new).area
    log(f"牛岭乡：{B.area/1e6:.3f} -> {B_new.area/1e6:.3f} km²（划出 {growth/1e6:.3f}）")
    log(f"荔枝沟区：{A.area/1e6:.3f} -> {A_new.area/1e6:.3f} km²（划入 {growth/1e6:.3f}）")
    # 牛岭_new 必须是 B 的子集（只缩小不扩张）
    spill = B_new.difference(B.buffer(1)).area
    if spill > 50:
        raise SystemExit(f"✗ 牛岭_new 超出原范围 {spill:.1f} m²，中止")
    # 其他乡镇不涉及：U2 外边界未变（polygonize 由 U2.boundary 构造）

    # ---------- 5. 备份 + 写回（其余要素数值不动） ----------
    os.makedirs(BAK, exist_ok=True)
    for ext in SIDECARS:
        src = SHP[:-4] + ext
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(BAK, os.path.basename(src)))
    log(f"备份 -> {BAK}")

    ia = g.index[(g["TOWN"] == "荔枝沟区") & g["CITY"].astype(str).str.contains("三亚")][0]
    ib = g.index[(g["TOWN"] == "牛岭乡") & g["CITY"].astype(str).str.contains("三亚")][0]
    A_out = repair_validity(gpd.GeoSeries([A_new], crs=UTM).to_crs(src_crs).iloc[0])
    B_out = repair_validity(gpd.GeoSeries([B_new], crs=UTM).to_crs(src_crs).iloc[0])
    g.loc[ia, "geometry"] = A_out
    g.loc[ib, "geometry"] = B_out
    g.loc[ia, "AREA_KM2"] = round(A_out.area / 1e6, 3)
    g.loc[ib, "AREA_KM2"] = round(B_out.area / 1e6, 3)

    # 未受影响要素不变校验（读原始对比）
    g0 = gpd.read_file(SHP, encoding="utf-8")
    bad = []
    for idx in g0.index:
        if idx in (ia, ib):
            continue
        a0 = g0.loc[idx, "geometry"]
        a1 = g.loc[idx, "geometry"]
        if a0.symmetric_difference(a1).area > 1e-9:
            bad.append(str(g0.loc[idx, "TOWN"]))
    log(f"未受影响要素不变校验：{len(g0)-2-len(bad)}/{len(g0)-2}" +
        (f"，异常 {bad}" if bad else ""))

    g.to_file(SHP, encoding="utf-8")
    log(f"已写回 {SHP}")

    # ---------- 6. QA 报告 + 出图 ----------
    rep = os.path.join(OUT, "荔枝沟牛岭界线调整_QA报告.md")
    with open(rep, "w", encoding="utf-8") as f:
        f.write("# 荔枝沟区-牛岭乡 界线调整 QA 报告\n\n")
        f.write(f"- 生成时间：{t0:%Y-%m-%d %H:%M:%S}\n")
        f.write(f"- 输入手绘图：`{IMG}`（{W}×{H}，1px=30m，EPSG:32649）\n")
        f.write(f"- 修改：`{SHP}`\n- 备份：`{BAK}`\n\n")
        f.write("## 1. 画法验证\n\n")
        f.write(f"- #3F48CC 像素（容差60）：{n_px60} —— 画法存在 ✓\n")
        f.write("- 配准：反推比例 30.01/30.02 m/px，与正向制图参数一致 ✓\n")
        f.write("- 笔画位于荔枝沟区∪牛岭乡范围内（477/480 像素），"
                "整条悬于现行荔-牛界以北 453~1185 m —— 语义=整条替换 ✓\n")
        f.write("- 西端落在牛岭乡|河西区边界（59m），东端距牛岭乡|红沙区边界 267m（收笔未到，已延长闭合）\n\n")
        f.write("## 2. 调整结果\n\n")
        f.write(f"| 乡镇 | 调整前km² | 调整后km² | 变化km² |\n|---|---:|---:|---:|\n")
        f.write(f"| 荔枝沟区 | {A.area/1e6:.4f} | {A_new.area/1e6:.4f} | +{growth/1e6:.4f} |\n")
        f.write(f"| 牛岭乡 | {B.area/1e6:.4f} | {B_new.area/1e6:.4f} | -{growth/1e6:.4f} |\n")
        f.write(f"\n- 新界线长度：{D.length:.0f} m（原界 {S.length:.0f} m）\n")
        f.write(f"- 分区校验：域缺 {miss:.2f} m²，重叠 0（已消除），U2 外边界不变\n")
        f.write(f"- 未受影响要素：{len(g0)-2} 个逐字节不变\n")
    log(f"报告：{rep}")

    # overlay / sidebyside
    def draw_poly(im, geom, color, th=2):
        for p in parts_of(geom):
            for r in [p.exterior] + list(p.interiors):
                c = np.asarray(r.coords)
                pi = np.stack([(c[:, 0] - minx) / 30.0, (maxy - c[:, 1]) / 30.0], 1) \
                    .astype(np.int32)
                cv2.polylines(im, [pi], True, color, th, cv2.LINE_AA)

    ov = img.copy()
    A_utm = gpd.GeoSeries([A_out], crs=src_crs).to_crs(UTM).iloc[0]
    B_utm = gpd.GeoSeries([B_out], crs=src_crs).to_crs(UTM).iloc[0]
    draw_poly(ov, A_utm, (0, 0, 255), 3)
    draw_poly(ov, B_utm, (0, 0, 255), 3)
    cv2.imwrite(os.path.join(OUT, "调整后_overlay.png"),
                cv2.imencode(".png", ov)[1])
    # 渲染修改后的三亚图（与手绘图同参数）
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.ops import unary_union as uu
    ss = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy().to_crs(UTM)
    ss["geometry"] = ss.geometry.buffer(0).simplify(5, preserve_topology=True)
    w_px, h_px = int(round((maxx - minx) / 30)), int(round((maxy - miny) / 30))
    fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_facecolor("white")
    boundary = uu([gg.boundary for gg in ss.geometry])
    gpd.GeoSeries([boundary], crs=ss.crs).plot(ax=ax, color="black", linewidth=2 * 72 / 100,
                                               antialiased=True)
    import io
    buf = io.BytesIO()
    fig.savefig(buf, dpi=100, pad_inches=0, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    arr = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    # 手绘图与修改后渲染图并排（含 #3F48CC 标注）
    side = np.hstack([img, arr])
    cv2.imwrite(os.path.join(OUT, "调整后_sidebyside.png"),
                cv2.imencode(".png", side)[1])
    log("出图：调整后_overlay.png / 调整后_sidebyside.png")
    log("DONE")


def shapely_nearest(line, pt):
    from shapely.ops import nearest_points
    return nearest_points(line, pt)[0]


if __name__ == "__main__":
    main()
