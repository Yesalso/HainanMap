# -*- coding: utf-8 -*-
"""配准验证：黑线范围反推仿射；确认 #3F48CC 蓝线落在荔枝沟区/牛岭乡之间。"""
import numpy as np, cv2, geopandas as gpd, os
from shapely.ops import unary_union

IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
OUT = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change"

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
dark = (img.astype(np.int16).sum(axis=2) < 300).astype(np.uint8)   # 黑线
ys, xs = np.where(dark)
bx0, bx1, by0, by1 = xs.min(), xs.max(), ys.min(), ys.max()
print(f"黑线bbox: x[{bx0},{bx1}] y[{by0},{by1}]  ({bx1-bx0+1}x{by1-by0+1})")

g = gpd.read_file(SHP, encoding="utf-8")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy()
s = s.to_crs("EPSG:32649")
minx, miny, maxx, maxy = s.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny

# 假设：黑线bbox 外沿 ≈ 四至（线宽一半误差 1-2px）
sx = geo_w / (bx1 - bx0)
sy = geo_h / (by1 - by0)
print(f"反推比例: {sx:.3f} / {sy:.3f} m/px")

def px2geo(i, j):
    return minx + (i - bx0) * sx, maxy - (j - by0) * sy

# 蓝线像素 -> 地理坐标
bgr = np.array([204, 72, 63], np.int16)
d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
bys, bxs = np.where(d <= 60)
pts = [px2geo(i, j) for i, j in zip(bxs, bys)]
gx = [p[0] for p in pts]; gy = [p[1] for p in pts]
print(f"蓝线地理范围: x[{min(gx):.0f},{max(gx):.0f}] y[{min(gy):.0f},{max(gy):.0f}]")

# 荔枝沟区 / 牛岭乡 几何
lz = s[s["TOWN"] == "荔枝沟区"].geometry.union_all()
nl = s[s["TOWN"] == "牛岭乡"].geometry.union_all()
U2 = unary_union([lz, nl]).buffer(0)
from shapely.geometry import MultiPoint, LineString
mp = MultiPoint(pts)
line = LineString(pts) if len(pts) > 1 else None
print(f"蓝线质心到荔枝沟区距离: {lz.distance(mp.centroid):.0f} m, 到牛岭乡: {nl.distance(mp.centroid):.0f} m")
print(f"蓝线整体在 U2(荔+牛)内像素比例: ", end="")
inside = sum(1 for p in pts if U2.contains(MultiPoint([p])))
# 用 prepared 更快，无所谓，量小
print(f"{inside}/{len(pts)}")
# 蓝线附近哪些乡镇
near = []
for _, r in s.iterrows():
    dd = r.geometry.distance(mp.centroid)
    near.append((round(dd), r["TOWN"]))
near.sort()
print("质心到各乡镇距离(m):", near[:6])

# 检查蓝线是否与现行荔-牛共享边界接近
shared = lz.boundary.intersection(nl.boundary)
print(f"现行荔-牛共享边界长度: {shared.length if not shared.is_empty else 0:.0f} m")
if line is not None and shared.length > 0:
    print(f"蓝线到共享边界最近距离: {line.distance(shared):.0f} m")
print(f"蓝线长度(折线): {line.length if line else 0:.0f} m")

# 出诊断叠加图：荔枝沟=橙框, 牛岭=红框, 蓝线=高亮
vis = img.copy()
def draw(geom, color):
    for p in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
        c = np.asarray(p.exterior.coords)
        pi = np.stack([(c[:,0]-minx)/sx + bx0, (maxy-c[:,1])/sy + by0], 1).astype(np.int32)
        cv2.polylines(vis, [pi], True, color, 2, cv2.LINE_AA)
draw(lz, (255, 128, 0))   # 橙=荔枝沟
draw(nl, (0, 0, 255))     # 红=牛岭
cv2.imencode(".png", vis)[1].tofile(os.path.join(OUT, "_diag_overlay.png"))
x0, x1 = max(0, bxs.min()-200), min(W, bxs.max()+200)
y0, y1 = max(0, bys.min()-200), min(H, bys.max()+200)
crop = vis[y0:y1, x0:x1]
crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
cv2.imencode(".png", crop)[1].tofile(os.path.join(OUT, "_diag_overlay_crop.png"))
print("输出 _diag_overlay.png / _diag_overlay_crop.png")
