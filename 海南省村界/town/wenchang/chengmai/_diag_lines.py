# -*- coding: utf-8 -*-
import numpy as np, cv2, geopandas as gpd

CRS = "EPSG:32649"
SHP = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai\Hainan_town_chengmai.shp"
IMG = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai\Chengmai.png"

g = gpd.read_file(SHP, encoding="utf-8")
g = g.to_crs(CRS)
minx, miny, maxx, maxy = g.total_bounds
W, H = 2822, 3455
sx, sy = (maxx - minx) / W, (maxy - miny) / H

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# 现行SHP边界栅格化
lines = np.zeros((H, W), np.uint8)
for geom in g.geometry:
    ring = geom.boundary
    segs = list(ring.geoms) if ring.geom_type in ("MultiLineString", "GeometryCollection") else [ring]
    for ln in segs:
        if ln is None or ln.is_empty:
            continue
        c = np.array(ln.coords)
        if len(c) < 2:
            continue
        pts = np.stack([(c[:, 0] - minx) / sx, (maxy - c[:, 1]) / sy], 1).astype(np.int32)
        cv2.polylines(lines, [pts], False, 255, 1, cv2.LINE_8)
lines = (lines == 255).astype(np.uint8)

# 手绘黑线
drawn = (gray < 100).astype(np.uint8)
# 手绘黑线到现行边界的距离
dt = cv2.distanceTransform((1 - lines) * 255, cv2.DIST_L2, 3)
ys, xs = np.nonzero(drawn)
dd = dt[ys, xs]
print(f"手绘黑像素: {int(drawn.sum())}, 现行SHP边界像素: {int(lines.sum())}")
for t in (0, 1, 2, 3, 5):
    print(f"  手绘黑像素 距现行边界 <= {t}px: {(dd <= t).mean():.4f}")
print(f"  平均 {dd.mean():.2f}px  中位 {np.median(dd):.2f}px")

# 手绘黑线总长度（近似）
print("\n手绘黑线中存在但距现行边界较远的像素（>3px，即边界经过了修改）:", int((dd > 3).sum()))