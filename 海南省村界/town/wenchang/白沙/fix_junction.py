# -*- coding: utf-8 -*-
"""
fix_junction.py —— 修复白沙2002_fixed 中打安镇在 (109.382618, 19.234186)
处的南部尖角凸起，并把该区域就近划入牙叉镇。

严格分区保证：仅在同一 "打安镇 <-> 牙叉镇" 面板上重划，白误差为零；
其它乡镇逐字节不动。输出 EPSG:32649 与源 CRS(CGCS2000_Albers) 两套。
"""
from __future__ import annotations

import datetime
import os

import geopandas as gpd
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely import make_valid

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "白沙2002_fixed")
ALBERS = os.path.join(HERE, "白沙2002_fixed_Albers.shp")
GEO = (109.382618, 19.234186)   # 上报坐标（WGS84 经纬度）
CUT_H = 18.0                    # 切线与 pt 的纵向偏移（m），切线上方保留打安镇本体
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def main():
    g = gpd.read_file(BASE + ".shp", encoding="utf-8")
    pt = gpd.GeoSeries([Point(GEO)], crs="EPSG:4326").to_crs(g.crs).iloc[0]
    c = g.set_index("CODE")
    daan = c.loc["469025103", "geometry"]   # 打安镇（沿用）
    yacha = c.loc["469025100", "geometry"]  # 牙叉镇（沿用）
    bai = c.loc["469025208", "geometry"]    # 白沙镇（新增，不动）

    ycut = pt.y + CUT_H
    box = Polygon([(pt.x - 500, ycut), (pt.x + 500, ycut),
                   (pt.x + 500, pt.y - 500), (pt.x - 500, pt.y - 500)])
    piece = make_valid(daan.intersection(box)).buffer(0)
    daan_new = make_valid(daan.difference(piece)).buffer(0)
    yacha_new = make_valid(yacha.union(piece)).buffer(0)

    # ---- 校验 ----
    assert abs(daan_new.area + piece.area - daan.area) < 1e-4, "打安镇面积不守恒"
    assert abs(yacha_new.area - (yacha.area + piece.area)) < 1e-4, "牙叉镇面积不守恒"
    ov = daan_new.intersection(yacha_new).area
    assert ov < 1e-3, f"打安/牙叉重叠 {ov} m²"
    assert abs(daan_new.intersection(bai).area) + abs(yacha_new.intersection(bai).area) < 1e-3
    print(f"  切点 y={ycut:.2f}，凸起面积 {(piece.area/1e4):.4f} ha ({(piece.area/1e6)*1e3:.3f} km²)")

    # 整体校验：并集不变（严格分区）——修改前全量几何
    before_all = unary_union(list(g.geometry)).buffer(0).area
    g.loc[g.CODE == "469025103", "geometry"] = daan_new
    g.loc[g.CODE == "469025100", "geometry"] = yacha_new
    g["AREA_KM2"] = (g.geometry.area / 1e6).round(3)
    assert not g.geometry.is_valid.eq(False).any()

    after_all = unary_union(list(g.geometry)).buffer(0).area
    assert abs(before_all - after_all) < 1e-3, f"分区并集改变 {after_all-before_all:.4f} m²"
    tot = g.geometry.area.sum() / 1e6

    for ext in SIDECARS:
        p = BASE + ext
        if os.path.exists(p):
            os.remove(p)
    g.to_file(BASE + ".shp", encoding="utf-8")

    src = gpd.read_file(ALBERS, encoding="utf-8")
    g.to_crs(src.crs).to_file(BASE + "_Albers.shp", encoding="utf-8")

    d = g.set_index("CODE")
    print(f"  打安镇 {d.loc['469025103','AREA_KM2']} km² / 牙叉镇 {d.loc['469025100','AREA_KM2']} km²")
    print(f"  输出 OK，总 {tot:.6f} km²")

    with open(BASE + "_QA报告.md", "a", encoding="utf-8") as f:
        f.write(f"\n## 5. 打安镇尖角修复（109.382618, 19.234186）\n\n"
                f"- 时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"- 处置：打安镇在坐标处的南部尖角凸起沿 y=pt.y+{CUT_H:.0f}m 切平，"
                f"切下面积 {piece.area/1e4:.4f} ha，就近划入牙叉镇。\n"
                f"- 校验：面积守恒、无重叠、分区并集不变，其余乡镇未改动。\n"
                f"- 修复后：打安镇 {d.loc['469025103','AREA_KM2']} km²，"
                f"牙叉镇 {d.loc['469025100','AREA_KM2']} km²，全县 {tot:.6f} km²（不变）。\n")
    print("  QA 报告已更新")


if __name__ == "__main__":
    main()