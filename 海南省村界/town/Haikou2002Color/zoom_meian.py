# -*- coding: utf-8 -*-
"""放大核对：美安镇 vs 原石山镇外边界 / 2002 黑线。"""
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
OLD = os.path.join(TOWN, "Hainan2002", "Hainan_town_before_haikou2002.shp")
OUT = os.path.join(TOWN, "Haikou2002Color", "zoom_meian.png")
TARGET_CRS = "EPSG:32649"
Wr, Hr, OX, OY = 2036, 2097, 0, 310


def hk(p):
    g = gpd.read_file(p, encoding="utf-8").to_crs(TARGET_CRS)
    g["CODE"] = g["CODE"].astype(str)
    return g[g["CODE"].str.startswith("4601") | (g["CITY"].astype(str) == "海口市")].copy().reset_index(drop=True)


new = hk(HT)
old = hk(OLD)
minx, miny, maxx, maxy = unary_union([x.buffer(0) for x in new.geometry]).bounds
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr

img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002.png")).convert("RGB")).astype(np.int32)
dark = (img.sum(2) < 3 * 128)[OY:OY + Hr, OX:OX + Wr]

gm = new.geometry[new.index[new.TOWN == "美安镇"][0]]
x0, y0, x1, y1 = gm.bounds
x0 -= 4000; y0 -= 4000; x1 += 4000; y1 += 4000

c0 = int((x0 - minx) / sx); c1 = int((x1 - minx) / sx)
r0 = int((maxy - y1) / sy); r1 = int((maxy - y0) / sy)
c0, r0 = max(0, c0), max(0, r0); c1, r1 = min(Wr, c1), min(Hr, r1)
ext = (minx + c0 * sx, minx + c1 * sx, maxy - r1 * sy, maxy - r0 * sy)

fig, ax = plt.subplots(figsize=(13, 13), dpi=120)
# 黑线（浅灰底）
bl = np.ones((r1 - r0, c1 - c0, 3), np.uint8) * 255
bl[dark[r0:r1, c0:c1]] = [120, 120, 120]
ax.imshow(bl, extent=ext, origin="upper", zorder=0)

colors = {"美安镇": "#ffb6c1", "石山镇": "#ffe9a8", "永兴镇": "#c8e6c9",
          "东山镇": "#bbdefb", "长流镇": "#e1bee7"}
for i in new.index:
    nm = new.TOWN[i]
    if nm not in colors:
        continue
    g = new.geometry[i]
    for p in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
        xs, ys = p.exterior.xy
        ax.fill(xs, ys, color=colors[nm], alpha=0.5, zorder=1)
        ax.plot(xs, ys, color="black", lw=0.7, zorder=3)
    c = g.representative_point()
    ax.annotate(nm, (c.x, c.y), fontsize=11, ha="center", zorder=6,
                bbox=dict(fc="white", ec="none", alpha=0.7))
# 原石山镇外边界
og = old.geometry[old.index[old.TOWN == "石山镇"][0]]
for p in (og.geoms if og.geom_type == "MultiPolygon" else [og]):
    xs, ys = p.exterior.xy
    ax.plot(xs, ys, color="red", lw=1.6, ls="--", zorder=4, label="原石山镇外边界")
ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
ax.set_title("美安镇(粉) / 石山镇(黄) vs 原石山镇外边界(红虚线) / 2002黑线(灰)", fontsize=12)
ax.legend(loc="upper right", fontsize=10)
plt.tight_layout(); plt.savefig(OUT); plt.close()
print("已输出", OUT)
