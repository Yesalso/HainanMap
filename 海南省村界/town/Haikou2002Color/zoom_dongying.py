# -*- coding: utf-8 -*-
"""放大核对：灵山镇 / 东营镇 / 演丰镇 交界处的残留。"""
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
OUT = os.path.join(TOWN, "Haikou2002Color", "zoom_dongying.png")
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

x0, y0, x1, y1 = 439500, 2215800, 442800, 2217600
c0 = int((x0 - minx) / sx); c1 = int((x1 - minx) / sx)
r0 = int((maxy - y1) / sy); r1 = int((maxy - y0) / sy)
c0, r0 = max(0, c0), max(0, r0); c1, r1 = min(Wr, c1), min(Hr, r1)
ext = (minx + c0 * sx, minx + c1 * sx, maxy - r1 * sy, maxy - r0 * sy)

fig, ax = plt.subplots(figsize=(15, 12), dpi=130)
base = crop[r0:r1, c0:c1].astype(np.uint8).copy()
col = cropa[r0:r1, c0:c1]
mask = np.abs(col.astype(int) - np.array([63, 72, 204])).max(2) <= 20   # 东营镇蓝
base[mask] = (base[mask] * 0.35 + np.array([63, 72, 204]) * 0.65).astype(np.uint8)
mask2 = np.abs(col.astype(int) - np.array([34, 177, 76])).max(2) <= 20  # 演海绿
base[mask2] = (base[mask2] * 0.35 + np.array([34, 177, 76]) * 0.65).astype(np.uint8)
mask3 = np.abs(col.astype(int) - np.array([181, 230, 29])).max(2) <= 20  # 桂林洋
base[mask3] = (base[mask3] * 0.35 + np.array([181, 230, 29]) * 0.65).astype(np.uint8)
ax.imshow(base, extent=ext, origin="upper", zorder=0)

colors = {"东营镇": "red", "灵山镇": "blue", "演丰镇": "orange",
          "桂林洋镇": "green", "三江镇": "purple", "大致坡镇": "brown"}
for i in hk.index:
    nm = hk.TOWN[i]
    if nm not in colors:
        continue
    for p in (hk.geometry[i].geoms if hk.geometry[i].geom_type == "MultiPolygon" else [hk.geometry[i]]):
        xs, ys = p.exterior.xy
        ax.plot(xs, ys, color=colors[nm], lw=1.6, zorder=3)
    c = hk.geometry[i].representative_point()
    if x0 < c.x < x1 and y0 < c.y < y1:
        ax.annotate(nm, (c.x, c.y), fontsize=11, color=colors[nm], zorder=5,
                    bbox=dict(fc="white", ec="none", alpha=0.7))
ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
ax.set_title("灵山镇(蓝)/东营镇(红)/演丰镇(橙)/桂林洋镇(绿) 交界", fontsize=13)
plt.tight_layout(); plt.savefig(OUT); plt.close()
print("已输出", OUT)
