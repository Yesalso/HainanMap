# -*- coding: utf-8 -*-
"""西部细节：只看 新海乡(蓝底) 与 当前西秀镇/长流镇 的分区。"""
import os
import numpy as np
import geopandas as gpd
from PIL import Image
from rasterio.transform import Affine
from rasterio.features import rasterize
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
OUTDIR = os.path.join(TOWN, "Haikou2002Color")
TARGET_CRS = "EPSG:32649"

gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
gdf["CODE"] = gdf["CODE"].astype(str)
hk = gdf[gdf["CODE"].str.startswith("4601")].copy().reset_index(drop=True)
minx, miny, maxx, maxy = hk.total_bounds
Wr, Hr = 2036, 2097
ox, oy = 0, 310
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr

img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
H, W, _ = img.shape
base_lab = rasterize([(g, i + 1) for i, g in enumerate(hk.geometry)],
                     out_shape=(Hr, Wr), transform=Affine(sx, 0, minx, 0, -sy, maxy),
                     fill=0, dtype="int32")


def to_px(x, y):
    return (x - minx) / sx - 0.5, (maxy - y) / sy - 0.5


# 新海乡像素范围
d = np.abs(img[:, :, :3] - np.array([0x00, 0xA2, 0xE8])).max(2)
m = d <= 20
rr, cc = np.nonzero(m[oy:oy + Hr, ox:ox + Wr])
print("新海乡 行列范围 row", rr.min(), rr.max(), "col", cc.min(), cc.max())

for tag, (x0, y0, x1, y1) in {
    "westA": (406000, 2205000, 425000, 2222000),
}.items():
    c0, r0 = to_px(x0, y1)
    c1, r1 = to_px(x1, y0)
    c0, r0, c1, r1 = max(0, int(c0)), max(0, int(r0)), min(W, int(c1)), min(H, int(r1))
    fig, axes = plt.subplots(2, 1, figsize=(16, 22))
    ax = axes[0]
    ax.imshow(img[r0:r1, c0:c1].astype(np.uint8))
    ax.set_title(f"原图 crop col{c0}-{c1} row{r0}-{r1}")
    ax.axis("off")

    ax = axes[1]
    for nm, col in [("西秀镇", "red"), ("长流镇", "blue"), ("海秀镇", "purple"),
                    ("石山镇", "green"), ("东山镇", "orange")]:
        s = hk[hk.TOWN == nm]
        if len(s) == 0:
            continue
        g = s.geometry.iloc[0]
        gs = g.geoms if g.geom_type == "MultiPolygon" else [g]
        for p in gs:
            x, y = p.exterior.xy
            ax.plot(x, y, color=col, lw=2.0, label=nm if p is gs[0] else None)
    # 黑线
    dark = img[:, :, :3].sum(2) < 250
    rgba2 = np.zeros((H, W, 4))
    rgba2[dark] = [0, 0, 0, 1]
    ax.imshow(rgba2, extent=(minx, maxx, miny, maxy), origin="upper", zorder=0)
    # 蓝块
    rgba = np.zeros((H, W, 4))
    rgba[m] = [0x00 / 255, 0xA2 / 255, 0xE8 / 255, 0.45]
    ax.imshow(rgba, extent=(minx, maxx, miny, maxy), origin="upper", zorder=1)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.legend(loc="upper right")
    ax.set_title("当前乡镇(实线) + 新海乡(蓝)")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTDIR, f"zoom_{tag}.png"), dpi=105)
    plt.close()
    print("已输出", f"zoom_{tag}.png")
