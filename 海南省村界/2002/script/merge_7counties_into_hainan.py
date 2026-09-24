# -*- coding: utf-8 -*-
"""
把 2002/hainan 下 7 个市县（白沙、昌江、东方、乐东、五指山、屯昌、万宁）的
2002 乡镇数据整体替换进全省乡镇图层 Hainan_town.shp。

与 merge_danzhou_qionghai_into_hainan.py 相同的修复原理：
  1. 以当前 Hainan_town.shp 中该市县旧乡镇的并集作为「县界掩膜」（与全省咬合）；
  2. 把新乡镇逐一裁剪进掩膜，超出旧县界的部分一律切掉（消除越界/重叠）；
  3. 掩膜内未被覆盖的剩余缺口按共享边界就近并入相邻乡镇（消除空白缝隙）。

注意：基线取「当前」Hainan_town.shp（已含琼海/儋州等更新），不要回退到旧备份。

依赖：geopandas / pandas / shapely
"""
import os
import shutil

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from shapely.validation import make_valid

# ==================== 配置 ====================
HERE = os.path.dirname(os.path.abspath(__file__))
DIR_2002 = os.path.dirname(HERE)
HAINAN = os.path.join(DIR_2002, "hainan")

BASE_SHP = os.path.join(HAINAN, "Hainan_town.shp")
OUT_SHP = os.path.join(HAINAN, "Hainan_town.shp")

# 写回前是否先备份当前省图（另存为 <name>_before_<tag>.shp，仅一次）
BACKUP_BEFORE = True
BACKUP_TAG = "before_7counties"

# 是否把新乡镇裁剪吸附到原县界（修复边界空隙/重叠）
CLIP_TO_COUNTY_BOUNDARY = True

# 需要替换的市县列表：shp=替换源 / city=中文市名 / en=英文 / prefix=旧CODE前缀
REPLACEMENTS = [
    {
        "shp": os.path.join(HAINAN, "baisha", "白沙2002_fixed_Albers.shp"),
        "city": "白沙黎族自治县", "en": "Baisha", "prefix": "469025",
    },
    {
        "shp": os.path.join(HAINAN, "changjiang", "昌江2002_fixed_Albers.shp"),
        "city": "昌江黎族自治县", "en": "Changjiang", "prefix": "469026",
    },
    {
        "shp": os.path.join(HAINAN, "dongfang", "东方2002_fixed_Albers.shp"),
        "city": "东方市", "en": "Dongfang", "prefix": "469007",
    },
    {
        "shp": os.path.join(HAINAN, "ledong", "乐东2002_fixed_Albers_v2.shp"),
        "city": "乐东黎族自治县", "en": "Ledong", "prefix": "469027",
    },
    {
        "shp": os.path.join(HAINAN, "tongza", "五指山2002_fixed_Albers.shp"),
        "city": "五指山市", "en": "Wuzhishan", "prefix": "469001",
    },
    {
        "shp": os.path.join(HAINAN, "tunchang", "屯昌2002_final_Albers_fixed2.shp"),
        "city": "屯昌县", "en": "Tunchang", "prefix": "469022",
    },
    {
        "shp": os.path.join(HAINAN, "wangning", "wanning2002_fixed_Albers_clean.shp"),
        "city": "万宁市", "en": "Wanning", "prefix": "469006",
    },
]

