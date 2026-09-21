# -*- coding: utf-8 -*-
"""同步与导出：
  1. 从 Hainan2002/Hainan_town.shp 提取海口 50 单元 -> town/海口2002.shp
  2. 导出海口乡镇清单 CSV -> Haikou2002Color/海口乡镇清单.csv
  3. 输出最终成果图 -> Haikou2002Color/海口2002成果图.png
"""
import os
import shutil
import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
WORK = os.path.join(TOWN, "Haikou2002Color")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
HK_ONLY = os.path.join(TOWN, "海口2002.shp")
CSV_OUT = os.path.join(WORK, "海口乡镇清单.csv")
PNG_OUT = os.path.join(WORK, "海口2002成果图.png")
TARGET_CRS = "EPSG:32649"
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")

NEW = ["新海乡", "薛样乡", "美安镇", "东营镇", "桂林洋镇",
       "演海镇", "美仁坡乡", "新民乡", "谭文镇"]
NEWCOL = {
    "新海乡": "#00A2E8", "薛样乡": "#7F7F7F", "美安镇": "#880015",
    "东营镇": "#3F48CC", "桂林洋镇": "#B5E61D", "演海镇": "#22B14C",
    "美仁坡乡": "#C3C3C3", "新民乡": "#73FBFD", "谭文镇": "#FFAEC9",
}


def main():
    src = gpd.read_file(HT, encoding="utf-8")
    src["CODE"] = src["CODE"].astype(str)
    is_hk = src["CODE"].str.startswith("4601") | (src["CITY"].astype(str) == "海口市")
    hk = src[is_hk].copy().reset_index(drop=True)
    hk = hk[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
    print(f"海口单元 {len(hk)}")

    # 1) 同步：海口单列图层（先写临时再替换，避免文件占用）
    tmp = HK_ONLY[:-4] + "__tmp.shp"
    for e in SIDECARS:
        p = tmp[:-4] + e
        if os.path.exists(p):
            os.remove(p)
    hk.to_file(tmp, encoding="utf-8")
    for e in SIDECARS:
        s = tmp[:-4] + e
        if os.path.exists(s):
            os.replace(s, HK_ONLY[:-4] + e)
    print("已同步:", HK_ONLY)

    # 2) 清单 CSV
    df = hk.drop(columns="geometry").copy()
    df["新增"] = df["TOWN"].isin(NEW)
    df.to_csv(CSV_OUT, index=False, encoding="utf-8-sig")
    print("已导出:", CSV_OUT)

    # 3) 成果图
    hkt = hk.to_crs(TARGET_CRS)
    minx, miny, maxx, maxy = unary_union([x.buffer(0) for x in hkt.geometry]).bounds
    fig, ax = plt.subplots(figsize=(14, 15), dpi=140)
    for _, r in hkt.iterrows():
        gs = r.geometry.geoms if r.geometry.geom_type == "MultiPolygon" else [r.geometry]
        col = NEWCOL.get(r["TOWN"], "#f4f4f4")
        gpd.GeoSeries(gs, crs=hkt.crs).plot(
            ax=ax, facecolor=col, edgecolor="black",
            linewidth=1.0 if r["TOWN"] in NEW else 0.35, alpha=0.95)
        c = r.geometry.representative_point()
        ax.annotate(r["TOWN"], (c.x, c.y), fontsize=8, ha="center", va="center",
                    color="black", zorder=6,
                    bbox=dict(fc="white", ec="none", alpha=0.6, pad=0.5))
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("海口市乡镇界成果图（含 2002 年新增 9 乡镇）", fontsize=18, pad=14)
    handles = [Patch(facecolor=NEWCOL[n], edgecolor="black", label=n) for n in NEW]
    ax.legend(handles=handles, loc="lower right", fontsize=10, ncol=3,
              title="新增/重画乡镇", framealpha=0.9)
    # 比例尺
    L = 10000.0
    x0 = minx + (maxx - minx) * 0.05
    y0 = miny + (maxy - miny) * 0.03
    ax.plot([x0, x0 + L], [y0, y0], color="black", lw=3, zorder=6)
    ax.text(x0 + L / 2, y0 + (maxy - miny) * 0.012, "10 km", ha="center",
            va="bottom", fontsize=11, zorder=6)
    ax.annotate("N", xy=(maxx - (maxx - minx) * 0.06, maxy - (maxy - miny) * 0.05),
                xytext=(maxx - (maxx - minx) * 0.06, maxy - (maxy - miny) * 0.14),
                ha="center", va="center", fontsize=16,
                arrowprops=dict(facecolor="black", width=2.5, headwidth=10))
    fig.tight_layout()
    fig.savefig(PNG_OUT)
    plt.close(fig)
    print("已输出:", PNG_OUT)


if __name__ == "__main__":
    main()
