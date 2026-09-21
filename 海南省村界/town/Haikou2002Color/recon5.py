# -*- coding: utf-8 -*-
"""局部放大（修正版：只画裁切区，保证像素与地理坐标一致）。"""
import os, sys
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

NAMES = ["新海乡", "薛样乡", "美安镇", "东营镇", "桂林洋镇", "演海镇", "美仁坡乡", "新民乡", "谭文镇"]
HEX = ["00A2E8", "7F7F7F", "880015", "3F48CC", "B5E61D", "22B14C", "C3C3C3", "73FBFD", "FFAEC9"]
RGB = {n: np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)]) for n, h in zip(NAMES, HEX)}

gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
gdf["CODE"] = gdf["CODE"].astype(str)
hk = gdf[gdf["CODE"].str.startswith("4601")].copy().reset_index(drop=True)
minx, miny, maxx, maxy = hk.total_bounds
Wr, Hr, ox, oy = 2036, 2097, 0, 310
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr

img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
H, W, _ = img.shape

# 裁切到配准区（grid 像素 = img[oy+row, ox+col]）
crop = img[oy:oy + Hr, ox:ox + Wr]
dark = crop.sum(2) < 3 * 128
ext = (minx, maxx, miny, maxy)


def zoom(x0, y0, x1, y1, fname, title):
    fig, axes = plt.subplots(2, 1, figsize=(15, 20))
    ax = axes[0]
    c0 = int((x0 - minx) / sx); c1 = int((x1 - minx) / sx)
    r0 = int((maxy - y1) / sy); r1 = int((maxy - y0) / sy)
    c0, r0 = max(0, c0), max(0, r0)
    c1, r1 = min(Wr, c1), min(Hr, r1)
    ax.imshow(crop[r0:r1, c0:c1].astype(np.uint8))
    ax.set_title("原图黑线")
    ax.axis("off")

    ax = axes[1]
    # 色块
    layer = np.ones((Hr, Wr, 4))
    for k, n in enumerate(NAMES, 2):
        m = np.abs(crop - RGB[n]).max(2) <= 20
        if m.any():
            c = RGB[n] / 255.0
            layer[m] = [c[0], c[1], c[2], 0.75]
    layer[dark] = [0, 0, 0, 1]
    ax.imshow(layer, extent=ext, origin="upper", zorder=0)
    from matplotlib import cm
    cmap = plt.get_cmap("tab20")
    for i, g in enumerate(hk.geometry):
        gs = g.geoms if g.geom_type == "MultiPolygon" else [g]
        for p in gs:
            x, y = p.exterior.xy
            ax.plot(x, y, color=cmap(i % 20), lw=1.3, zorder=2)
            cx, cy = p.centroid.x, p.centroid.y
            if x0 < cx < x1 and y0 < cy < y1:
                ax.annotate(hk.TOWN[i], (cx, cy), fontsize=9, ha="center",
                            color=cmap(i % 20), zorder=3,
                            bbox=dict(fc="white", ec="none", alpha=0.75))
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    ax.set_title(title)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTDIR, fname), dpi=105)
    plt.close()
    print("已输出", fname)


if __name__ == "__main__":
    zoom(407500, 2203000, 424000, 2221500, "zoom_west_fix.png", "西部：新海乡 + 长流镇/西秀镇/海秀镇")
    zoom(430000, 2190000, 476000, 2225000, "zoom_east_fix.png", "东部：桂林洋镇/演海镇/东营镇")
