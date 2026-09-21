# -*- coding: utf-8 -*-
"""
根据 Wenchang2002.xlsx 生成文昌市 2002 年乡镇级边界 Shapefile。

思路：
  1. 读取 海南村界.shp 中属于文昌市(469005)的村级面；
  2. 用 Wenchang2002.xlsx 的「乡镇名称 <-> 建制村名称/社区名称」建立映射；
  3. 把村级面按 2002 年乡镇名归并(dissolve)；
  4. 仅输出乡镇级要素（无村级界线）到 town/ 目录。

输出：D:/Windows/Documents/海南省村界/海南省村界/town/Wenchang2002_town.shp
"""

import os
import re
import difflib
from collections import defaultdict

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# ==================== 配置 ====================
SHP_PATH = r"D:/Windows/Documents/海南省村界/海南省村界/海南村界.shp"
EXCEL_PATH = r"D:/Windows/Documents/海南省村界/海南省村界/Wenchang2002.xlsx"
OUT_DIR = r"D:/Windows/Documents/海南省村界/海南省村界/town"
OUT_NAME = "Wenchang2002_town.shp"

WENCHANG_CODE = "469005"
SHP_ENCODING = "gbk"
OUT_ENCODING = "utf-8"

# 现状 shp 名称 -> 2002 xlsx 名称 的错别字/异体字对照（用于纠正个别对不上的村）
SHX_ALIAS = {
    "大杨": "大扬",
    "南隆": "高隆",
    "排瑯": "排蜋",
    "蛟龙": "蚊龙",
    "罗拿": "罗民",
    "圆堆": "园堆",
}

# 需要排除、不并入任何乡镇的要素（岛屿、独立农场等）
EXCLUDE_NAME_KEYWORDS = ("峙岛",)
EXCLUDE_NAMES = {"罗豆农场"}
EXCLUDE_GROUPS = {"469005500", "469005402"}  # 北峙岛等岛群、罗豆农场

FUZZY_THRESHOLD = 0.5
# ==============================================


