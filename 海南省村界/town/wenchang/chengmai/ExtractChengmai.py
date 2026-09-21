# -*- coding: utf-8 -*-
"""从 Hainan2002/Current/Hainan_town_before_wenchang.shp 中提取澄迈县数据，输出到本目录。
沿用 Hainan2002/Wenchang/ExtractWenchang.py 的代码结构。"""
import os

import geopandas as gpd

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "..", "..", "Hainan2002", "Current", "Hainan_town_before_wenchang.shp")
OUT = os.path.join(BASE, "Hainan_town_chengmai.shp")

CITY = "澄迈县"


def main():
    g = gpd.read_file(SRC, encoding="utf-8")
    wc = g[g["CITY"] == CITY].copy()
    print("total:", len(g), "chengmai:", len(wc))
    print(wc[["CODE", "TOWN", "CITY", "AREA_KM2"]])

    os.makedirs(BASE, exist_ok=True)
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = OUT[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    wc.to_file(OUT, encoding="utf-8")
    print("saved:", OUT)


if __name__ == "__main__":
    main()