# -*- coding: utf-8 -*-
"""平滑新海乡/长流镇交界处的异常凸起。

异常：长流镇(460105100)在给定点处向北突出一段约 340m 的细长尖刺，
夹在新海乡(HK01)与海秀街道(460105002)之间。

处理：删除尖刺，用一条平滑曲线代替；空出的尖刺区域按就近原则划给
两侧的新海乡/海秀街道（分界点取尖刺底部中点 M）。
"""
import os
import shutil

import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon

BASE = os.path.dirname(os.path.abspath(__file__))
SHP = os.path.join(BASE, "Hainan_town.shp")
BAK = os.path.join(BASE, "Hainan_town_before_smooth.shp")

CHANGLIU = "460105100"
HAIXIU = "460105002"
XINHAI = "HK01"

# 尖刺顶点
J = (557999.513, 2081910.562)          # 底部西端（给定点处）
N = (557992.618, 2082247.697)          # 尖端
S = (558012.567, 2081881.186)          # 底部东端
M = ((J[0] + S[0]) / 2.0, (J[1] + S[1]) / 2.0)   # 底部中点（新三叉点）
# 平滑曲线端点外的辅助点
P_N0 = (557996.450, 2082333.216)       # N 以北相邻点
P_S1 = (558014.976, 2081842.793)       # S 以南相邻点


def catmull_rom(p0, p1, p2, p3, n):
    """均匀 Catmull-Rom 曲线在 p1->p2 段上的采样(含端点)。"""
    out = []
    for i in range(n + 1):
        t = i / n
        t2, t3 = t * t, t * t * t
        x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                   + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                   + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
        y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                   + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                   + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
        out.append((round(x, 3), round(y, 3)))
    return out


def ring_pts(poly):
    return [(round(x, 3), round(y, 3)) for x, y in poly.exterior.coords]


def replace_between(pts, a, b, new):
    """把环 pts 中 a->b(正向) 之间(含 a,b)的子段替换为 new。"""
    ia = pts.index(a)
    ib = pts.index(b)
    assert ia < ib, (a, b, ia, ib)
    return pts[:ia] + new + pts[ib + 1:]


def main():
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        s = SHP[:-4] + ext
        if os.path.exists(s) and not os.path.exists(BAK[:-4] + ext):
            shutil.copyfile(s, BAK[:-4] + ext)
    print("backup:", BAK)

    g = gpd.read_file(SHP, encoding="utf-8")

    curve_NM = catmull_rom(P_N0, N, M, S, 12)          # N -> M 平滑曲线
    curve_MN = list(reversed(curve_NM))                # M -> N

    # 长流镇：删除尖刺，底边 J->M->S
    i = g.index[g["CODE"] == CHANGLIU][0]
    pts = ring_pts(g.at[i, "geometry"])
    pts = replace_between(pts, J, S, [J, M, S])
    g.at[i, "geometry"] = Polygon(pts)

    # 新海乡：尖刺西侧 -> 平滑曲线 N->M，再经 M->J 接回原有横界
    i = g.index[g["CODE"] == XINHAI][0]
    pts = ring_pts(g.at[i, "geometry"])
    pts = replace_between(pts, N, J, curve_NM + [J])
    g.at[i, "geometry"] = Polygon(pts)

    # 海秀街道：S->M->N
    i = g.index[g["CODE"] == HAIXIU][0]
    pts = ring_pts(g.at[i, "geometry"])
    pts = replace_between(pts, S, N, [S] + curve_MN)
    g.at[i, "geometry"] = Polygon(pts)

    # 校验
    cl = g[g["CODE"] == CHANGLIU].geometry.iloc[0]
    xh = g[g["CODE"] == XINHAI].geometry.iloc[0]
    hx = g[g["CODE"] == HAIXIU].geometry.iloc[0]
    print("valid:", cl.is_valid, xh.is_valid, hx.is_valid)
    print("overlap km2:", round(cl.intersection(xh).area / 1e6, 8),
          round(cl.intersection(hx).area / 1e6, 8),
          round(xh.intersection(hx).area / 1e6, 8))
    print("area km2:", round(cl.area / 1e6, 4), round(xh.area / 1e6, 4),
          round(hx.area / 1e6, 4))

    g.to_file(SHP, encoding="utf-8")
    print("saved:", SHP)


if __name__ == "__main__":
    main()
