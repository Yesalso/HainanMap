# -*- coding: utf-8 -*-
"""填充显示 灵山镇/东营镇/演丰镇/桂林洋镇 多边形，查找残留。"""
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
OUT = os.path.join(TOWN, "Haikou2002Color", "zoom_junction.png")
TARGET_CRS = "EPSG:32649"
Wr, Hr, OX, OY = 2036, 2097, 0, 310

g = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
g["CODE"] = g["CODE"].astype(str)
hk = g[g["CODE"].str.startswith("4601") | (g["CITY"].astype(str) == "海口市")].copy().reset_index(drop=True)
minx, miny, maxx, maxy = unary_union([x.buffer(0) for x in hk.geometry]).bounds
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr
img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002.png")).convert("RGB")).astype(np.int32)
dark = (img.sum(2) < 3 * 128)[OY:OY + Hr, OX:OX + Wr]

x0, y0, x1, y1 = 438500, 2215500, 444500, 2220500
c0 = int((x0 - minx) / sx); c1 = int((x1 - minx) / sx)
r0 = int((maxy - y1) / sy); r1 = int((maxy - y0) / sy)
c0, r0 = max(0, c0), max(0, r0); c1, r1 = min(Wr, c1), min(Hr, r1)
ext = (minx + c0 * sx, minx + c1 * sx, maxy - r1 * sy, maxy - r0 * sy)

fig, ax = plt.subplots(figsize=(15, 12), dpi=140)
bl = np.ones((r1 - r0, c1 - c0, 3), np.uint8) * 255
bl[dark[r0:r1, c0:c1]] = [200, 200, 200]
ax.imshow(bl, extent=ext, origin="upper", zorder=0)
colors = {"东营镇": "#e6194b", "灵山镇": "#4363d8", "演丰镇": "#f58231",
          "桂林洋镇": "#3cb44b", "三江镇": "#911eb4", "海秀镇": "#42d4f4",
          "府城镇": "#f032e6", "城西镇": "#9a6324"}
for i in hk.index:
    nm = hk.TOWN[i]
    if nm not in colors:
        continue
    for p in (hk.geometry[i].geoms if hk.geometry[i].geom_type == "MultiPolygon" else [hk.geometry[i]]):
        xs, ys = p.exterior.xy
        ax.fill(xs, ys, color=colors[nm], alpha=0.55, zorder=1)
        ax.plot(xs, ys, color="black", lw=1.0, zorder=3)
    c = hk.geometry[i].representative_point()
    if x0 < c.x < x1 and y0 < c.y < y1:
        ax.annotate(nm, (c.x, c.y), fontsize=12, zorder=6,
                    bbox=dict(fc="white", ec="none", alpha=0.8))
ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
ax.set_title("填充：东营镇(红)/灵山镇(蓝)/演丰镇(橙)/桂林洋镇(绿)", fontsize=13)
plt.tight_layout(); plt.savefig(OUT); plt.close()
print("已输出", OUT)
