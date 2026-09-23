# -*- coding: utf-8 -*-
"""fix_neck.py — 删除打安镇(469025103)与白沙镇(469025208)之间、位于
(109.382962, 19.234300) 的牙叉镇(469025100)窄条，按就近把北半并入打安镇、
南半并入白沙镇，使两镇直接相接。其余乡镇几何逐字节不动。

做法：
- 窄条西端 = 打安/牙叉/白沙三镇交汇点 T（打安-牙叉与白沙-牙叉公共边在牙叉环上的交点）
- 窄条东端 = 标记点经线 x=x_mark 处的竖直切口（将窄条裁剪到 x<=x_mark）
- 沿“到打安/白沙边界等距”的脊线把窄条一分为二：北半->打安，南半->白沙
"""
from __future__ import annotations

import datetime
import os

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree
from shapely import make_valid
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import snap, split, unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "白沙2002_fixed")
ALBERS = os.path.join(HERE, "白沙2002_fixed_Albers.shp")
GEO = (109.382962, 19.234300)
DAAN, YACHA, BAISHA = "469025103", "469025100", "469025208"
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def densify(geom, step=0.5):
    out = []
    for p in ([geom] if geom.geom_type == "Polygon" else list(geom.geoms)):
        for cc in [p.exterior] + list(p.interiors):
            a = np.array(cc.coords)
            for i in range(len(a) - 1):
                seg = a[i + 1] - a[i]
                out.append(a[i])
                k = max(1, int(np.hypot(*seg) / step))
                for t in np.linspace(0, 1, k, endpoint=False):
                    out.append(a[i] + t * seg)
    return np.array(out)


def build_neck(A, Y, xcap):
    ay = A.intersection(Y)
    pts = np.array([p for ls in
                    (ay.geoms if "Multi" in ay.geom_type or ay.geom_type == "GeometryCollection" else [ay])
                    if ls.geom_type == "LineString" for p in ls.coords])
    w = (pts[:, 0] > xcap - 400) & (pts[:, 0] < xcap + 300)
    T = pts[w][np.argmin(pts[w][:, 0])]
    poly = max(Y.geoms, key=lambda p: p.area) if Y.geom_type.startswith("Multi") else Y
    ring = np.array(poly.exterior.coords)
    iT = int(np.argmin(np.hypot(ring[:, 0] - T[0], ring[:, 1] - T[1])))
    n = len(ring) - 1

    def walk(step):
        out, i = [tuple(ring[iT])], iT
        for _ in range(n):
            i = (i + step) % n
            out.append(tuple(ring[i]))
            if ring[i][0] >= xcap:
                break
        return out

    def cap_pt(arc):
        p0, p1 = np.array(arc[-2]), np.array(arc[-1])
        t = (xcap - p0[0]) / (p1[0] - p0[0]) if p1[0] != p0[0] else 0.0
        return tuple(p0 + t * (p1 - p0))

    a1, a2 = walk(1), walk(-1)
    neck = Polygon(a1 + [cap_pt(a1), cap_pt(a2)] + list(reversed(a2))).buffer(0)
    north = np.array([p for p in a1 if p[0] < xcap] + [cap_pt(a1)])
    south = np.array([p for p in a2 if p[0] < xcap] + [cap_pt(a2)])
    return neck, north, south


def split_neck(neck, north, south, A, B, xcap):
    tA, tB = cKDTree(densify(A)), cKDTree(densify(B))
    xs = np.linspace(north[0, 0], xcap, 160)
    rp = []
    for x in xs:
        yN = np.interp(x, north[:, 0], north[:, 1])
        yS = np.interp(x, south[:, 0], south[:, 1])
        lo, hi = yS, yN
        for _ in range(40):
            mid = (lo + hi) / 2
            if tA.query([x, mid])[0] - tB.query([x, mid])[0] > 0:
                lo = mid
            else:
                hi = mid
        rp.append((x, (lo + hi) / 2))
    r = np.array(LineString(rp).simplify(0.5).coords)
    ridge = LineString(np.vstack([r[0] - (r[1] - r[0]) * 50, r, r[-1] + (r[-1] - r[-2]) * 50]))
    parts = [p for p in split(neck, ridge).geoms if p.area > 1e-6]
    if len(parts) != 2:
        raise RuntimeError("窄条未被脊线切成两块")
    na = max(parts, key=lambda p: (lambda q: tB.query([q.x, q.y])[0])(p.representative_point()))
    nb = neck.difference(na).buffer(0)
    na = snap(na, A, 1.0)
    nb = snap(nb, B, 1.0)
    return na, nb