def clean_name(name):
    """去掉行政区后缀/前缀，得到可比较的短名。"""
    if not isinstance(name, str):
        return ""
    s = name.strip()
    for suf in ("村委会", "村民委员会", "社区居委会", "居委会", "社区", "村"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    if s.startswith("文昌市"):
        s = s[3:]
    return s.strip()


def norm(s):
    """统一异体字：圩/墟 同义。"""
    return s.replace("圩", "墟")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---------- 1. 读取 Excel，建立 村名/社区名 -> 乡镇名 映射 ----------
    df = pd.read_excel(EXCEL_PATH, header=0, dtype=str)
    town_col = next(c for c in df.columns if "乡镇" in c and "名称" in c)
    cols_to_read = [c for c in df.columns if c != town_col]
    print(f"Excel 行数：{len(df)}，乡镇字段：{town_col}")

    name2towns = defaultdict(set)
    for _, row in df.iterrows():
        town = str(row[town_col]).strip()
        if not town or town == "nan":
            continue
        for col in cols_to_read:
            val = row.get(col)
            if not isinstance(val, str):
                continue
            for part in re.split(r"[、，,;；\s]+", val):
                c = norm(clean_name(part))
                if not c or c in ("—", "-", "－"):
                    continue
                name2towns[c].add(town)

    towns_all = sorted({t for s in name2towns.values() for t in s})
    print(f"映射记录：{len(name2towns)} 个村/社区名 -> {len(towns_all)} 个乡镇")

    # ---------- 2. 读取 SHP，提取文昌市村级面 ----------
    gdf = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING)
    gdf["county"] = gdf["XZQDM"].astype(str).str[:6]
    gdf["group"] = gdf["XZQDM"].astype(str).str[:9]
    w = gdf[gdf["county"] == WENCHANG_CODE].copy()
    print(f"文昌市村级要素：{len(w)}")

    # 排除岛屿/独立农场
    raw_names = w["XZQMC"].astype(str)
    excl = raw_names.str.contains("|".join(EXCLUDE_NAME_KEYWORDS))
    for n in EXCLUDE_NAMES:
        excl = excl | raw_names.str.contains(re.escape(n))
    excl = excl | w["group"].isin(EXCLUDE_GROUPS)
    print(f"排除岛屿/独立农场要素：{int(excl.sum())}")
    w = w[~excl].copy()

    w["clean"] = w["XZQMC"].apply(lambda s: norm(clean_name(s)))

    # ---------- 3. 每个现状 group(9位码) 内先做无歧义投票 ----------
    def candidates(c):
        if c in name2towns:
            return set(name2towns[c])
        if c in SHX_ALIAS:
            return set(name2towns.get(SHX_ALIAS[c], set()))
        return set()

    w["cand"] = w["clean"].apply(candidates)

    votes = defaultdict(lambda: defaultdict(int))
    for _, row in w.iterrows():
        if len(row["cand"]) == 1:
            votes[row["group"]][next(iter(row["cand"]))] += 1

    # ---------- 4. 分配每个村级面所属的 2002 乡镇 ----------
    def assign(row):
        c = row["clean"]
        cand = set(row["cand"])
        v = votes.get(row["group"], {})

        if len(cand) == 1:
            return next(iter(cand))
        if len(cand) > 1:
            return max(cand, key=lambda t: (v.get(t, 0), t))

        # 模糊匹配（优先限定在本 group 已出现的乡镇）
        group_towns = set(v.keys())
        best, best_score = None, 0.0
        for name, towns in name2towns.items():
            if group_towns and not (towns & group_towns):
                continue
            score = difflib.SequenceMatcher(None, c, name).ratio()
            if score > best_score:
                best_score, best = score, towns
        if best is not None and best_score >= FUZZY_THRESHOLD:
            return max(best, key=lambda t: (v.get(t, 0), t))
        return None

    w["TOWN"] = w.apply(assign, axis=1)

    n_matched = int(w["TOWN"].notna().sum())
    print(f"名称匹配成功：{n_matched} / {len(w)}")

    # ---------- 5. 未匹配的村级面就近并入最近的乡镇 ----------
    matched = w[w["TOWN"].notna()].copy()
    unmatched = w[w["TOWN"].isna()].copy()
    if not unmatched.empty:
        town_geom = matched.dissolve(by="TOWN").geometry
        assigned = []
        for _, row in unmatched.iterrows():
            pt = row.geometry.representative_point()
            best_town, best_dist = None, float("inf")
            for t, geom in town_geom.items():
                d = pt.distance(geom)
                if d < best_dist:
                    best_dist, best_town = d, t
            assigned.append(best_town)
        unmatched["TOWN"] = assigned
        print(f"未匹配要素 {len(unmatched)} 个，已就近并入最近乡镇：")
        for _, row in unmatched.iterrows():
            print(f"    {row['XZQMC']} -> {row['TOWN']}")

    merged = pd.concat([matched, unmatched], ignore_index=True)
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=w.crs)

    # ---------- 6. 归并为乡镇级面 ----------
    n_vill = merged.groupby("TOWN").size().rename("N_VILL")
    n_fill = (
        unmatched.groupby("TOWN").size().rename("N_FILL")
        if not unmatched.empty
        else pd.Series(dtype="int64", name="N_FILL")
    )
    towns = merged.dissolve(by="TOWN", aggfunc="first").reset_index()
    towns = towns.merge(n_vill, on="TOWN", how="left")
    towns = towns.merge(n_fill, on="TOWN", how="left")
    towns["N_FILL"] = towns["N_FILL"].fillna(0).astype(int)
    towns = towns[["TOWN", "N_VILL", "N_FILL", "geometry"]].copy()
    towns["AREA_KM2"] = (towns.geometry.area / 1e6).round(3)
    towns = towns.sort_values("TOWN").reset_index(drop=True)
    towns["TOWN_ID"] = range(1, len(towns) + 1)

    print(f"\n生成乡镇级要素：{len(towns)} 个")
    for _, r in towns.iterrows():
        print(f"    {r['TOWN_ID']:>2}  {r['TOWN']:<6} 村数={int(r['N_VILL']):>3} "
              f"补入={int(r['N_FILL'])} 面积={r['AREA_KM2']} km²")

    # ---------- 7. 输出 Shapefile ----------
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
