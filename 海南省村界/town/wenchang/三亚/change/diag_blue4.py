# -*- coding: utf-8 -*-
"""诊断3：列平均提取蓝线中心线 + 端点锚定拓扑分析 + 可视化。"""
import numpy as np, cv2, geopandas as gpd, os
from shapely.ops import unary_union, linemerge
from shapely.geometry import LineString, Point
from shapely import make_valid

IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
OUT = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change"

g = gpd.read_file(SHP, encoding="utf-8")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy()
utm = s.to_crs("EPSG:32649")
minx, miny, maxx, maxy = utm.total_bounds
img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]

bgr = np.array([204, 72, 63], np.int16)
d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
mask = (d <= 90).astype(np.uint8)
# 膨胀1px弥合抗锯齿断裂，再取骨架：按列平均
mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), 1)
cols = {}
for j, i in zip(*np.where(mask > 0)):
    cols.setdefault(i, []).append(j)
xs = sorted(cols)
cx, cy = [], []
for i in xs:
    cy.append(maxy - np.mean(cols[i]) * 30.0)
    cx.append(minx + i * 30.0)
line = LineString(list(zip(cx, cy))).simplify(10, preserve_topology=True)
print(f"中心线：{len(xs)} 列 -> {len(line.coords)} 点，长 {line.length:.0f} m")
e1, e2 = Point(line.coords[0]), Point(line.coords[-1])
print(f"e1 {tuple(round(c) for c in e1.coords[0])}  e2 {tuple(round(c) for c in e2.coords[0])}")

A = utm[utm["TOWN"] == "荔枝沟区"].geometry.union_all().buffer(0)
B = utm[utm["TOWN"] == "牛岭乡"].geometry.union_all().buffer(0)
U2 = unary_union([A, B]).buffer(0)
S = A.boundary.intersection(B.boundary)
print(f"e1 到S: {e1.distance(S):.0f} m, 到U2外边界: {e1.distance(U2.boundary.difference(S.buffer(1))):.0f} m, 在U2内: {U2.covers(e1)}")
print(f"e2 到S: {e2.distance(S):.0f} m, 在U2内: {U2.covers(e2)}")

# 端点附近的边界网络：哪个乡镇与谁相邻
def who_near(pt, r=300):
    out = []
    for _, row in utm.iterrows():
        dd = row.geometry.boundary.distance(pt)
        if dd <= r:
            out.append((row["TOWN"], round(dd)))
    return sorted(out, key=lambda t: t[1])
print("e1 300m内乡镇边界:", who_near(e1))
print("e2 300m内乡镇边界:", who_near(e2))

# 蓝线中心线到 S 的整体情况：采样蓝线，到S距离分布
ds = [line.distance(Point(c)) for c in line.coords]
ds = np.array(ds)
print(f"中心线各点到S距离: min={ds.min():.0f} 中位={np.median(ds):.0f} max={ds.max():.0f} m")
# 蓝线与S交点数
inter = line.intersection(S)
n_inter = sum(1 for _ in (inter.geoms if hasattr(inter, "geoms") else [inter])) if not inter.is_empty else 0
print(f"中心线与S交点数: {n_inter}")

# 蓝线中点在S哪一侧：取蓝线中点，判断在A内还是B内
mid = line.interpolate(0.5, normalized=True)
print(f"蓝线中点 {tuple(round(c) for c in mid.coords[0])}: A内={A.covers(mid)} B内={B.covers(mid)}")

# ---- 可视化：裁剪区放大，画 中心线(绿) + A(橙) B(红) ----
vis = img.copy()
def draw(geom, color, th=2):
    geoms = geom.geoms if hasattr(geom, "geoms") else [geom]
    for p in geoms:
        if p.geom_type == "Polygon":
            rings = [p.exterior] + list(p.interiors)
        else:
            rings = [p]
        for r in rings:
            cc = np.asarray(r.coords)
            pi = np.stack([(cc[:, 0] - minx) / 30.0, (maxy - cc[:, 1]) / 30.0], 1).astype(np.int32)
            cv2.polylines(vis, [pi], p.geom_type == "LinearRing" or p.geom_type == "Polygon", color, th, cv2.LINE_AA)
draw(A, (255, 128, 0))
draw(B, (0, 0, 255))
draw(line, (0, 200, 0), 2)
bys2, bxs2 = np.where(d <= 90)
x0, x1 = max(0, bxs2.min()-250), min(W, bxs2.max()+250)
y0, y1 = max(0, bys2.min()-250), min(H, bys2.max()+250)
crop = vis[y0:y1, x0:x1]
crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
cv2.imencode(".png", crop)[1].tofile(os.path.join(OUT, "_diag_centerline.png"))
print("输出 _diag_centerline.png")
