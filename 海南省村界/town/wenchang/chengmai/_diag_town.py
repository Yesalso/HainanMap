# -*- coding: utf-8 -*-
import numpy as np, cv2, geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

CRS = "EPSG:32649"
SHP = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai\Hainan_town_chengmai.shp"
IMG = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai\Chengmai.png"

g = gpd.read_file(SHP, encoding="utf-8")
g = g.to_crs(CRS)
minx, miny, maxx, maxy = g.total_bounds
W, H = 2822, 3455
sx, sy = (maxx - minx) / W, (maxy - miny) / H

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)

colors = {
    "#3F48CC": "山口乡", "#22B14C": "金江镇", "#FFF200": "太平乡", "#C3C3C3": "石浮乡",
    "#00A2E8": "红岗乡", "#FF7F27": "文儒镇", "#7F7F7F": "福山镇", "#B5E61D": "老城镇",
    "#99D9EA": "白莲乡", "#7092BE": "新吴镇", "#C8BFE7": "美亭乡", "#E657E6": "永发镇",
}

def hex2bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))

# 每个像素属于哪个颜色
mask_any = np.zeros((H, W), np.uint8)
for hx in colors:
    bgr = np.array(hex2bgr(hx), np.int16)
    d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
    mask_any |= ((d <= 30).astype(np.uint8))

# 逐现行乡镇统计
print("现行乡镇           面积km2  彩色km2  白色km2  彩占比")
for idx, r in g.iterrows():
    geom = r.geometry
    minx2, miny2, maxx2, maxy2 = geom.bounds
    i0, i1 = int(max(0, (minx2 - minx) / sx)), int(min(W, (maxx2 - minx) / sx))
    j0, j1 = int(max(0, (maxy - maxy2) / sy)), int(min(H, (maxy - miny2) / sy))
    sub = mask_any[j0:j1, i0:i1]
    pix_col = int(sub.sum())
    area_col = pix_col * sx * sy
    a = geom.area
    print(f"{r['TOWN']:<6} {a/1e6:8.2f}  {area_col/1e6:8.2f}  {(a-area_col)/1e6:8.2f}  {area_col/a:5.1%}")