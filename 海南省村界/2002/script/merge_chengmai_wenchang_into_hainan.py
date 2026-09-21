# -*- coding: utf-8 -*-
"""
把 2002/chengmai 与 2002/wenchang 的乡镇界线合并进
2002/hainan/Hainan_town_before_wenchang.shp，替换其中的澄迈县、文昌市。

做法：
  1. 读取全岛基线 Hainan_town_before_wenchang.shp；
  2. 删掉原澄迈县（CODE 469023 / CITY 澄迈县）与文昌市（CODE 469005 / CITY 文昌市）要素；
  3. 澄迈层从 EPSG:32649 重投影到全岛 Albers（CGCS2000 Albers），文昌层坐标系已一致；
  4. 两个县的要素插回它们在原文件中的位置，其余要素顺序不变；
  5. 输出为 2002/hainan/Hainan_town.shp（基线文件保留不动）。

校验：CRS 一致、几何有效、替换后与邻县不重叠。
"""

import os

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from shapely.validation import make_valid

HERE = os.path.dirname(os.path.abspath(__file__))
DIR_2002 = os.path.dirname(HERE)
HN_SHP = os.path.join(DIR_2002, "hainan", "Hainan_town_before_wenchang.shp")
OUT_SHP = os.path.join(DIR_2002, "hainan", "Hainan_town.shp")
CM_SHP = os.path.join(DIR_2002, "chengmai", "chengmai_v_g40_fixed.shp")
WC_SHP = os.path.join(DIR_2002, "wenchang", "wenchang2002_fixed_Albers.shp")

COLS = ["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
CM_CITY, WC_CITY = "澄迈县", "文昌市"
CM_EN, WC_EN = "Chengmai", "Wenchang"


def sanitize(g):
    if g is None or g.is_empty:
        return g
    if not g.is_valid:
        g = make_valid(g)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else g.buffer(0)


def load_layer(path, base_crs, city, en):
    g = gpd.read_file(path, encoding="utf-8").to_crs(base_crs)
    g["geometry"] = g.geometry.apply(sanitize)
    g = g[g.geometry.notna() & (~g.geometry.is_empty)].copy()
    g["CODE"] = g["CODE"].astype(str)
    g["TOWN"] = g["TOWN"].astype(str)
    g["CITY"] = city
    g["EN"] = g["EN"].fillna(en).replace("nan", en)
    g["N_FEAT"] = pd.to_numeric(g.get("N_FEAT"), errors="coerce").fillna(0).astype(int)
    return g[COLS].copy()


def is_county(df, prefix, city):
    return df["CODE"].astype(str).str.startswith(prefix) | (df["CITY"].astype(str) == city)


def replace_block(rows, mask, new_rows):
    """按原顺序逐行遍历，遇到目标县块时用 new_rows 整体替换。"""
    out, i, n = [], 0, len(rows)
    inserted = False
    while i < n:
        if mask[i]:
            if not inserted:
                out.extend(new_rows)
                inserted = True
            i += 1
        else:
            out.append(rows[i])
            i += 1
    return out, inserted


def check_overlap(merged, mask, label):
    other = unary_union(list(merged[~mask].geometry)).buffer(0)
    hit = unary_union(list(merged[mask].geometry)).buffer(0).intersection(other).area / 1e6
    print(f"  {label} 与其余县市重叠：{hit:.4f} km²")


def main():
    hn = gpd.read_file(HN_SHP, encoding="utf-8")
    print(f"全岛基线：{len(hn)} 个单元")

    cm = load_layer(CM_SHP, hn.crs, CM_CITY, CM_EN)
    wc = load_layer(WC_SHP, hn.crs, WC_CITY, WC_EN)
    print(f"澄迈替换层：{len(cm)} 个单元；文昌替换层：{len(wc)} 个单元")

    cm_mask = is_county(hn, "469023", CM_CITY).to_numpy()
    wc_mask = is_county(hn, "469005", WC_CITY).to_numpy()
    print(f"将替换：澄迈 {int(cm_mask.sum())} 个，文昌 {int(wc_mask.sum())} 个")

    rows = hn.to_dict("records")
    # 文昌块先替换（保持原位置），再替换澄迈块
    rows, _ = replace_block(rows, wc_mask, wc.to_dict("records"))
    # 掩码是按原始行索引算的，替换后行数变了，重新按新表定位澄迈块
    tmp = gpd.GeoDataFrame(rows, geometry="geometry", crs=hn.crs)
    cm_mask2 = is_county(tmp, "469023", CM_CITY).to_numpy()
    rows, _ = replace_block(tmp.to_dict("records"), cm_mask2, cm.to_dict("records"))

    merged = gpd.GeoDataFrame(rows, geometry="geometry", crs=hn.crs)
    merged["AREA_KM2"] = (merged.geometry.area / 1e6).round(3)

    cm_final = is_county(merged, "469023", CM_CITY)
    wc_final = is_county(merged, "469005", WC_CITY)
    print(f"替换后单元数：{len(merged)}（澄迈 {int(cm_final.sum())} + 文昌 {int(wc_final.sum())}）")
    print(f"总面积：{merged.geometry.area.sum() / 1e6:.1f} km²")
    print(f"有效几何：{int(merged.geometry.is_valid.sum())}/{len(merged)}")
    check_overlap(merged, cm_final, "澄迈")
    check_overlap(merged, wc_final, "文昌")

    for ext in SIDECARS:
        p = OUT_SHP[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    merged.to_file(OUT_SHP, encoding="utf-8")
    print(f"已输出：{OUT_SHP}")


if __name__ == "__main__":
    main()
