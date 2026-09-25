# -*- coding: utf-8 -*-
"""最终验证 + 前后对比预览图
1. 崖城-梅山共享边检查（之前的重灾区）
2. 残余 3 处两镇间窄带详情
3. 被移除的冗余描边线定位（新线网 1m 内不存在的旧线 = 被消除的双线）
4. 放大对比图 / 全岛变化图 / 全岛 60m/px 新底图
只读 shp，只写 PNG。
"""
import json
import numpy as np
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shapely.ops import unary_union, polygonize
from shapely import STRtree
from shapely.geometry import box

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
SRC = BASE + r"\Hainan_town_codefix.shp"
DST = BASE + r"\Hainan_town_topo.shp"

a = gpd.read_file(SRC, encoding="utf-8").to_crs("EPSG:32649")
b = gpd.read_file(DST, encoding="utf-8").to_crs("EPSG:32649")
a["geometry"] = a.geometry.buffer(0)
b["geometry"] = b.geometry.buffer(0)
polys_a, polys_b = list(a.geometry), list(b.geometry)
names = a["TOWN"].astype(str).tolist()

def width_of(f):
    bl = f.boundary.length
    return 2.0 * f.area / bl if bl > 0 else 0.0

net_old = unary_union([g.boundary for g in polys_a])
net_new = unary_union([g.boundary for g in polys_b])
lines_old = list(net_old.geoms) if net_old.geom_type == "MultiLineString" else [net_old]
lines_new = list(net_new.geoms) if net_new.geom_type == "MultiLineString" else [net_new]

# ---------- 1. 崖城-梅山 ----------
for pair in [(51, 56), (180, 265)]:   # 崖城-梅山, 抱板-七叉
    i, j = pair
    old_sh = polys_a[i].boundary.intersection(polys_a[j].boundary).length
    new_sh = polys_b[i].boundary.intersection(polys_b[j].boundary).length
    print(f"{names[i]}-{names[j]}: 共享边 修复前 {old_sh:,.0f} m → 修复后 {new_sh:,.0f} m，"
          f"touches={polys_b[i].touches(polys_b[j])}")

# ---------- 2. 残余两镇间窄带 ----------
tree_b = STRtree(polys_b)
resid = []
for f in polygonize(lines_new):
    if f.area < 1.0 or not (25.0 <= width_of(f) <= 100.0):
        continue
    tt = {int(i) for i in tree_b.query(f, predicate="touches")}
    if len(tt) >= 2:
        resid.append((f, tt))
print(f"\n残余两镇间窄带 {len(resid)} 处：")
for f, tt in resid:
    cov = {names[i]: polys_b[i].intersection(f).area / f.area for i in tt}
    print(f"  宽 {width_of(f):.0f}m 面 {f.area:,.0f}m² towns={sorted(tt and [names[i] for i in tt])} cov={cov}")
    print(f"    bounds={[round(v) for v in f.bounds]}")

# ---------- 3. 被移除的冗余线 ----------
tree_n = STRtree(lines_new)
removed = []
for l in lines_old:
    near = tree_n.query(l, predicate="dwithin", distance=1.0)
    if len(near) == 0:
        removed.append(l)
