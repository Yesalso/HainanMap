# -*- coding: utf-8 -*-
"""QA出图 + 数值验证：新界线与 #3F48CC 弧线的贴合度。"""
import numpy as np, cv2, geopandas as gpd, os, io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.ops import unary_union, linemerge
from shapely.geometry import LineString, Point

SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
IMG = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change/Sanya_no_label.png"
OUT = r"D:/Windows/Documents/海南省村界/海南省村界/town/wenchang/三亚/change"
UTM = "EPSG:32649"

g = gpd.read_file(SHP, encoding="utf-8")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy().to_crs(UTM)
T = {t: geom.buffer(0) for t, geom in zip(s["TOWN"], s.geometry)}
A, B = T["荔枝沟区"], T["牛岭乡"]
S_new = linemerge(A.boundary.intersection(B.boundary))
minx, miny, maxx, maxy = s.total_bounds
print(f"调整后：荔枝沟 {A.area/1e6:.3f} km²，牛岭 {B.area/1e6:.3f} km²")

# 蓝弧中心线（同 adjust 脚本）
img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
bgr = np.array([204, 72, 63], np.int16)
d = np.abs(img.astype(np.int16) - bgr).sum(axis=2)
mask = cv2.dilate((d <= 90).astype(np.uint8), np.ones((3, 3), np.uint8), 1)
cols = {}
for j, i in zip(*np.where(mask > 0)):
    cols.setdefault(i, []).append(j)
xs = sorted(cols)
L = LineString([(minx + i * 30.0, maxy - np.mean(cols[i]) * 30.0) for i in xs]) \
    .simplify(10, preserve_topology=True)

# 贴合度：沿蓝弧采样到新共享边界的距离
n = max(int(L.length / 50), 1)
ds = np.array([L.interpolate(min(k * 50.0, L.length)).distance(S_new) for k in range(n + 1)])
print(f"蓝弧各点到新荔-牛界距离: 中位={np.median(ds):.1f} 平均={ds.mean():.1f} "
      f"P95={np.percentile(ds,95):.1f} max={ds.max():.1f} m")

# 出图
def save_png(im, path):
    with open(path, "wb") as f:
        f.write(cv2.imencode(".png", im)[1].tobytes())

# overlay：手绘图 + 新荔-牛界（红）+ 荔/牛轮廓
ov = img.copy()
def draw(geom, color, th=2, close=True):
    ps = geom.geoms if hasattr(geom, "geoms") else [geom]
    for p in ps:
        rings = [p.exterior] + list(p.interiors) if p.geom_type == "Polygon" else [p]
        for r in rings:
            c = np.asarray(r.coords)
            pi = np.stack([(c[:, 0] - minx) / 30.0, (maxy - c[:, 1]) / 30.0], 1).astype(np.int32)
            cv2.polylines(ov, [pi], close, color, th, cv2.LINE_AA)
draw(S_new, (0, 0, 255), 3, close=False)
draw(A, (0, 200, 0), 1, close=True)
draw(B, (255, 128, 0), 1, close=True)
save_png(ov, os.path.join(OUT, "调整后_overlay.png"))

# 渲染修改后的三亚图（同 Sanya_map.py 参数）
ss = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy().to_crs(UTM)
ss["geometry"] = ss.geometry.buffer(0).simplify(5, preserve_topology=True)
w_px, h_px = int(round((maxx - minx) / 30)), int(round((maxy - miny) / 30))
fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=100)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
ax.set_aspect("equal"); ax.axis("off"); ax.set_facecolor("white")
boundary = unary_union([gg.boundary for gg in ss.geometry])
gpd.GeoSeries([boundary], crs=ss.crs).plot(ax=ax, color="black", linewidth=1.44, antialiased=True)
buf = io.BytesIO()
fig.savefig(buf, dpi=100, pad_inches=0, facecolor="white")
plt.close(fig); buf.seek(0)
arr = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
save_png(np.hstack([img, arr]), os.path.join(OUT, "调整后_sidebyside.png"))

# 局部放大 QA：蓝弧区 overlay
crop = ov[1080:1330, 1820:2250]
crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
save_png(crop, os.path.join(OUT, "_qa_overlay_crop.png"))
print("输出 调整后_overlay.png / 调整后_sidebyside.png / _qa_overlay_crop.png")
