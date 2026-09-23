# -*- coding: utf-8 -*-
"""
apply_jiaji_fix.py —— 琼海人工修正（在 fix_partition 结果之上）
  1) 嘉积镇(469002100) 是东块 35.85 + 中块 7.60 两块不连通；
     东块 → 新要素「上埇乡」(469002113)，中块留在「嘉积镇」。
  2) 原「上埇乡」块(469002113, 18.24km²) → 并入现行「中原镇」(469002103)。
  3) 泮水乡、温泉镇 维持不变。
"""
from __future__ import annotations
import os
import geopandas as gpd
from shapely.ops import unary_union
from pyproj import CRS

HERE = os.path.dirname(os.path.abspath(__file__))
FIXED = os.path.join(HERE, "qionghai2002_fixed.shp")
CHUNK = os.path.join(HERE, "qionghai.shp")
OUT = os.path.join(HERE, "qionghai2002_final")
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".geojson")


def parts(g):
    return list(g.geoms) if g.geom_type.startswith("Multi") else [g]


def clean(g, min_hole_m2=1.0):
    """去掉退化内环（面积 < min_hole_m2），避免 union 产生的 0 m² 空洞。"""
    from shapely.geometry import Polygon, MultiPolygon
    out = []
    for p in parts(g):
        ints = [r for r in p.interiors if Polygon(r).area >= min_hole_m2]
        out.append(Polygon(p.exterior, ints))
    return out[0] if len(out) == 1 else MultiPolygon(out)


def log(m=""):
    print(m, flush=True)


def main():
    fx = gpd.read_file(FIXED, encoding="utf-8")
    src_crs = gpd.read_file(CHUNK, encoding="utf-8").crs
    code = fx["CODE"].astype(str)

    jj = fx[code == "469002100"].geometry.iloc[0]
    ps = sorted(parts(jj), key=lambda p: -p.area)
    east, central = ps[0], unary_union(ps[1:])
    log(f"嘉积镇原 {len(ps)} 块：东块 {east.area/1e6:.3f} km²，中块 {central.area/1e6:.3f} km²")

    sy = fx[code == "469002113"].geometry.iloc[0]
    zy = fx[code == "469002103"].geometry.iloc[0]
    zy_new = clean(unary_union([zy, sy]).buffer(0))
    log(f"原上埇乡块 {sy.area/1e6:.3f} km² 并入中原镇：{zy.area/1e6:.3f} → {zy_new.area/1e6:.3f} km²")

    rows = []
    for _, r in fx.iterrows():
        c = str(r["CODE"])
        if c == "469002113":
            continue
        g = r.geometry
        if c == "469002100":
            g = central
        elif c == "469002103":
            g = zy_new
        rows.append(dict(CODE=c, TOWN=r["TOWN"], CITY=r["CITY"],
                         EN=r.get("EN", ""), N_FEAT=int(r.get("N_FEAT", 0) or 0),
                         SOURCE=r["SOURCE"], geom=clean(g)))
    rows.append(dict(CODE="469002113", TOWN="上埇乡", CITY="琼海市",
                     EN="", N_FEAT=0, SOURCE="2002新增", geom=east))

    gdf = gpd.GeoDataFrame(
        {"CODE": [r["CODE"] for r in rows], "TOWN": [r["TOWN"] for r in rows],
         "CITY": [r["CITY"] for r in rows], "EN": [r["EN"] for r in rows],
         "N_FEAT": [r["N_FEAT"] for r in rows], "SOURCE": [r["SOURCE"] for r in rows]},
        geometry=[r["geom"] for r in rows], crs=fx.crs)
    gdf["AREA_KM2"] = (gdf.geometry.area / 1e6).round(3)

    # ---- 校验 ----
    ref = gpd.read_file(CHUNK).to_crs(fx.crs)
    tot = gdf.geometry.area.sum() / 1e6
    cur = ref.geometry.area.sum() / 1e6
    ua = unary_union(list(gdf.geometry)).buffer(0).area / 1e6
    mx = 0.0
    geoms = list(gdf.geometry)
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if geoms[i].intersects(geoms[j]):
                mx = max(mx, geoms[i].intersection(geoms[j]).area)
    log(f"面积：现行 {cur:.4f} 结果 {tot:.4f} 并 {ua:.4f} 重叠 {(tot-ua):.6f} km²")
    log(f"两两最大重叠 {mx:.4f} m²  非法几何 {int((~gdf.geometry.is_valid).sum())}")

    for ext in SIDECARS:
        p = OUT + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    gdf.to_file(OUT + ".shp", encoding="utf-8")
    gdf.to_crs(CRS.from_user_input(src_crs)).to_file(OUT + "_Albers.shp", encoding="utf-8")
    log(gdf[["CODE", "TOWN", "SOURCE", "AREA_KM2"]].to_string(index=False))
    log(f"写出：{OUT}.shp / {OUT}_Albers.shp")


if __name__ == "__main__":
    main()
