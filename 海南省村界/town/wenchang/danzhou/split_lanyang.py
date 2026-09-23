# -*- coding: utf-8 -*-
"""
split_lanyang.py —— 把兰洋镇（现行层自带的 MultiPolygon 两块）拆成：
  北块 → 兰洋镇（沿用，469003105）
  南块 → 番加乡（2002新增，469003122）
用法：
  python split_lanyang.py
"""
from __future__ import annotations

import os

import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))

CRS = "EPSG:32649"
NORTH_CODE = "469003105"
NORTH_NAME = "兰洋镇"
SOUTH_CODE = "469003122"
SOUTH_NAME = "番加乡"


def parts_of(g):
    return list(g.geoms) if g.geom_type == "MultiPolygon" else [g]


def split_one(path):
    gdf = gpd.read_file(path, encoding="utf-8")
    rows = []
    for _, r in gdf.iterrows():
        if str(r["CODE"]) == NORTH_CODE and r["TOWN"] == NORTH_NAME:
            parts = sorted(parts_of(r.geometry), key=lambda p: p.centroid.y, reverse=True)
            if len(parts) < 2:
                print(f"  ⚠ {path}: 兰洋镇只有 {len(parts)} 块，跳过拆分")
                rows.append(r)
                continue
            north, south = parts[0], parts[1]
            base = dict(CITY=r["CITY"], EN=r["EN"], N_FEAT=r["N_FEAT"])
            rows.append({**base, "CODE": NORTH_CODE, "TOWN": NORTH_NAME,
                         "SOURCE": "沿用", "geom": north})
            rows.append({**base, "CODE": SOUTH_CODE, "TOWN": SOUTH_NAME,
                         "SOURCE": "2002新增", "N_FEAT": 0, "geom": south})
        else:
            rows.append(dict(r.drop("geometry")))
            rows[-1]["geom"] = r.geometry
    out = gpd.GeoDataFrame(
        {"CODE": [x["CODE"] for x in rows], "TOWN": [x["TOWN"] for x in rows],
         "CITY": [x.get("CITY", "") for x in rows], "EN": [x.get("EN", "") for x in rows],
         "N_FEAT": [x.get("N_FEAT", 0) or 0 for x in rows],
         "SOURCE": [x["SOURCE"] for x in rows]},
        geometry=[x["geom"] for x in rows], crs=gdf.crs)
    out["AREA_KM2"] = (out.geometry.area / 1e6).round(3)
    return out


def clean(base):
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx"):
        p = base + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    for tag in ("danzhou2002", "danzhou2002_fixed"):
        shp = os.path.join(HERE, tag + ".shp")
        if not os.path.exists(shp):
            continue
        gdf = split_one(shp)
        clean(os.path.join(HERE, tag))
        gdf.to_file(os.path.join(HERE, tag + ".shp"), encoding="utf-8")
        src = gpd.read_file(shp, encoding="utf-8").crs
        if str(src) != CRS:
            gdf.to_crs(src).to_file(os.path.join(HERE, tag + "_Albers.shp"), encoding="utf-8")
        print(f"  ✓ {tag}.shp：{len(gdf)} 个要素")
        sub = gdf[gdf["TOWN"].isin(["兰洋镇", "番加乡"])]
        print(sub[["CODE", "TOWN", "SOURCE", "AREA_KM2"]].to_string(index=False))
    print("DONE")