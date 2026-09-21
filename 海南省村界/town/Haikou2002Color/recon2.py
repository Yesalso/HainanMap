# -*- coding: utf-8 -*-
"""侦察2：色块 → 当前乡镇 归属矩阵 + 可视化。"""
import os
import numpy as np
import geopandas as gpd
import pandas as pd
from PIL import Image
from rasterio.transform import Affine
from rasterio.features import rasterize
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.ops import unary_union
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
OUTDIR = os.path.join(TOWN, "Haikou2002Color")
os.makedirs(OUTDIR, exist_ok=True)

TARGET_CRS = "EPSG:32649"
HAIKOU = "4601"

NAMES = ["新海乡", "薛样乡", "美安镇", "东营镇", "桂林洋镇", "演海镇", "美仁坡乡", "新民乡", "谭文镇"]
HEX = ["00A2E8", "7F7F7F", "880015", "3F48CC", "B5E61D", "22B14C", "C3C3C3", "73FBFD", "FFAEC9"]
RGB = {n: np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.int32)
       for n, h in zip(NAMES, HEX)}


def main():
    img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
    H, W, _ = img.shape

    # 分类图：0=线/其他, 1=白, 2..10=各色
    cls = np.zeros((H, W), np.int8)
    near_white = (img[:, :, 0] > 200) & (img[:, :, 1] > 200) & (img[:, :, 2] > 200)
    cls[near_white] = 1
    for k, n in enumerate(NAMES, 2):
        d = np.abs(img - RGB[n]).max(2)
        cls[d <= 20] = k
    print("类别像素数：")
    for k, n in enumerate(["线/其他", "白"] + NAMES):
        print(f"  {n:6s} {int((cls == k).sum())}")

    # 连通域（同类别 4 连通）
    zones = np.zeros((H, W), np.int32)
    nxt = 0
    zone_cls = [0]
    zone_list = []
    for k in range(1, len(NAMES) + 2):
        lab, n = ndimage.label(cls == k, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
        for i in range(1, n + 1):
            m = lab == i
            a = int(m.sum())
            if a < 60:
                continue
            nxt += 1
            zones[m] = nxt
            zone_cls.append(k)
            zone_list.append((nxt, k, a, m))
    print("连通域数：", nxt)

    # 几何配准
    gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
    gdf["CODE"] = gdf["CODE"].astype(str)
    hk = gdf[gdf["CODE"].str.startswith(HAIKOU)].copy().reset_index(drop=True)
    uu = unary_union(list(hk.geometry)).buffer(0)
    minx, miny, maxx, maxy = uu.bounds
    Wr, Hr = 2036, 2097
    ox, oy = 0, 310
    sx = (maxx - minx) / Wr
    sy = (maxy - miny) / Hr
    print("sx,sy =", sx, sy)

    base_lab = rasterize([(g, i + 1) for i, g in enumerate(hk.geometry)],
                         out_shape=(Hr, Wr),
                         transform=Affine(sx, 0, minx, 0, -sy, maxy), fill=0, dtype="int32")
    zc = zones[oy:oy + Hr, ox:ox + Wr]

    # 归属矩阵
    print("\n=== 色块 × 当前乡镇 归属（面积 km²，只列 >0.5）===")
    px_area = sx * sy / 1e6
    for k, n in enumerate(NAMES, 2):
        m = (zc > 0) & np.isin(zc, [z[0] for z in zone_list if z[1] == k])
        if not m.any():
            print(f"{n}: 无像素")
            continue
        b = np.bincount(base_lab[m].ravel(), minlength=len(hk) + 1)
        tot = m.sum() * px_area
        print(f"\n{n}  总 {tot:.2f} km²  (0=海口外 {b[0]*px_area:.3f})")
        for i in np.argsort(-b):
            if i == 0 or b[i] * px_area < 0.5:
                continue
            print(f"    {hk.TOWN[i-1]:8s} {hk.CODE[i-1]}  {b[i]*px_area:8.2f} km²"
                  f"  占该镇 {b[i]/max(1,int((base_lab==i).sum()))*100:5.1f}%")

    # ---------- 可视化 ----------
    fig, axes = plt.subplots(1, 2, figsize=(26, 14))
    ax = axes[0]
    ax.imshow(img.astype(np.uint8))
    ax.set_title("Haikou_2002_a.png（原图）")
    ax.axis("off")

    ax = axes[1]
    ax.set_facecolor("white")
    xs = np.arange(Wr) * sx + minx
    ys = np.arange(Hr)[::-1] * sy + miny
    # 当前乡镇边界
    for g in hk.geometry:
        gs = g.geoms if g.geom_type == "MultiPolygon" else [g]
        for p in gs:
            x, y = p.exterior.xy
            ax.plot(x, y, color="#999999", lw=0.5)
    cmap = plt.get_cmap("tab20")
    for i, g in enumerate(hk.geometry):
        c = cmap(i % 20)
        gs = g.geoms if g.geom_type == "MultiPolygon" else [g]
        for p in gs:
            x, y = p.exterior.xy
            ax.plot(x, y, color=c, lw=1.4)
    handles = [Patch(color=cmap(i % 20), label=hk.TOWN[i]) for i in range(len(hk))]
    ax.legend(handles=handles, fontsize=7, ncol=3, loc="lower right")
    # 色块
    for k, n in enumerate(NAMES, 2):
        m = (zc > 0) & np.isin(zc, [z[0] for z in zone_list if z[1] == k])
        if not m.any():
            continue
        rgbn = RGB[n] / 255.0
        rgba = np.zeros((Hr, Wr, 4))
        rgba[m] = [rgbn[0], rgbn[1], rgbn[2], 0.85]
        ax.imshow(rgba, extent=(minx, maxx, miny, maxy), origin="upper")
    ax.set_title("当前海口乡镇(彩色线) + 2002色块(填充)")
    ax.set_aspect("equal")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTDIR, "recon_overlay.png"), dpi=110)
    print("\n已输出", os.path.join(OUTDIR, "recon_overlay.png"))


if __name__ == "__main__":
    main()
