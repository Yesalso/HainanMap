# -*- coding: utf-8 -*-
"""诊断4：蓝弧端点与三界交点/S端点的精确锚定关系；蓝弧沿程到S的距离。"""
import numpy as np, cv2, geopandas as gpd, os
from shapely.ops import unary_union
from shapely.geometry import LineString, Point

IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
OUT = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change"

g = gpd.read_file(SHP, encoding="utf-8")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy()
utm = s.to_crs("EPSG:32649")
minx, miny, maxx, maxy = utm.total_bounds
print(f"UTM bounds: x[{minx:.0f},{maxx:.0f}] y[{miny:.0f},{maxy:.0f}]")
img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]

bgr = np.array([204, 72, 63], np.int16)
d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
mask = (d <= 90).astype(np.uint8)
mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), 1)
ys, xs = np.where(mask > 0)
print(f"mask(膨胀后) x[{xs.min()},{xs.max()}] y[{ys.min()},{ys.max()}]")

cols = {}
for j, i in zip(ys, xs):
    cols.setdefault(i, []).append(j)
xs_s = sorted(cols)
line = LineString([(minx + i * 30.0, maxy - np.mean(cols[i]) * 30.0) for i in xs_s]) \
    .simplify(10, preserve_topology=True)
e1, e2 = Point(line.coords[0]), Point(line.coords[-1])
print(f"中心线 {line.length:.0f} m, e1={tuple(round(c) for c in e1.coords[0])}, "
      f"e2={tuple(round(c) for c in e2.coords[0])}")
print(f"e1 px=({(e1.x-minx)/30:.0f},{(maxy-e1.y)/30:.0f})  e2 px=({(e2.x-minx)/30:.0f},{(maxy-e2.y)/30:.0f})")

A = utm[utm["TOWN"] == "荔枝沟区"].geometry.union_all().buffer(0)
B = utm[utm["TOWN"] == "牛岭乡"].geometry.union_all().buffer(0)
U2 = unary_union([A, B]).buffer(0)
S = A.boundary.intersection(B.boundary)
segs = sorted(list(S.geoms), key=lambda p: p.coords[0][0]) if S.geom_type == "MultiLineString" else [S]
S_w, S_e = Point(segs[0].coords[0]), Point(segs[-1].coords[-1])
print(f"S 西端 {tuple(round(c) for c in S_w.coords[0])}, 东端 {tuple(round(c) for c in S_e.coords[0])}")

# 蓝弧与 S 的 along-S 距离：S 按 50m 采样
ds, ss = [], []
n = max(int(S.length / 50), 1)
for k in range(n + 1):
    pt = S.interpolate(min(k * 50.0, S.length))
    ss.append(k * 50.0)
    ds.append(pt.distance(line))
ds = np.array(ds)
idx = np.where(ds <= 150.0)[0]
if len(idx):
    print(f"S沿程到蓝弧距离≤150m 的区间: s=[{ss[idx.min()]:.0f}, {ss[idx.max()]:.0f}] m")
else:
    print(f"\n确认：S 全程距蓝弧 > 150m（min={ds.min():.0f} max={ds.max():.0f} 中位={np.median(ds):.0f}）")
    print(f"→ 画法语义 = 蓝弧整条替换荔-牛边界")

# 蓝弧端点与 U2 边界及其他乡镇三界交点
def junctions(r=120):
    """三界及以上交点：边界网络中度>=3的节点（近似：与其他乡镇边界两两交点）"""
    towns = {t: geom.buffer(0) for t, geom in zip(utm["TOWN"], utm.geometry)}
    out = {}
    names = list(towns)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            inter = towns[names[i]].boundary.intersection(towns[names[j]].boundary)
            if inter.is_empty:
                continue
            for p in (inter.geoms if hasattr(inter, "geoms") else [inter]):
                if p.geom_type == "Point":
                    out.setdefault((round(p.x), round(p.y)), set()).update([names[i], names[j]])
    return out

js = junctions()
print(f"\n三界交点共 {len(js)} 个（含两两交点聚类）")
for pt, ts in sorted(js.items()):
    if len(ts) >= 3 and ("荔枝沟区" in ts or "牛岭乡" in ts):
        p = Point(pt)
        d1, d2 = e1.distance(p), e2.distance(p)
        if d1 < 1500 or d2 < 1500:
            print(f"  {tuple(pt)} {'/'.join(sorted(ts))}  e1距={d1:.0f} e2距={d2:.0f}")

# 蓝弧沿程到 S 的距离曲线（沿蓝弧采样）
m = int(line.length / 50)
print("\n蓝弧沿程到S距离(每250m):")
for k in range(0, m + 1, 5):
    pt = line.interpolate(min(k * 50.0, line.length))
    print(f"  s={k*50:5.0f}m d={pt.distance(S):6.0f} m")
