# -*- coding: utf-8 -*-
"""放大看西部（新海乡/西秀镇/长流镇）与演海镇跨镇问题。"""
import os
import numpy as np
import geopandas as gpd
from PIL import Image
from rasterio.transform import Affine
from rasterio.features import rasterize
from scipy import ndimage
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


def main():
    img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
    H, W, _ = img.shape
    gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
    gdf["CODE"] = gdf["CODE"].astype(str)
    hk = gdf[gdf["CODE"].str.startswith("4601")].copy().reset_index(drop=True)
    minx, miny, maxx, maxy = hk.total_bounds
    Wr, Hr = 2036, 2097
    ox, oy = 0, 310
    sx = (maxx - minx) / Wr
    sy = (maxy - miny) / Hr
    base_lab = rasterize([(g, i + 1) for i, g in enumerate(hk.geometry)],
                         out_shape=(Hr, Wr),
                         transform=Affine(sx, 0, minx, 0, -sy, maxy), fill=0, dtype="int32")

    def to_px(x, y):
        return (x - minx) / sx - 0.5, (maxy - y) / sy - 0.5

    def render(crop_geo, fname, title):
        x0, y0, x1, y1 = crop_geo
        c0, r0 = to_px(x0, y1)
        c1, r1 = to_px(x1, y0)
        c0, r0, c1, r1 = int(c0), int(r0), int(c1), int(r1)
        c0, r0 = max(0, c0), max(0, r0)
        c1, r1 = min(W, c1), min(H, r1)
        sub = img[r0:r1, c0:c1].astype(np.uint8)
        fig, axes = plt.subplots(1, 2, figsize=(20, 12))
        ax = axes[0]
        ax.imshow(sub)
        ax.set_title("原图 " + title)
        ax.axis("off")

        ax = axes[1]
        # 底图：当前乡镇
        from matplotlib import cm
        cmap = plt.get_cmap("tab20")
        for i, g in enumerate(hk.geometry):
            gs = g.geoms if g.geom_type == "MultiPolygon" else [g]
            for p in gs:
                x, y = p.exterior.xy
                ax.plot(x, y, color=cmap(i % 20), lw=1.6)
                cx, cy = p.centroid.x, p.centroid.y
                if x0 < cx < x1 and y0 < cy < y1:
                    ax.annotate(hk.TOWN[i], (cx, cy), fontsize=10, ha="center",
                                color=cmap(i % 20),
                                bbox=dict(fc="white", ec="none", alpha=0.7))
        # 色块
        for k, n in enumerate(NAMES, 2):
            d = np.abs(img[:, :, :3] - RGB[n]).max(2)
            m = d <= 20
            if not m.any():
                continue
            rgbn = RGB[n] / 255.0
            rgba = np.zeros((H, W, 4))
            rgba[m] = [rgbn[0], rgbn[1], rgbn[2], 0.6]
            ax.imshow(rgba, extent=(minx, maxx, miny, maxy), origin="upper", zorder=0)
        # 显示手绘黑线
        dark = img[:, :, :3].sum(2) < 250
        rgba2 = np.zeros((H, W, 4))
        rgba2[dark] = [0, 0, 0, 1]
        ax.imshow(rgba2, extent=(minx, maxx, miny, maxy), origin="upper", zorder=1)
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal")
        ax.set_title(title)
        plt.tight_layout()
        plt.savefig(os.path.join(OUTDIR, fname), dpi=110)
        plt.close()
        print("已输出", fname)

    # 西部
    w = hk[hk.TOWN.isin(["西秀镇", "长流镇", "海秀镇", "石山镇"])]
    bx = w.total_bounds
    render((bx[0] - 2000, bx[1] - 2000, bx[2] + 2000, bx[3] + 2000),
           "zoom_west.png", "西部：新海乡/西秀镇/长流镇")
    # 东部：演海镇
    e = hk[hk.TOWN.isin(["演丰镇", "三江镇", "灵山镇", "大致坡镇"])]
    bx = e.total_bounds
    render((bx[0] - 2000, bx[1] - 2000, bx[2] + 2000, bx[3] + 2000),
           "zoom_east.png", "东部：演海镇/桂林洋镇/东营镇")


if __name__ == "__main__":
    main()
