# -*- coding: utf-8 -*-
"""最终校验：全三亚严格分区（无缝隙/无重叠/面积守恒）+ 邻接关系检查。"""
import geopandas as gpd, numpy as np
from shapely.ops import unary_union

SHP = r"D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo.shp"
g = gpd.read_file(SHP, encoding="utf-8")
print(f"全文件：{len(g)} 要素，非法几何 {int((~g.geometry.is_valid).sum())}")
s = g[g["CITY"].astype(str).str.contains("三亚", na=False)].copy().to_crs("EPSG:32649")
geoms = list(s.geometry)
sum_a = sum(x.area for x in geoms) / 1e6
u = unary_union(geoms).buffer(0).area / 1e6
print(f"三亚 {len(geoms)} 乡镇：面积和 {sum_a:.4f} km²，并 {u:.4f} km²，差 {abs(sum_a-u)*1e6:.2f} m²")
ov_max, ov_cnt = 0.0, 0
for i in range(len(geoms)):
    for j in range(i + 1, len(geoms)):
        if geoms[i].intersects(geoms[j]):
            a = geoms[i].intersection(geoms[j]).area
            if a > 1e-6:
                ov_cnt += 1; ov_max = max(ov_max, a)
print(f"两两重叠：{ov_cnt} 处，最大 {ov_max:.3f} m²")
# 荔枝沟/牛岭 与各邻镇的公共边界长度
A = s[s["TOWN"] == "荔枝沟区"].geometry.iloc[0].buffer(0)
B = s[s["TOWN"] == "牛岭乡"].geometry.iloc[0].buffer(0)
for nm, X in [("荔枝沟区", A), ("牛岭乡", B)]:
    nb = []
    for _, r in s.iterrows():
        if r["TOWN"] == nm:
            continue
        L = X.boundary.intersection(r.geometry.boundary).length
        if L > 0:
            nb.append((r["TOWN"], round(L)))
    print(f"{nm} 邻接：{sorted(nb, key=lambda t: -t[1])}")
print(f"FIELD检查 AREA_KM2: ",
      s[s["TOWN"].isin(["荔枝沟区", "牛岭乡"])][["TOWN", "AREA_KM2"]].to_string(index=False))
