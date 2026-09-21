# -*- coding: utf-8 -*-
"""预览：新海口乡镇 vs 2002 色块图。"""
import os
import numpy as np
import geopandas as gpd
from PIL import Image
from shapely.ops import unary_union
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
OUT = os.path.join(TOWN, "Haikou2002Color", "preview_new.png")
TARGET_CRS = "EPSG:32649"
Wr, Hr, OX, OY = 2036, 2097, 0, 310

COLORS = {
    "新海乡": "#00A2E8", "薛样乡": "#7F7F7F", "美安镇": "#880015",
    "东营镇": "#3F48CC", "桂林洋镇": "#B5E61D", "演海镇": "#22B14C",
    "美仁坡乡": "#C3C3C3", "新民乡": "#73FBFD", "谭文镇": "#FFAEC9",
}

gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
gdf["CODE"] = gdf["CODE"].astype(str)
is_hk = gdf["CODE"].str.startswith("4601") | (gdf["CITY"].astype(str) == "海口市")
hk = gdf[is_hk].copy().reset_index(drop=True)
minx, miny, maxx, maxy = hk.total_bounds

aimg = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB"))
crop = aimg[OY:OY + Hr, OX:OX + Wr]
ext = (minx, maxx, miny, maxy)

fig, ax = plt.subplots(figsize=(14, 15), dpi=110)
ax.imshow(crop, extent=ext, origin="upper", alpha=0.45, zorder=0)
for _, r in hk.iterrows():
    gs = r.geometry.geoms if r.geometry.geom_type == "MultiPolygon" else [r.geometry]
    col = COLORS.get(r["TOWN"])
    for p in gs:
        x, y = p.exterior.xy
        if col:
            ax.fill(x, y, color=col, alpha=0.55, zorder=2)
            ax.plot(x, y, color="black", lw=1.1, zorder=3)
        else:
            ax.plot(x, y, color="#888888", lw=0.5, zorder=1)
    c = r.geometry.representative_point()
    ax.annotate(r["TOWN"], (c.x, c.y), fontsize=8, ha="center", zorder=4,
                color="black", bbox=dict(fc="white", ec="none", alpha=0.6, pad=0.5))
ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
ax.set_aspect("equal"); ax.axis("off")
ax.set_title("海口乡镇（实色=新增/重画，灰线=其余）叠加 2002 色块图", fontsize=13)
plt.tight_layout()
plt.savefig(OUT)
print("已输出", OUT)