def main():
    g = gpd.read_file(BASE + ".shp", encoding="utf-8")
    c = g.set_index("CODE")
    A, B, Y = c.loc[DAAN, "geometry"], c.loc[BAISHA, "geometry"], c.loc[YACHA, "geometry"]
    mark = gpd.GeoSeries([Point(GEO)], crs="EPSG:4326").to_crs(g.crs).iloc[0]

    neck, north, south = build_neck(A, Y, mark.x)
    assert 100 < neck.area < 1e5, "未找到窄条（是否已修复？）"
    assert neck.difference(Y).area < 1e-6, "窄条不在牙叉镇内"
    na, nb = split_neck(neck, north, south, A, B, mark.x)

    A2 = make_valid(A.union(na)).buffer(0)
    B2 = make_valid(B.union(nb)).buffer(0)
    Y2 = make_valid(Y.difference(neck)).buffer(0)

    assert A2.is_valid and B2.is_valid and Y2.is_valid
    assert abs(A2.area + B2.area + Y2.area - (A.area + B.area + Y.area)) < 1e-3, "面积不守恒"
    assert A2.intersection(B2).area < 1e-3, "打安/白沙重叠"
    assert A2.intersection(Y2).area < 1e-3 and B2.intersection(Y2).area < 1e-3, "与牙叉重叠"
    sym = unary_union([A, B, Y]).symmetric_difference(unary_union([A2, B2, Y2])).area
    assert sym < 1e-3, f"三镇并集改变 {sym} m2"
    assert neck.difference(A2).difference(B2).difference(Y2).area < 1e-3, "窄条处存在缝隙"

    before = unary_union(list(g.geometry)).buffer(0).area
    g.loc[g.CODE == DAAN, "geometry"] = A2
    g.loc[g.CODE == BAISHA, "geometry"] = B2
    g.loc[g.CODE == YACHA, "geometry"] = Y2
    g["AREA_KM2"] = (g.geometry.area / 1e6).round(3)
    after = unary_union(list(g.geometry)).buffer(0).area
    assert abs(before - after) < 1e-3, f"分区并集改变 {after - before} m2"
    assert not g.geometry.is_valid.eq(False).any()

    print("标记点 (%.3f, %.3f)" % (mark.x, mark.y))
    print("窄条 %.1f m2 -> 打安 %.1f m2 / 白沙 %.1f m2" % (neck.area, na.area, nb.area))

    for ext in SIDECARS:
        p = BASE + ext
        if os.path.exists(p):
            os.remove(p)
    g.to_file(BASE + ".shp", encoding="utf-8")

    src = gpd.read_file(ALBERS, encoding="utf-8")
    g.to_crs(src.crs).to_file(BASE + "_Albers.shp", encoding="utf-8")

    d = g.set_index("CODE")
    tot = g.geometry.area.sum() / 1e6
    print("打安镇 %.3f / 白沙镇 %.3f / 牙叉镇 %.3f km2，总 %.6f km2"
          % (d.loc[DAAN, "AREA_KM2"], d.loc[BAISHA, "AREA_KM2"], d.loc[YACHA, "AREA_KM2"], tot))

    with open(BASE + "_QA报告.md", "a", encoding="utf-8") as f:
        f.write(f"\n## 6. 打安/白沙之间牙叉窄条清理（109.382962, 19.234300）\n\n"
                f"- 时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"- 处置：删除打安镇与白沙镇之间、标记点经线以西的牙叉镇窄条 "
                f"{neck.area / 1e4:.4f} ha；按到边界最近，北半 {na.area / 1e4:.4f} ha 并入打安镇，"
                f"南半 {nb.area / 1e4:.4f} ha 并入白沙镇，两镇直接相接。\n"
                f"- 校验：面积守恒、无重叠、三镇并集不变，其余乡镇未改动。\n"
                f"- 修复后：打安镇 {d.loc[DAAN, 'AREA_KM2']} km²，白沙镇 {d.loc[BAISHA, 'AREA_KM2']} km²，"
                f"牙叉镇 {d.loc[YACHA, 'AREA_KM2']} km²，全县 {tot:.6f} km²（不变）。\n")
    print("QA 报告已更新")


if __name__ == "__main__":
    main()
