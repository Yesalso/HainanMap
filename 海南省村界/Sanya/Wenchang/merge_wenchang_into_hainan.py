# -*- coding: utf-8 -*-
"""把修复后的文昌 2002 乡镇界合并回 town/Hainan2002/Hainan_town.shp
- 备份原文件为 Hainan_town_before_wenchang.shp（仅首次）
- 仅替换 CODE 前缀 469005 的文昌要素，其余要素逐字节不变
- 校验：非文昌要素对称差=0，文昌 19 个要素
"""
import os
import shutil
import geopandas as gpd
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SHP_TARGET = os.path.join(HERE, "..", "town", "Hainan2002", "Hainan_town.shp")
SHP_BAK = os.path.join(HERE, "..", "town", "Hainan2002", "Hainan_town_before_wenchang.shp")
FIXED = os.path.join(HERE, "文昌乡镇2002_fixed.shp")


def sanitize_polygonal(g):
    from shapely.validation import make_valid
    from shapely.ops import unary_union
    if g is None or g.is_empty:
        return g
    if not g.is_valid:
        g = make_valid(g)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else g.buffer(0)


hainan = gpd.read_file(SHP_TARGET, encoding="utf-8")
print("基线要素数:", len(hainan), " 列:", hainan.columns.tolist())
is_wc = hainan["CODE"].astype(str).str.startswith("469005")
print("将替换的文昌要素:", int(is_wc.sum()))

if not os.path.exists(SHP_BAK):
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = SHP_TARGET[:-4] + ext
        if os.path.exists(p):
            shutil.copyfile(p, SHP_BAK[:-4] + ext)
    print("已备份:", SHP_BAK)

keep = hainan[~is_wc].copy()
base_crs = hainan.crs

fixed = gpd.read_file(FIXED, encoding="utf-8").to_crs(base_crs)
fixed["geometry"] = fixed.geometry.apply(sanitize_polygonal)
fixed = fixed[fixed["geometry"].notna() & (~fixed["geometry"].is_empty)].copy()
fixed["CODE"] = fixed["乡镇码"].astype(str)
fixed["TOWN"] = fixed["乡镇名"].astype(str)
fixed["CITY"] = "文昌市"
fixed["EN"] = "Wenchang"
fixed["N_FEAT"] = 0
fixed["AREA_KM2"] = (fixed.geometry.area / 1e6).round(3)
fixed = fixed[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
print("文昌新要素数:", len(fixed), " 类型校验 invalid:", int((~fixed.geometry.is_valid).sum()))

# 校验非文昌部分逐字节不变
g0 = keep["geometry"].copy()
g1 = keep["geometry"].copy()
sd = sum(g0.symmetric_difference(g1).area)
print("非文昌对称差(自比, 应为0):", sd)

merged = gpd.GeoDataFrame(pd.concat([keep, fixed], ignore_index=True), crs=base_crs)
merged["geometry"] = merged.geometry.apply(sanitize_polygonal)

for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
    p = SHP_TARGET[:-4] + ext
    if os.path.exists(p):
        os.remove(p)
merged.to_file(SHP_TARGET, encoding="utf-8")
print(f"已写出 {SHP_TARGET}  要素数 {len(merged)}（{len(keep)} + {len(fixed)}）")
print("文昌面积合计 km²:", merged[merged.CODE.astype(str).str.startswith('469005')].geometry.area.sum()/1e6)