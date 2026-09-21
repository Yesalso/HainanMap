# -*- coding: utf-8 -*-
"""
生成整个海南岛的乡镇级(乡镇/街道)边界 Shapefile。

数据源：
  - 海南村界.shp   村级面（XZQDM 前 9 位为乡镇级行政区码）
  - HainanMap.xlsx 9 位行政区码 -> 乡镇名/市县名/英文名

处理：
  - 按 XZQDM 前 9 位把村级面 dissolve 成乡镇级面；
  - 包含岛屿群、农场、开发区等全部乡镇级单元（三沙市不在本数据中）；
  - 名称统一转为简体。

输出：D:/Windows/Documents/海南省村界/海南省村界/town/Hainan_town.shp
"""

import os
import re
from os.path import commonprefix

import pandas as pd
import geopandas as gpd

# ==================== 配置 ====================
SHP_PATH = r"D:/Windows/Documents/海南省村界/海南省村界/海南村界.shp"
MAP_XLSX = r"D:/Windows/Documents/海南省村界/海南省村界/HainanMap.xlsx"
OUT_DIR = r"D:/Windows/Documents/海南省村界/海南省村界/town"
OUT_NAME = "Hainan_town.shp"

SHP_ENCODING = "gbk"
OUT_ENCODING = "utf-8"

# 不参与出图的要素（按名称关键字排除），如：国有滩涂
EXCLUDE_NAME_KEYWORDS = ("国有滩涂",)
# ==============================================

try:
    from opencc import OpenCC
    _cc_t2s = OpenCC("t2s")

    def to_simple(s):
        return _cc_t2s.convert(s)
except Exception:
    def to_simple(s):
        return s


def clean_name(name):
    if not isinstance(name, str):
        return ""
    s = name.strip()
    for suf in ("村民委员会", "居民委员会", "社区居委会", "村委会", "居委会",
                "社区", "办事处", "建成区", "城区"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.strip()


def derive_name(names):
    """从 group 内所有村级名提取乡镇名（用于映射表缺失/无效时兜底）。"""
    pref = commonprefix([str(n) for n in names])
    m = re.findall(r"([^\W\d_市区县]{1,5}?(?:镇|街道|乡))", pref)
    if m:
        return m[-1]
    return clean_name(str(names[0])) if names else ""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---------- 1. 读取行政区码 -> 乡镇名映射 ----------
    hm = pd.read_excel(MAP_XLSX, header=None, dtype=str)
    code2info = {}
    for _, r in hm.iterrows():
        code = str(r[0]).strip()
        if not code or code == "nan":
            continue
        code2info[code] = {
            "town": str(r[4]).strip() if pd.notna(r[4]) else "",
            "city": str(r[3]).strip() if pd.notna(r[3]) else "",
            "en": str(r[2]).strip() if pd.notna(r[2]) else "",
        }
    print(f"映射表记录：{len(code2info)} 个乡镇级单元")

    # ---------- 2. 读取村级面 ----------
    gdf = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING)
    gdf["CODE"] = gdf["XZQDM"].astype(str).str[:9]

    # 排除国有滩涂等不需要出图的要素
    raw = gdf["XZQMC"].astype(str)
    excl = raw.str.contains("|".join(EXCLUDE_NAME_KEYWORDS), regex=True)
    if excl.any():
        for _, r in gdf[excl].iterrows():
            print(f"  排除：{r['XZQDM']} {r['XZQMC']} "
                  f"({r.geometry.area / 1e6:.3f} km²)")
        gdf = gdf[~excl].copy()
        print(f"已排除 {int(excl.sum())} 个要素（{','.join(EXCLUDE_NAME_KEYWORDS)}）")

    print(f"村级要素：{len(gdf)}，乡镇级分组：{gdf['CODE'].nunique()}")

    # 修复无效几何，避免 dissolve 出错
    try:
        gdf["geometry"] = gdf.geometry.make_valid()
    except Exception:
        pass

    # ---------- 3. 归并为乡镇级面 ----------
    n_feat = gdf.groupby("CODE").size().rename("N_FEAT")
    towns = gdf.dissolve(by="CODE", aggfunc="first").reset_index()

    # ---------- 4. 补全名称 ----------
    def resolve_name(code, names):
        info = code2info.get(code)
        raw = info["town"] if info else ""
        bad = (not raw) or raw.lower() == "nan" or len(raw) <= 1
        if bad:
            raw = derive_name(names)
        return to_simple(raw)

    group_names = gdf.groupby("CODE")["XZQMC"].apply(list)
    towns["TOWN"] = [resolve_name(c, group_names.get(c, [])) for c in towns["CODE"]]
    towns["CITY"] = [to_simple(code2info.get(c, {}).get("city", "")) for c in towns["CODE"]]
    towns["EN"] = [code2info.get(c, {}).get("en", "") for c in towns["CODE"]]
    towns = towns.merge(n_feat, on="CODE", how="left")
    towns["AREA_KM2"] = (towns.geometry.area / 1e6).round(3)
    towns = towns[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
    towns = towns.sort_values("CODE").reset_index(drop=True)

    print(f"生成乡镇级要素：{len(towns)}")
    print(towns[["CODE", "TOWN", "CITY", "N_FEAT", "AREA_KM2"]].to_string(index=False))

    # ---------- 5. 输出 Shapefile ----------
    out_path = os.path.join(OUT_DIR, OUT_NAME)
    if os.path.exists(out_path):
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            p = out_path[:-4] + ext
            if os.path.exists(p):
                os.remove(p)
    towns.to_file(out_path, encoding=OUT_ENCODING)
    print(f"\n已输出：{out_path}")
    print(f"CRS：{towns.crs}")


if __name__ == "__main__":
    main()
