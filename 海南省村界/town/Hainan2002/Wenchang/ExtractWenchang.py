# -*- coding: utf-8 -*-
"""从 Hainan_town_before_smooth.shp 中提取文昌市数据，输出到本目录。"""
import os

import geopandas as gpd

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "..", "Unless", "Hainan_town_before_smooth.shp")
OUT = os.path.join(BASE, "Hainan_town_wenchang.shp")

CITY = "文昌市"


def main():
    g = gpd.read_file(SRC, encoding="utf-8")
    wc = g[g["CITY"] == CITY].copy()
    print("total:", len(g), "wenchang:", len(wc))
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