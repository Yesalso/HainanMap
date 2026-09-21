# -*- coding: utf-8 -*-
"""列出西部块内所有 map 分区（zone），看 2002 图怎么切 西秀镇/长流镇/新海乡。"""
import os
import numpy as np
import geopandas as gpd
from PIL import Image
from rasterio.transform import Affine
from rasterio.features import rasterize
from scipy import ndimage

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
TARGET_CRS = "EPSG:32649"

NAMES = ["新海乡", "薛样乡", "美安镇", "东营镇", "桂林洋镇", "演海镇", "美仁坡乡", "新民乡", "谭文镇"]
HEX = ["00A2E8", "7F7F7F", "880015", "3F48CC", "B5E61D", "22B14C", "C3C3C3", "73FBFD", "FFAEC9"]
RGB = {n: np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)]) for n, h in zip(NAMES, HEX)}

img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
H, W, _ = img.shape
near_white = (img[:, :, 0] > 200) & (img[:, :, 1] > 200) & (img[:, :, 2] > 200)
cls = np.zeros((H, W), np.int8)
cls[near_white] = 1
for k, n in enumerate(NAMES, 2):
    cls[np.abs(img - RGB[n]).max(2) <= 20] = k

gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
gdf["CODE"] = gdf["CODE"].astype(str)
hk = gdf[gdf["CODE"].str.startswith("4601")].copy().reset_index(drop=True)
minx, miny, maxx, maxy = hk.total_bounds
Wr, Hr, ox, oy = 2036, 2097, 0, 310
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr
base_lab = rasterize([(g, i + 1) for i, g in enumerate(hk.geometry)],
                     out_shape=(Hr, Wr), transform=Affine(sx, 0, minx, 0, -sy, maxy),
                     fill=0, dtype="int32")
zc = cls[oy:oy + Hr, ox:ox + Wr]

st4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
zones = np.zeros((Hr, Wr), np.int32)
info = []
nid = 0
for k in range(1, len(NAMES) + 2):
    lab, n = ndimage.label(zc == k, structure=st4)
    for i in range(1, n + 1):
        m = lab == i
        a = int(m.sum())
        if a < 25:
            continue
        nid += 1
        zones[m] = nid
        ys, xs = np.nonzero(m)
        cy, cx = ys.mean(), xs.mean()
        gx = minx + (cx + 0.5) * sx
        gy = maxy - (cy + 0.5) * sy
        b = np.bincount(base_lab[m].ravel(), minlength=len(hk) + 1)
        top = np.argsort(-b)[:3]
        info.append(dict(id=nid, cls=k, name=("白" if k == 1 else NAMES[k - 2]),
                         px=a, km2=a * sx * sy / 1e6, gx=gx, gy=gy,
                         owner=[(hk.TOWN[j - 1] if j > 0 else "海口外", round(b[j] * sx * sy / 1e6, 2))
                                for j in top if b[j] > 0]))
px_area = sx * sy / 1e6

print("=== 西部块 (x 406000-425000, y 2202000-2222000) 内分区 ===")
for d in info:
    if 406000 < d["gx"] < 425000 and 2202000 < d["gy"] < 2222000:
        print(f"  id{d['id']:3d} {d['name']:6s} {d['km2']:7.2f}km²  质心({d['gx']:.0f},{d['gy']:.0f})  {d['owner']}")

print("\n=== 全部 zone 概览（按面积）===")
for d in sorted(info, key=lambda t: -t["km2"])[:60]:
    print(f"  id{d['id']:3d} {d['name']:6s} {d['km2']:8.2f}km²  质心({d['gx']:.0f},{d['gy']:.0f})  {d['owner']}")

np.save(os.path.join(TOWN, "Haikou2002Color", "zones.npy"), zones)
import json
with open(os.path.join(TOWN, "Haikou2002Color", "zones.json"), "w", encoding="utf-8") as f:
    json.dump(info, f, ensure_ascii=False, indent=1)
print("\n已保存 zones.npy / zones.json  zone 总数", nid)
