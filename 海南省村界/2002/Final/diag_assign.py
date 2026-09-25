# -*- coding: utf-8 -*-
"""核对输出文件与原文件的逐镇面积，并追查异常镇的分配去向"""
import geopandas as gpd
from shapely.ops import unary_union, polygonize
from shapely import STRtree

SRC = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_codefix.shp"
DST = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"

a = gpd.read_file(SRC, encoding="utf-8").to_crs("EPSG:32649")
b = gpd.read_file(DST, encoding="utf-8").to_crs("EPSG:32649")
a["geometry"] = a.geometry.buffer(0)
b["geometry"] = b.geometry.buffer(0)

m = a[["CODE", "TOWN"]].copy()
m["旧面积"] = a.geometry.area.values / 1e6
m = m.merge(b[["CODE"]].assign(新面积=b.geometry.area.values / 1e6), on="CODE")
m["差"] = m["新面积"] - m["旧面积"]
print(m.reindex(m["差"].abs().sort_values(ascending=False).index).head(12).to_string(index=False))

# 追查：抱板乡范围内面片的分配过程
polys = list(a.geometry)
tree = STRtree(polys)
net = unary_union([g.boundary for g in polys])
lines = net.geoms if net.geom_type == "MultiLineString" else [net]
faces = [f for f in polygonize(lines) if f.area >= 1.0]

for tname in ["抱板乡", "崖城镇", "梅山镇"]:
    idx = a.index[a["TOWN"].astype(str).str.contains(tname)][0]
    g = polys[idx]
    print(f"\n=== {tname} idx={idx} 面积 {g.area/1e6:.2f} km² ===")
    # 找与该镇重叠 >10 km² 的面片，看它们被分给了谁
    cnt = 0
    for f in faces:
        ia = g.intersection(f).area
        if ia > 10e6:  # >10 km² 的重叠
            # 重演分配逻辑
            best_i, best_a = -1, 0.0
            for j in tree.query(f):
                ja = polys[j].intersection(f).area
                if ja > best_a:
                    best_a, best_i = ja, j
            print(f"  面片 {f.area/1e6:7.2f} km², 与{tname}重叠 {ia/1e6:6.2f} km² "
                  f"({ia/f.area*100:5.1f}%), 最佳归属={a.loc[best_i,'TOWN']} "
                  f"({best_a/f.area*100:5.1f}%), bbox=({f.bounds[0]:.0f},{f.bounds[1]:.0f})")
            cnt += 1
            if cnt >= 5:
                break
