# -*- coding: utf-8 -*-
"""核对：9 个新增/重画乡镇的边界与 2002 图黑线的贴合度。"""
import os
import numpy as np
import geopandas as gpd
from PIL import Image
from shapely.ops import unary_union
from shapely.validation import make_valid
from rasterio.transform import Affine
from rasterio.features import rasterize

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
TARGET_CRS = "EPSG:32649"
Wr, Hr, OX, OY = 2036, 2097, 0, 310
NEW = ["新海乡", "薛样乡", "美安镇", "东营镇", "桂林洋镇", "演海镇", "美仁坡乡", "新民乡", "谭文镇"]

g = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
g["CODE"] = g["CODE"].astype(str)
is_hk = g["CODE"].str.startswith("4601") | (g["CITY"].astype(str) == "海口市")
hk = g[is_hk].copy().reset_index(drop=True)
minx, miny, maxx, maxy = unary_union([x.buffer(0) for x in hk.geometry]).bounds
sx = (maxx - minx) / Wr
sy = (maxy - miny) / Hr
tr = Affine(sx, 0, minx, 0, -sy, maxy)

img = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002.png")).convert("RGB")).astype(np.int32)
dark = (img.sum(2) < 3 * 128)[OY:OY + Hr, OX:OX + Wr]
# 到最近黑线的距离(px)
import cv2
dist = cv2.distanceTransform((~dark).astype(np.uint8), cv2.DIST_L2, 5)


def boundary_px(geom):
    r = rasterize([(geom, 1)], out_shape=(Hr, Wr), transform=tr, fill=0, dtype="uint8") > 0
    b = np.zeros((Hr, Wr), bool)
    b[1:, :] |= r[1:, :] != r[:-1, :]
    b[:, 1:] |= r[:, 1:] != r[:, :-1]
    return b


print(f"格网 {sx:.2f}m/px  (1px={sx:.0f}m)")
print(f"{'乡镇':8s} {'边px':>7s} {'均值':>7s} {'P90':>7s} {'≤1px':>7s} {'≤2px':>7s}")
for nm in NEW:
    i = hk.index[hk.TOWN == nm][0]
    b = boundary_px(hk.geometry[i])
    d = dist[b]
    print(f"{nm:8s} {int(b.sum()):7d} {d.mean():7.2f} {np.percentile(d,90):7.2f} "
          f"{(d<=1).mean()*100:6.1f}% {(d<=2).mean()*100:6.1f}%")

# 仅“内部新界线”（去掉落在原父级外边界上的段）——用父级外边界缓冲剔除
PARENTS = {"新海乡": ["西秀镇", "长流镇"], "薛样乡": ["城西镇"], "美安镇": ["石山镇"],
           "东营镇": ["灵山镇"], "桂林洋镇": ["演丰镇"], "演海镇": ["三江镇", "演丰镇"],
           "美仁坡乡": ["龙泉镇"], "新民乡": ["甲子镇"], "谭文镇": ["三门坡镇"]}
print("\n仅内部新界线：")
for nm in NEW:
    if nm == "新海乡":
        continue
    i = hk.index[hk.TOWN == nm][0]
    pidx = [int(hk.index[hk.TOWN == p][0]) for p in PARENTS[nm]]
    pu = unary_union([hk.geometry[k].buffer(0) for k in pidx]).buffer(0)
    # 父级外边界（与其他乡镇相邻处）
    nb = unary_union([hk.geometry[k].buffer(0) for k in hk.index if k not in pidx]).buffer(0)
    outer = pu.boundary.intersection(nb.buffer(1.0))
    inner = hk.geometry[i].boundary.difference(outer.buffer(3.0))
    # 栅格化 inner
    from shapely.geometry import MultiLineString
    rr = rasterize([(inner, 1)], out_shape=(Hr, Wr), transform=tr, fill=0, dtype="uint8") > 0
    b = np.zeros((Hr, Wr), bool)
    b[1:, :] |= rr[1:, :] != rr[:-1, :]
    b[:, 1:] |= rr[:, 1:] != rr[:, :-1]
    d = dist[b]
    if b.sum():
        print(f"{nm:8s} {int(b.sum()):7d} {d.mean():7.2f} {np.percentile(d,90):7.2f} "
              f"{(d<=1).mean()*100:6.1f}% {(d<=2).mean()*100:6.1f}%")
