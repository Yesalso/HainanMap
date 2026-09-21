# -*- coding: utf-8 -*-
"""一次性重建：backup(非文昌) + 文昌乡镇2002_fixed(裁剪回 base 陆域) -> Hainan_town.shp
（此前一次 clip 写盘因退化线要素失败留下损坏文件，此脚本保证原子重建）
"""
import os
import time
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from shapely.validation import make_valid

HERE = os.path.dirname(os.path.abspath(__file__))
SHP_TARGET = os.path.join(HERE, "..", "town", "Hainan2002", "Hainan_town.shp")
SHP_BAK = os.path.join(HERE, "..", "town", "Hainan2002", "Hainan_town_before_wenchang.shp")
FIXED = os.path.join(HERE, "文昌乡镇2002_fixed.shp")
T0 = time.time()


def sanitize_polygonal(g):
    if g is None or g.is_empty:
        return None
    if not g.is_valid:
        g = make_valid(g)
    if g.geom_type == "Polygon":
        return g
    if g.geom_type == "MultiPolygon":
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    if parts:
        return unary_union(parts)
    return g.buffer(0)


base = gpd.read_file(SHP_BAK, encoding="utf-8")
is_wc = base.CODE.astype(str).str.startswith("469005")
keep = base[~is_wc].copy()
base_outer = base[is_wc].dissolve().geometry[0]

fixed = gpd.read_file(FIXED, encoding="utf-8").to_crs(base.crs)
fixed["geometry"] = fixed.geometry.apply(sanitize_polygonal)
fixed = fixed[fixed.geometry.notna() & (~fixed.geometry.is_empty)].copy()
fixed["CODE"] = fixed["乡镇码"].astype(str)
fixed["TOWN"] = fixed["乡镇名"].astype(str)
fixed["CITY"] = "文昌市"
fixed["EN"] = "Wenchang"
fixed["N_FEAT"] = 0
fixed["AREA_KM2"] = (fixed.geometry.area / 1e6).round(3)
fixed = fixed[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]

wc = gpd.clip(fixed, base_outer).reset_index(drop=True)
covered = wc.dissolve().geometry[0]
missing = base_outer.difference(covered)
frags = list(missing.geoms) if missing.geom_type == "MultiPolygon" else [missing]
geoms = list(wc.geometry)
n_filled = 0
for f in frags:
    if f.area <= 0:
        continue
    fb = f.buffer(40, quad_segs=1)
    best, bo = -1, -1.0
    for i, g in enumerate(geoms):
        a = g.intersection(fb).area
        if a > bo:
            bo, best = a, i
    if best >= 0:
        geoms[best] = geoms[best].union(f)
        n_filled += 1
wc["geometry"] = [sanitize_polygonal(g) for g in geoms]
wc = wc[wc.geometry.notna() & (~wc.geometry.is_empty)].copy()
wc["AREA_KM2"] = (wc.geometry.area / 1e6).round(3)

final_union = wc.dissolve().geometry[0]
neighbor = keep.dissolve().geometry[0]
print(f"越界裁剪后：文昌∩邻县 = {final_union.intersection(neighbor).area/1e3:.6f} km²")
print(f"base文昌陆域缺口 = {base_outer.difference(final_union).area:.1f} m²")
print(f"文昌面积 = {final_union.area/1e6:.3f} / base {base_outer.area/1e6:.3f} km²  (补位 {n_filled} 块)")

merged = gpd.GeoDataFrame(pd.concat([keep, wc], ignore_index=True), crs=base.crs)
assert len(merged) == 247, f"要素数 {len(merged)}"
assert (merged.CODE.astype(str).str.startswith("469005")).sum() == 19

tmp = SHP_TARGET[:-4] + "_tmp"
for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
    p = tmp + ext
    if os.path.exists(p):
        os.remove(p)
merged.to_file(tmp, encoding="utf-8")
for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
    p = SHP_TARGET[:-4] + ext
    if os.path.exists(p):
        os.remove(p)
    if os.path.exists(tmp + ext):
        os.replace(tmp + ext, p)
chk = gpd.read_file(SHP_TARGET, encoding="utf-8")
print(f"重读校验 n={len(chk)} 非法={int((~chk.geometry.is_valid).sum())} "
      f"文昌={int(chk.CODE.astype(str).str.startswith('469005').sum())} 耗时{time.time()-T0:.1f}s")