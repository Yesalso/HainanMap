# -*- coding: utf-8 -*-
"""侦察：颜色提取 + 配准 + 色块归属到当前海口乡镇。"""
import os, json
import numpy as np
import geopandas as gpd
import pandas as pd
from PIL import Image
from rasterio.transform import Affine
from rasterio.features import rasterize
from shapely.geometry import Polygon
from shapely.ops import unary_union

BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
SHP = os.path.join(BASE, "海南村界.shp")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")

TARGET_CRS = "EPSG:32649"
HAIKOU = "4601"

COLORS = {
    "新海乡": (0x00, 0xA2, 0xE8),
    "薛样乡": (0x7F, 0x7F, 0x7F),
    "美安镇": (0x88, 0x00, 0x15),
    "东营镇": (0x3F, 0x48, 0xCC),
    "桂林洋镇": (0xB5, 0xE6, 0x1D),
    "演海镇": (0x22, 0xB1, 0x4C),
    "美仁坡乡": (0xC3, 0xC3, 0xC3),
    "新民乡": (0x73, 0xFB, 0xFD),
    "谭文镇": (0xFF, 0xAE, 0xC9),
}


def rgba(path):
    return np.array(Image.open(path).convert("RGBA"))


def main():
    a = rgba(os.path.join(EMPTY, "Haikou_2002_a.png"))
    b = rgba(os.path.join(EMPTY, "Haikou_2002.png"))
    print("a shape", a.shape, "b shape", b.shape)
    # 色块统计
    rgb = a[:, :, :3].astype(np.int32)
    key = (rgb[:, :, 0] * 65536 + rgb[:, :, 1] * 256 + rgb[:, :, 2])
    uniq, cnt = np.unique(key, return_counts=True)
    print("--- Haikou_2002_a.png 调色板 ---")
    for k, c in sorted(zip(uniq, cnt), key=lambda t: -t[1]):
        print(f"  #{k>>16:02X}{(k>>8)&255:02X}{k&255:02X}  {c}")

    print("--- Haikou_2002.png 调色板 ---")
    rgb2 = b[:, :, :3].astype(np.int32)
    k2 = (rgb2[:, :, 0] * 65536 + rgb2[:, :, 1] * 256 + rgb2[:, :, 2])
    for k, c in sorted(zip(*np.unique(k2, return_counts=True)), key=lambda t: -t[1]):
        print(f"  #{k>>16:02X}{(k>>8)&255:02X}{k&255:02X}  {c}")

    # ---------- 当前海口 base ----------
    gdf = gpd.read_file(HT, encoding="utf-8").to_crs(TARGET_CRS)
    gdf["CODE"] = gdf["CODE"].astype(str)
    hk = gdf[gdf["CODE"].str.startswith(HAIKOU)].copy()
    uu = unary_union(list(hk.geometry)).buffer(0)
    print("海口 base 单元", len(hk), "面积", round(uu.area / 1e6, 2))
    minx, miny, maxx, maxy = uu.bounds
    print("bounds", minx, miny, maxx, maxy,
          "w/h km", round((maxx - minx) / 1000, 2), round((maxy - miny) / 1000, 2))

    # ---------- 配准：不同 (sx,ox,oy) 尝试 ----------
    W, H = 2968, 2432
    dark = (a[:, :, :3].sum(2) < 300) & (a[:, :, 3] > 0)
    print("dark px", int(dark.sum()))

    best = None
    for sx in (20.0, 25.0, 30.0, 35.0, 36.0, 40.0):
        Wr = int(round((maxx - minx) / sx))
        Hr = int(round((maxy - miny) / sx))
        if Wr <= 0 or Hr <= 0 or Wr > W or Hr > H:
            continue
        ssx = (maxx - minx) / Wr
        ssy = (maxy - miny) / Hr
        lab = rasterize([(g, i + 1) for i, g in enumerate(hk.geometry)],
                        out_shape=(Hr, Wr), transform=Affine(ssx, 0, minx, 0, -ssy, maxy),
                        fill=0, dtype="int32")
        B0 = np.zeros((Hr, Wr), bool)
        B0[1:, :] |= lab[1:, :] != lab[:-1, :]
        B0[:, 1:] |= lab[:, 1:] != lab[:, :-1]
        B0 |= lab > 0
        bb = np.zeros((Hr, Wr), bool)
        bb[1:, :] |= lab[1:, :] != lab[:-1, :]
        bb[:, 1:] |= lab[:, 1:] != lab[:, :-1]
        for oy in range(0, H - Hr + 1, 10):
            for ox in range(0, W - Wr + 1, 10):
                ov = int((dark[oy:oy + Hr, ox:ox + Wr] & bb).sum())
                if best is None or ov > best[0]:
                    best = (ov, sx, Wr, Hr, ox, oy)
    print("粗配准 best:", best)


if __name__ == "__main__":
    main()
