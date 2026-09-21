# -*- coding: utf-8 -*-
"""
把 海口2002_select.shp（41 个海口乡镇）整合进
town/Hainan2002/Hainan_town.shp（全省 231 个乡镇）。

- 保留 Hainan2002 中非海口（CODE 不以 4601 开头）的单元；
- 用海口 41 个单元替换原海口部分；
- 原文件先备份为 Hainan_town_base.shp。
"""
import os
import shutil
import sys

import geopandas as gpd
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOWN_DIR = os.path.dirname(os.path.abspath(__file__))
HN_DIR = os.path.join(TOWN_DIR, "Hainan2002")
HN_SHP = os.path.join(HN_DIR, "Hainan_town.shp")
HK_SHP = os.path.join(TOWN_DIR, "海口2002_select.shp")
BAK_SHP = os.path.join(HN_DIR, "Hainan_town_base.shp")

COLS = ["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]


def backup_once():
    if not os.path.exists(BAK_SHP):
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            src = HN_SHP[:-4] + ext
            if os.path.exists(src):
                shutil.copy2(src, BAK_SHP[:-4] + ext)
        print(f"已备份原文件 → {BAK_SHP}")
    else:
        print(f"备份已存在：{BAK_SHP}")


def main():
    hn = gpd.read_file(HN_SHP, encoding="utf-8")
    hk = gpd.read_file(HK_SHP, encoding="utf-8")
    print(f"Hainan2002：{len(hn)} 个单元；海口替换层：{len(hk)} 个单元")

    assert hk.crs == hn.crs, "两图层 CRS 不一致"
    hk_codes = set(hk.CODE.astype(str))
    hn_hk = set(hn.CODE.astype(str).str.startswith("4601"))
    assert len(hk_codes) == 41 and all(c.startswith("4601") for c in hk_codes), \
        "海口层应恰好为 41 个 4601 单元"

    backup_once()

    keep = hn[~hn.CODE.astype(str).str.startswith("4601")].copy()
    print(f"保留非海口单元：{len(keep)}")

    merged = gpd.GeoDataFrame(
        pd.concat([keep[COLS], hk[COLS]], ignore_index=True),
        geometry="geometry", crs=hn.crs)
    merged["AREA_KM2"] = (merged.geometry.area / 1e6).round(3)
    merged = merged.sort_values("CODE").reset_index(drop=True)
    print(f"整合后单元数：{len(merged)}（海口 {len(hk)} + 其他 {len(keep)}）")
    print(f"总面积：{merged.geometry.area.sum()/1e6:.1f} km²")

    bad = int((~merged.geometry.is_valid).sum())
    print(f"有效几何：{len(merged) - bad}/{len(merged)}")

    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = HN_SHP[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    merged.to_file(HN_SHP, encoding="utf-8")
    print(f"已输出：{HN_SHP}")


if __name__ == "__main__":
    main()