TARGET_COLS = ["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
# ==============================================


def sanitize(g):
    """修复无效几何并只保留(多)面部分。"""
    if g is None or g.is_empty:
        return g
    if not g.is_valid:
        g = make_valid(g)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else g.buffer(0)


def load_layer(path, base_crs, city, en):
    """读取替换源：重投影到省图 CRS、修复几何、补齐 CITY/EN 字段。"""
    g = gpd.read_file(path, encoding="utf-8").to_crs(base_crs)
    g["geometry"] = g.geometry.apply(sanitize)
    g = g[g.geometry.notna() & (~g.geometry.is_empty)].copy()
    g["CODE"] = g["CODE"].astype(str)
    g["TOWN"] = g["TOWN"].astype(str)
    g["CITY"] = city
    g["EN"] = g["EN"].astype(str).replace("nan", "").replace("", en).fillna(en)
    g["N_FEAT"] = pd.to_numeric(g.get("N_FEAT"), errors="coerce").fillna(0).astype(int)
    g["AREA_KM2"] = (g.geometry.area / 1e6).round(3)
    cols = [c for c in TARGET_COLS if c in g.columns]
    return g[cols].copy()


def clip_to_county_boundary(gdf, mask):
    """把 gdf 的每个乡镇裁剪进 mask，剩余缺口就近并入邻镇，返回修复后的要素。"""
    towns = [
        sanitize(t.intersection(mask))
        for t in gdf.geometry
        if not (t is None or t.is_empty)
    ]
    towns = [g for g in towns if not (g is None or g.is_empty)]
    if not towns:
        return gdf.iloc[0:0].copy()

    union = unary_union(towns).buffer(0)
    leftover = mask.difference(union)
    out = list(towns)
    if not leftover.is_empty:
        comps = list(leftover.geoms) if leftover.geom_type == "MultiPolygon" else [leftover]
        for comp in comps:
            comp = comp.buffer(0)
            if comp.is_empty:
                continue
            buf = comp.buffer(5.0)
            best, best_area = -1, -1.0
            for i, t in enumerate(out):
                a = buf.intersection(t).area
                if a > best_area:
                    best_area, best = a, i
            if best >= 0:
                out[best] = sanitize(out[best].union(comp))

    clipped = gdf[~gdf.geometry.is_empty].copy()
    clipped = clipped.iloc[:len(out)].copy()
    clipped["geometry"] = out
    clipped["AREA_KM2"] = (clipped.geometry.area / 1e6).round(3)
    return clipped


def backup_once(base, tag):
    """备份目标省图为 <名称>_<tag>.shp，仅执行一次。"""
    bak = base[:-4] + f"_{tag}.shp"
    if os.path.exists(bak):
        print(f"备份已存在，跳过：{bak}")
        return
    for ext in SIDECARS:
        src = base[:-4] + ext
        if os.path.exists(src):
            shutil.copy2(src, bak[:-4] + ext)
    print(f"已备份原省图：{bak}")


def is_county(df, prefix, city):
    return df["CODE"].astype(str).str.startswith(prefix) | \
        (df["CITY"].astype(str) == city)


def replace_block(rows, old_mask, new_rows):
    """把 rows 中 old_mask 覆盖的原位置块整体替换为 new_rows。"""
    out, i, n = [], 0, len(rows)
    inserted = False
    while i < n:
        if old_mask[i]:
            if not inserted:
                out.extend(new_rows)
                inserted = True
            i += 1
        else:
            out.append(rows[i])
            i += 1
    return out, inserted


def check_county(merged, mask, label, old_union):
    """覆盖/越界/重叠检查（整体县界与全省的关系）。"""
    u = unary_union(list(merged[mask].geometry)).buffer(0)
    print(f"  {label}：{int(mask.sum())} 乡镇")
    print(f"    县界与原县界缺口   : {(old_union.difference(u).area) / 1e6:.4f} km2")
    print(f"    县界超出原县界     : {(u.difference(old_union).area) / 1e6:.4f} km2")
    other = unary_union(list(merged[~mask].geometry)).buffer(0)
    print(f"    与周边县市重叠     : {u.intersection(other).area / 1e6:.4f} km2")


def write_shp(gdf, path, encoding="utf-8"):
    """原子写回：先写临时文件，成功后再 mov 覆盖，避免 Windows 占用/半写。"""
    tmp = path[:-4] + "__tmp.shp"
    for ext in SIDECARS:
        p = tmp[:-4] + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    gdf.to_file(tmp, encoding=encoding)
    for ext in SIDECARS:
        src, dst = tmp[:-4] + ext, path[:-4] + ext
        if os.path.exists(src):
            os.replace(src, dst)
    print("  已通过临时文件原子写回。")


def main():
    hn = gpd.read_file(BASE_SHP, encoding="utf-8")
    if hn.crs is None:
        hn = hn.set_crs("EPSG:4490")
    print(f"基图：{BASE_SHP}（{len(hn)} 要素，CRS={hn.crs.name if hn.crs else hn.crs}）")

    if BACKUP_BEFORE:
        backup_once(OUT_SHP, tag=BACKUP_TAG)

    old_unions = {}
    rows = hn.to_dict("records")
    for cfg in REPLACEMENTS:
        old_mask = is_county(hn, cfg["prefix"], cfg["city"]).to_numpy()
        if not old_mask.any():
            print(f"  [!] 基图中未找到 {cfg['city']}（{cfg['prefix']}），跳过。")
            continue
        if not os.path.exists(cfg["shp"]):
            print(f"  [!] 源文件不存在，跳过 {cfg['city']}：{cfg['shp']}")
            continue
        old_union = sanitize(unary_union(list(hn[old_mask].geometry)).buffer(0))
        old_unions[cfg["city"]] = old_union
        print(f"\n[{cfg['city']}] 原县界面积 {old_union.area / 1e6:.3f} km2" +
              f"（{int(old_mask.sum())} 旧乡镇）")

        layer = load_layer(cfg["shp"], hn.crs, cfg["city"], cfg["en"])
        print(f"  新数据：{len(layer)} 乡镇")
        if CLIP_TO_COUNTY_BOUNDARY:
            layer = clip_to_county_boundary(layer, old_union)
            print(f"  裁剪吸附后：{len(layer)} 乡镇")

        # 原位置替换
        cmask = is_county(hn, cfg["prefix"], cfg["city"]).to_numpy()
        rows, inserted = replace_block(rows, cmask, layer.to_dict("records"))
        hn = gpd.GeoDataFrame(rows, geometry="geometry", crs=hn.crs)
        print(f"  替换 {'成功' if inserted else '失败'}")

    merged = gpd.GeoDataFrame(rows, geometry="geometry", crs=hn.crs)
    merged["AREA_KM2"] = (merged.geometry.area / 1e6).round(3)
    print(f"\n替换后要素：{len(merged)}")
    print(f"总面积：{merged.geometry.area.sum() / 1e6:.1f} km2")
    print(f"有效几何：{int(merged.geometry.is_valid.sum())}/{len(merged)}")

    for cfg in REPLACEMENTS:
        mask = is_county(merged, cfg["prefix"], cfg["city"])
        if mask.any() and cfg["city"] in old_unions:
            check_county(merged, mask, cfg["city"], old_unions[cfg["city"]])

    write_shp(merged, OUT_SHP)
    print(f"\n完成，写出：{OUT_SHP}")


if __name__ == "__main__":
    main()
