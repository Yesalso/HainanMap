# -*- coding: utf-8 -*-
"""后处理：把合并进 Hainan_town.shp 的文昌新界裁剪回 base 文昌陆域外轮廓
（fix_slivers 以海南村界.shp 为参考，其县界与原 Hainan_town.shp 有微小差异，
  导致新文昌外沿与 base 有 ~0.05km² 越界。按"外轮廓永远以原 SHP 为权威"修正）
- excess = 新文昌 - base文昌陆域 → 裁掉
- deficit = base文昌陆域 - 新文昌 → buffer(40m) 归最近文昌乡镇（7620 个碎块批量）
"""
import os
import time
import geopandas as gpd
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SHP_TARGET = os.path.join(HERE, "..", "town", "Hainan2002", "Hainan_town.shp")
SHP_BAK = os.path.join(HERE, "..", "town", "Hainan2002", "Hainan_town_before_wenchang.shp")
t0 = time.time()

base = gpd.read_file(SHP_BAK, encoding="utf-8")
base_outer = base[base.CODE.astype(str).str.startswith("469005")].dissolve().geometry[0]
hainan = gpd.read_file(SHP_TARGET, encoding="utf-8")
is_wc = hainan.CODE.astype(str).str.startswith("469005")
keep = hainan[~is_wc].copy()
wc = hainan[is_wc].copy()

new_union = wc.dissolve().geometry[0]
excess = new_union.buffer(0).difference(base_outer).area
print(f"excess(应裁) = {excess/1e3:.3f} km²")

wc = gpd.clip(wc, base_outer).reset_index(drop=True)
covered = wc.dissolve().geometry[0]
missing = base_outer.difference(covered)
frags = list(missing.geoms) if missing.geom_type == "MultiPolygon" else [missing]
print(f"deficit 缺块 = {missing.area:.1f} m²，共 {len(frags)} 块")

geoms = list(wc.geometry)
res = []
for f in frags:
    if f.area <= 0:
        continue
    fb = f.buffer(40, quad_segs=1)
    best, bo = -1, -1.0
    for i, g in enumerate(geoms):
        a = g.intersection(fb).area
        if a > bo:
            bo, best = a, i
    res.append((best, f))
for i, f in res:
    if i >= 0:
        geoms[i] = geoms[i].union(f)
print(f"缺口补位 {len(res)} 块，耗时 {time.time()-t0:.1f}s")

wc["geometry"] = geoms
wc["AREA_KM2"] = (wc.geometry.area / 1e6).round(3)
final_union = wc.dissolve().geometry[0]
neighbor = keep.dissolve().geometry[0]
print(f"修正后 文昌∩邻县 = {final_union.intersection(neighbor).area/1e3:.6f} km²")
print(f"修正后 base陆域缺口 = {base_outer.difference(final_union).area:.1f} m²")
print(f"文昌面积 = {final_union.area/1e6:.3f} km² / base {base_outer.area/1e6:.3f} km²")

from shapely.ops import unary_union
from shapely.validation import make_valid

def sanitize_polygonal(g):
    if g is None or g.is_empty:
        return g
    if not g.is_valid:
        g = make_valid(g)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else g.buffer(0)

wc["geometry"] = wc.geometry.apply(sanitize_polygonal)
wc = wc[~wc.geometry.is_empty].copy()

merged = gpd.GeoDataFrame(pd.concat([keep, wc], ignore_index=True), crs=hainan.crs)
assert len(merged) == 247
for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
    p = SHP_TARGET[:-4] + ext
    if os.path.exists(p):
        os.remove(p)
merged.to_file(SHP_TARGET, encoding="utf-8")
print(f"已重写 {len(merged)} 要素；非法几何 {int((~merged.geometry.is_valid).sum())}")