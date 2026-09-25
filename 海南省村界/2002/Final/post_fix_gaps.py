# -*- coding: utf-8 -*-
"""收尾补丁：吸收新数据中残余的 3 处两镇间无主窄带（数字化缝隙，非真实飞地）"""
import os
import json
import numpy as np
import geopandas as gpd
from shapely.ops import unary_union, polygonize
from shapely import STRtree

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final"
DST = BASE + r"\Hainan_town_topo.shp"

gdf = gpd.read_file(DST, encoding="utf-8")
orig_crs = gdf.crs
gdf = gdf.to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
polys = list(gdf.geometry)
tree = STRtree(polys)

net = unary_union([g.boundary for g in polys])
lines = net.geoms if net.geom_type == "MultiLineString" else [net]

def width_of(f):
    bl = f.boundary.length
    return 2.0 * f.area / bl if bl > 0 else 0.0

patches = {}
n_fix = 0
for f in polygonize(lines):
    if f.area < 1.0 or f.area >= 5e5 or width_of(f) >= 150.0:
        continue
    cand = tree.query(f)
    if len(cand) == 0:
        continue
    cover = max(polys[i].intersection(f).area for i in cand)
    if cover > 0.01 * f.area:
        continue    # 有主，不是缝隙
    # 无主窄带 → 吸收进共享边最长的邻镇（新数据拓扑干净，touches 精确可用）
    scored = [(polys[i].intersection(f).length, int(i))
              for i in tree.query(f, predicate="touches")]
    if not scored:
        continue
    _, best = max(scored)
    patches.setdefault(best, []).append(f)
    n_fix += 1
    print(f"吸收缝隙：宽 {width_of(f):.0f}m 面 {f.area:,.0f}m² → {gdf.iloc[best]['TOWN']} "
          f"bounds={[round(v) for v in f.bounds]}")

if patches:
    for i, fs in patches.items():
        polys[i] = unary_union([polys[i]] + fs)
    gdf["geometry"] = polys
    gdf["geometry"] = gdf.geometry.buffer(0)
    if "AREA_KM2" in gdf.columns:
        gdf["AREA_KM2"] = (gdf.geometry.area / 1e6).round(4)

    # 复检
    polys2 = list(gdf.geometry)
    tree2 = STRtree(polys2)
    net2 = unary_union([g.boundary for g in polys2])
    lines2 = net2.geoms if net2.geom_type == "MultiLineString" else [net2]
    resid = 0
    for f in polygonize(lines2):
        if f.area < 1.0 or f.area >= 5e5 or width_of(f) >= 150.0:
            continue
        cand = tree2.query(f)
        if len(cand) == 0:
            continue
        if max(polys2[i].intersection(f).area for i in cand) < 0.01 * f.area:
            resid += 1
    print(f"补丁后残余无主窄带：{resid} 处")

    # 写回（临时 + os.replace，沙箱安全）
    SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
    tmp = DST[:-4] + "__tmp.shp"
    gdf.to_crs(orig_crs).to_file(tmp, encoding="utf-8")
    for ext in SIDECARS:
        s = tmp[:-4] + ext
        if os.path.exists(s):
            os.replace(s, DST[:-4] + ext)
    print(f"✅ 已更新 {DST}（原文件 {BASE + chr(92) + 'Hainan_town_codefix.shp'} 仍未触碰）")

    rep_path = BASE + r"\fix_report.json"
    rep = json.load(open(rep_path, encoding="utf-8"))
    rep["gap_patches_final"] = n_fix
    rep["residual_inter_town_bands"] = resid
    json.dump(rep, open(rep_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
else:
    print("无残余缝隙需要处理")
