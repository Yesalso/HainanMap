# -*- coding: utf-8 -*-
"""验证：修复前后交界节点放大对比图 + 三亚全貌检查"""
import geopandas as gpd
import shapely
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"

def load(p):
    g = gpd.read_file(p, encoding="utf-8")
    return g.to_crs("EPSG:32649")

a = load(BASE + r"\Hainan_town_topo.shp")   # 修复前
b = load(BASE + r"\Hainan_town_topo_v3.shp")  # 修复后
a["geometry"] = a.geometry.buffer(0)
b["geometry"] = b.geometry.buffer(0)

SHOW = [(342003, 2017010, "河西/河东"),
        (301714, 2036588, "崖城/梅山/保港"),
        (342945, 2016191, "南海/河东/鹿回头"),
        (347804, 2022645, "牛岭/荔枝沟/红沙")]

for cx, cy, tag in SHOW:
    c = shapely.Point(cx, cy)
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.5), dpi=110)
    for ax, gdf, title in [(axes[0], a, "before"), (axes[1], b, "after")]:
        half = 260
        for i in range(len(gdf)):
            if gdf.geometry[i].distance(c) > 1500:
                continue
            gdf.geometry[[i]].plot(ax=ax, facecolor="none",
                                   edgecolor=plt.cm.tab10(i % 10), linewidth=1.6)
            rp = gdf.geometry[i].representative_point()
            if rp.distance(c) < 1500:
                ax.annotate(gdf.iloc[i]["TOWN"], (rp.x, rp.y),
                            color=plt.cm.tab10(i % 10), fontsize=11, ha="center")
        # 顶点标记
        for i in range(len(gdf)):
            if gdf.geometry[i].distance(c) > 800:
                continue
            parts = gdf.geometry[i].geoms if hasattr(gdf.geometry[i], "geoms") else [gdf.geometry[i]]
            for pt in parts:
                v = np.array(pt.exterior.coords)
                d = np.hypot(v[:, 0] - cx, v[:, 1] - cy)
                vv = v[d < half]
                if len(vv):
                    ax.plot(vv[:, 0], vv[:, 1], ".", color=plt.cm.tab10(i % 10),
                            markersize=6, alpha=0.7)
        ax.set_xlim(cx - half, cx + half)
        ax.set_ylim(cy - half, cy + half)
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)
        ax.plot(cx, cy, "r+", markersize=14)
        enc = "修复前" if title == "before" else "修复后"
        ax.set_title(f"{enc} {tag}")
    fig.tight_layout()
    fn = BASE + rf"\sanya_junction_fix_{tag.replace('/', '_')}.png"
    fig.savefig(fn, bbox_inches="tight")
    plt.close(fig)
    print(f"已保存 {fn}")

# 三亚全貌（修复后）
fig, ax = plt.subplots(figsize=(11, 13), dpi=100)
b[b["CITY"].astype(str).str.contains("三亚")].plot(
    ax=ax, facecolor="#eef5ee", edgecolor="black", linewidth=1.0)
ax.set_aspect("equal")
ax.set_title("Sanya (Hainan_town_topo_v3)")
fig.savefig(BASE + r"\sanya_v3_overview.png", bbox_inches="tight")
plt.close(fig)
print("已保存 sanya_v3_overview.png")

# 修复后拓扑指标
sm = b[b["CITY"].astype(str).str.contains("三亚")]
net = shapely.set_precision(shapely.ops.unary_union([g.boundary for g in sm.geometry]), 2.0)
from shapely.ops import unary_union, polygonize
net = unary_union([g for g in (net.geoms if net.geom_type == "MultiLineString" else [net]) if g.length > 0])
lines = net.geoms if net.geom_type == "MultiLineString" else [net]
faces = [f for f in polygonize(lines) if f.area >= 1.0]
print(f"\n[修复后] 三亚逐面总长 {sum(g.boundary.length for g in sm.geometry):,.0f} m | "
      f"线网 {net.length:,.0f} m | 面片 {len(faces)}")
