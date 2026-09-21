# -*- coding: utf-8 -*-
"""精细配准诊断：base 边界 vs 图黑线 的重合度 + 可视化叠加。"""
import os
import numpy as np
import geopandas as gpd
from PIL import Image
from rasterio.transform import Affine
from rasterio.features import rasterize
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei"]

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
OUTDIR = os.path.join(TOWN, "Haikou2002Color")
TARGET_CRS = "EPSG:32649"

img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002.png")).convert("RGB")).astype(np.int32)
H, W, _ = img.shape
dark = (img[:, :, 0] + img[:, :, 1] + img[:, :, 2]) < 3 * 128
print("黑线像素", int(dark.sum()))

gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
gdf["CODE"] = gdf["CODE"].astype(str)
hk = gdf[gdf["CODE"].str.startswith("4601")].copy().reset_index(drop=True)
minx, miny, maxx, maxy = hk.total_bounds
sx = sy = 30.0
Wr = int(round((maxx - minx) / sx))
Hr = int(round((maxy - miny) / sy))
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr
print("Wr,Hr", Wr, Hr, "sx,sy", sx, sy)

lab = rasterize([(g, i + 1) for i, g in enumerate(hk.geometry)],
                out_shape=(Hr, Wr), transform=Affine(sx, 0, minx, 0, -sy, maxy),
                fill=0, dtype="int32")
inner = np.zeros((Hr, Wr), bool)
inner[1:, :] |= lab[1:, :] != lab[:-1, :]
inner[:, 1:] |= lab[:, 1:] != lab[:, :-1]
# 也把每个多边形的边界画出来
outer = np.zeros((Hr, Wr), bool)
for g in hk.geometry:
    gs = g.geoms if g.geom_type == "MultiPolygon" else [g]
    for p in gs:
        r = rasterize([(p, 1)], out_shape=(Hr, Wr),
                      transform=Affine(sx, 0, minx, 0, -sy, maxy), fill=0, dtype="uint8") > 0
        b = np.zeros((Hr, Wr), bool)
        b[1:, :] |= r[1:, :] != r[:-1, :]
        b[:, 1:] |= r[:, 1:] != r[:, :-1]
        outer |= b
del inner
print("base 边界像素", int(outer.sum()))

# 细扫
best = []
for oy in range(280, 350):
    for ox in range(-8, 25):
        if oy < 0 or ox < 0 or oy + Hr > H or ox + Wr > W:
            continue
        c = dark[oy:oy + Hr, ox:ox + Wr]
        ov = int((c & outer).sum())
        best.append((ov, ox, oy))
best.sort(reverse=True)
print("前 10：", best[:10])
ov, ox, oy = best[0]
ratio = ov / int(outer.sum())
covd = ov / int(dark[oy:oy + Hr, ox:ox + Wr].sum())
print(f"最优偏移 ({ox},{oy})  重合 {ov}  base边界命中率 {ratio:.3f}  黑线命中率 {covd:.3f}")

# 可视化
c = dark[oy:oy + Hr, ox:ox + Wr]
vis = np.ones((Hr, Wr, 3), np.uint8) * 255
vis[c] = [0, 0, 0]
vis[outer] = [255, 0, 0]
both = c & outer
vis[both] = [0, 160, 0]
Image.fromarray(vis).save(os.path.join(OUTDIR, "align_check.png"))
print("已输出 align_check.png  (绿=重合, 红=仅base, 黑=仅图)")

# 统计：每个乡镇边界命中率
print("\n各乡镇 base 边界与图黑线重合率：")
for i, g in enumerate(hk.geometry):
    gs = g.geoms if g.geom_type == "MultiPolygon" else [g]
    nb = 0
    hit = 0
    for p in gs:
        r = rasterize([(p, 1)], out_shape=(Hr, Wr),
                      transform=Affine(sx, 0, minx, 0, -sy, maxy), fill=0, dtype="uint8") > 0
        b = np.zeros((Hr, Wr), bool)
        b[1:, :] |= r[1:, :] != r[:-1, :]
        b[:, 1:] |= r[:, 1:] != r[:, :-1]
        nb += int(b.sum())
        hit += int((b & c).sum())
    print(f"  {hk.TOWN[i]:8s} {hk.CODE[i]}  边界px {nb:6d}  命中 {hit:6d}  {hit/max(1,nb)*100:5.1f}%")
