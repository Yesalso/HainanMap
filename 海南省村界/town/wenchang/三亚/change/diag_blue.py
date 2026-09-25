# -*- coding: utf-8 -*-
"""诊断：验证 change/Sanya_no_label.png 中 #3F48CC 画法是否存在、落在哪两个乡镇之间。"""
import numpy as np, cv2, geopandas as gpd, os

IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
OUT = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change"

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
print(f"PNG: {W}x{H}")

bgr = np.array([204, 72, 63], np.int16)          # #3F48CC -> BGR
d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
ys, xs = np.where(d <= 60)
print(f"#3F48CC 像素数(容差60): {len(xs)}")
if len(xs):
    print(f"  x范围 [{xs.min()},{xs.max()}]  y范围 [{ys.min()},{ys.max()}]")
    # 裁剪放大查看
    x0, x1 = max(0, xs.min()-120), min(W, xs.max()+120)
    y0, y1 = max(0, ys.min()-120), min(H, ys.max()+120)
    crop = img[y0:y1, x0:x1].copy()
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    cv2.imencode(".png", crop)[1].tofile(os.path.join(OUT, "_diag_blue_crop.png"))
    print(f"  裁剪区: x[{x0},{x1}] y[{y0},{y1}] -> _diag_blue_crop.png")

# SHP 检查
g = gpd.read_file(SHP, encoding="utf-8")
print(f"\nSHP: {len(g)} 要素, 字段: {[c for c in g.columns if c!='geometry']}")
print(f"CRS: {g.crs}")
city_col = "CITY" if "CITY" in g.columns else None
name_col = next((c for c in ["XZQMC","NAME","TOWN","Town"] if c in g.columns), None)
code_col = next((c for c in ["XZQDM","CODE"] if c in g.columns), None)
print(f"name_col={name_col}, code_col={code_col}, city_col={city_col}")
if city_col:
    s = g[g[city_col].astype(str).str.contains("三亚", na=False)]
else:
    s = g
print(f"三亚要素: {len(s)}")
print("  ", sorted(s[name_col].astype(str).tolist()))
minx, miny, maxx, maxy = s.total_bounds
print(f"四至: [{minx:.1f},{miny:.1f},{maxx:.1f},{maxy:.1f}]  {maxx-minx:.0f}x{maxy-miny:.0f} m")
for px in (30, 20):
    print(f"  1px={px}m 预期图像: {int(round((maxx-minx)/px))}x{int(round((maxy-miny)/px))}")