rem_len = sum(l.length for l in removed)
print(f"\n被消除的冗余描边线：{len(removed)} 条，总长 {rem_len:,.0f} m")
# 更新报告
rep_path = BASE + r"\fix_report.json"
try:
    rep = json.load(open(rep_path, encoding="utf-8"))
    rep["removed_duplicate_segments"] = len(removed)
    rep["removed_duplicate_length_m"] = rem_len
    json.dump(rep, open(rep_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
except Exception as e:
    print("更新报告失败:", e)

# 聚类成热点（200m 缓冲溶解）
if removed:
    hot = unary_union([l.buffer(200) for l in removed])
    comps = list(hot.geoms) if hot.geom_type in ("MultiPolygon", "GeometryCollection") else [hot]
    sites = []
    for c in comps:
        if c.geom_type != "Polygon":
            continue
        inside = [l for l in removed if c.buffer(10).intersects(l)]
        tl = sum(l.length for l in inside)
        if tl > 300:
            sites.append((tl, c))
    sites.sort(reverse=True)
    print(f"冗余线热点 {len(sites)} 处，前 6 处（消除长度/位置）：")
    for tl, c in sites[:6]:
        print(f"   {tl:8,.0f} m  bounds={[round(v) for v in c.bounds]}")

# ---------- 4a. 放大对比图 ----------
def draw_net(ax, net, extent, color="black", removed=None):
    ax.set_xlim(extent[0], extent[2]); ax.set_ylim(extent[1], extent[3])
    ax.set_facecolor("white"); ax.set_aspect("equal"); ax.axis("off")
    bb = box(extent[0], extent[1], extent[2], extent[3])
    # BEFORE：旧线网（灰）+ 被消除的冗余线（红）
    geoms = net.geoms if net.geom_type == "MultiLineString" else [net]
    sel = [l for l in geoms if bb.intersects(l)]
    gpd.GeoSeries(sel, crs=b.crs).plot(ax=ax, color="0.7", linewidth=0.7)
    if removed is not None:
        rsel = [l for l in removed if bb.intersects(l)]
        gpd.GeoSeries(rsel, crs=b.crs).plot(ax=ax, color="red", linewidth=1.2)
    else:
        gpd.GeoSeries(sel, crs=b.crs).plot(ax=ax, color=color, linewidth=0.9)

# 展示位点：热点前 3 + 崖-梅重灾段
spots = [(tl, c.bounds) for tl, c in sites[:3]]
spots.append((0, (301400, 2036300, 302300, 2037600)))   # 崖城-梅山解剖窗
titles = [f"hotspot {k+1} ({tl/1000:.1f} km removed)" for k, (tl, _) in enumerate(spots)]
titles[-1] = "Yacheng-Meishan shared edge"
n_show = len(spots)
fig, axes = plt.subplots(n_show, 2, figsize=(10, 4.6 * n_show), dpi=100)
if n_show == 1:
    axes = np.array([[axes[0], axes[1]]])
for k, (tl, bd) in enumerate(spots):
    cx, cy = (bd[0]+bd[2])/2, (bd[1]+bd[3])/2
    R = max(bd[2]-bd[0], bd[3]-bd[1])/2 + 1800
    ext = (cx-R, cy-R, cx+R, cy+R)
    draw_net(axes[k, 0], net_old, ext, removed=removed)
    draw_net(axes[k, 1], net_new, ext, color="black")
    axes[k, 0].set_title(f"BEFORE  {titles[k]}", fontsize=10)
    axes[k, 1].set_title("AFTER  single line", fontsize=10)
fig.tight_layout()
out = BASE + r"\topo_zoom_compare.png"
fig.savefig(out, dpi=150, facecolor="white"); plt.close(fig)
print(f"\n已保存 {out}")

# ---------- 4b. 全岛变化图：新线网 + 被消除的冗余线（红） ----------
minx, miny, maxx, maxy = net_new.bounds
fig, ax = plt.subplots(figsize=(12, 10), dpi=100)
ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
ax.set_facecolor("white"); ax.set_aspect("equal"); ax.axis("off")
gpd.GeoSeries([net_new], crs=b.crs).plot(ax=ax, color="0.75", linewidth=0.25)
if removed:
    gpd.GeoSeries(removed, crs=b.crs).plot(ax=ax, color="red", linewidth=0.4)
ax.set_title(f"Red = {rem_len/1000:,.1f} km of duplicated strokes removed "
             f"(gray = rebuilt single-line network)", fontsize=11)
out = BASE + r"\topo_change_map.png"
fig.savefig(out, dpi=100, facecolor="white", bbox_inches="tight"); plt.close(fig)
print(f"已保存 {out}")

# ---------- 4c. 全岛 60m/px 新底图 ----------
PX = 60
w_px = int(round((maxx-minx)/PX)); h_px = int(round((maxy-miny)/PX))
fig = plt.figure(figsize=(w_px/100, h_px/100), dpi=100)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
ax.set_facecolor("white"); ax.set_aspect("equal"); ax.axis("off")
gpd.GeoSeries([net_new], crs=b.crs).plot(ax=ax, color="black",
                                         linewidth=0.5*72/100, antialiased=True)
out = BASE + r"\Hainan_town_topo_no_label.png"
fig.savefig(out, dpi=100, pad_inches=0, bbox_inches=None, facecolor="white"); plt.close(fig)
print(f"已保存 {out}")
print("完成")
