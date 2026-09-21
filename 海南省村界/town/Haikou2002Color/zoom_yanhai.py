# -*- coding: utf-8 -*-
"""放大核对：演海镇（三江/演丰）边界 vs 2002 黑线。"""
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
OUT = os.path.join(TOWN, "Haikou2002Color", "zoom_yanhai.png")
TARGET_CRS = "EPSG:32649"
Wr, Hr, OX, OY = 2036, 2097, 0, 310

g = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
g["CODE"] = g["CODE"].astype(str)
hk = g[g["CODE"].str.startswith("4601") | (g["CITY"].astype(str) == "海口市")].copy().reset_index(drop=True)
minx, miny, maxx, maxy = unary_union([x.buffer(0) for x in hk.geometry]).bounds
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr

img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002.png")).convert("RGB")).astype(np.int32)
crop = img[OY:OY + Hr, OX:OX + Wr]
dark = crop.sum(2) < 3 * 128

aimg = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
cropa = aimg[OY:OY + Hr, OX:OX + Wr]
green = np.abs(cropa - np.array([34, 177, 76])).max(2) <= 20

x0, y0, x1, y1 = 440000, 2165000, 478000, 2210000
c0 = int((x0 - minx) / sx); c1 = int((x1 - minx) / sx)
r0 = int((maxy - y1) / sy); r1 = int((maxy - y0) / sy)
c0, r0 = max(0, c0), max(0, r0); c1, r1 = min(Wr, c1), min(Hr, r1)

fig, ax = plt.subplots(figsize=(13, 13), dpi=115)
ext = (minx + c0 * sx, minx + c1 * sx, maxy - r1 * sy, maxy - r0 * sy)
sub = crop[r0:r1, c0:c1].astype(np.uint8).copy()
sub[green[r0:r1, c0:c1]] = (sub[green[r0:r1, c0:c1]] * 0.4 + np.array([34, 177, 76]) * 0.6).astype(np.uint8)
ax.imshow(sub, extent=ext, origin="upper", zorder=0)

for i in hk.index:
    nm = hk.TOWN[i]
    if nm not in ("演海镇", "三江镇", "演丰镇", "灵山镇", "大致坡镇"):
        continue
    col = {"演海镇": "red", "三江镇": "blue", "演丰镇": "orange"}.get(nm, "gray")
    lw = 2.0 if nm == "演海镇" else 1.0
    for p in (hk.geometry[i].geoms if hk.geometry[i].geom_type == "MultiPolygon" else [hk.geometry[i]]):
        x, y = p.exterior.xy
        ax.plot(x, y, color=col, lw=lw, zorder=3)
ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
ax.set_title("演海镇(红) vs 三江/演丰(蓝/橙) vs 2002黑线", fontsize=13)
plt.tight_layout(); plt.savefig(OUT); plt.close()
print("已输出", OUT)
