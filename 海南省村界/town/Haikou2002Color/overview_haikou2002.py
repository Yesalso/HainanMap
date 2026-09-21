# -*- coding: utf-8 -*-
"""海口市乡镇界概览图（基于更新后的 Hainan_town.shp）。"""
import os
import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
OUT = os.path.join(TOWN, "Haikou2002Color", "海口概览图.png")
TARGET_CRS = "EPSG:32649"

NEW = {"新海乡", "薛样乡", "美安镇", "东营镇", "桂林洋镇",
       "演海镇", "美仁坡乡", "新民乡", "谭文镇"}

gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
gdf["CODE"] = gdf["CODE"].astype(str)
is_hk = gdf["CODE"].str.startswith("4601") | (gdf["CITY"].astype(str) == "海口市")
hk = gdf[is_hk].copy().reset_index(drop=True)
print(f"海口单元 {len(hk)}")

minx, miny, maxx, maxy = hk.total_bounds
fig, ax = plt.subplots(figsize=(14, 15), dpi=140)

cmap = plt.get_cmap("tab20")
ci = 0
for _, r in hk.iterrows():
    gs = r.geometry.geoms if r.geometry.geom_type == "MultiPolygon" else [r.geometry]
    if r["TOWN"] in NEW:
        color = cmap(ci % 20); ci += 1
    else:
        color = "#f2f2f2"
    gpd.GeoSeries(gs, crs=hk.crs).plot(ax=ax, facecolor=color,
                                       edgecolor="black", linewidth=0.4, alpha=0.9)
    c = r.geometry.representative_point()
    if minx < c.x < maxx and miny < c.y < maxy:
        ax.annotate(r["TOWN"], (c.x, c.y), fontsize=8, ha="center", va="center",
                    color="black", zorder=5,
                    bbox=dict(fc="white", ec="none", alpha=0.65, pad=0.6))

ax.set_aspect("equal")
ax.axis("off")
ax.set_title("海口市乡镇界概览图", fontsize=18, pad=12)

# 比例尺 10 km
L = 10000.0
x0 = minx + (maxx - minx) * 0.05
y0 = miny + (maxy - miny) * 0.03
ax.plot([x0, x0 + L], [y0, y0], color="black", lw=3, zorder=6)
ax.text(x0 + L / 2, y0 + (maxy - miny) * 0.012, "10 km", ha="center",
        va="bottom", fontsize=11, zorder=6)

# 指北针
ax.annotate("N", xy=(maxx - (maxx - minx) * 0.06, maxy - (maxy - miny) * 0.05),
            xytext=(maxx - (maxx - minx) * 0.06, maxy - (maxy - miny) * 0.14),
            ha="center", va="center", fontsize=16,
            arrowprops=dict(facecolor="black", width=2.5, headwidth=10))

fig.tight_layout()
fig.savefig(OUT)
plt.close(fig)
print("已输出", OUT)
