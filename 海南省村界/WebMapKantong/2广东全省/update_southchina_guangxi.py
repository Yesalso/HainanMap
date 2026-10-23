# -*- coding: utf-8 -*-
"""用新的广西乡镇界线（含合并区）更新《华南乡镇界线》。

- 删除华南图中原有的广西来源要素（乡镇码以 45 开头，共 1281 个）
- 换成 2广西村界/广西乡镇界线_标注合并.shp 的 1281 个要素
- 按历史省界调整规则：北海(4505)、防城港(4506 除上思)、钦州(4507) -> 广东
- 红+橙合并区(RED_ORANGE) -> 广东钦州市，蓝色合并区(BLUE) -> 广西
- 其余要素（广东、海南、港澳及怀集）保持不变

输出：覆盖 华南乡镇界线.shp（先备份），并更新预览图
"""

import os
import shutil
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SC_SHP = HERE / "华南乡镇界线.shp"
GX_NEW = HERE / "2广西村界" / "广西乡镇界线_标注合并.shp"
PREVIEW = HERE / "华南乡镇界线_preview.png"
TARGET_CRS = gpd.read_file(SC_SHP, rows=1).crs

CITY_NAMES = {"4505": "北海市", "4506": "防城港市", "4507": "钦州市"}
COUNTY_NAMES = {
    "450502": "海城区", "450503": "银海区", "450512": "合浦县", "450521": "铁山港区",
    "450602": "港口区", "450603": "防城区", "450621": "上思县", "450681": "东兴市",
    "450702": "钦南区", "450703": "钦北区", "450721": "灵山县", "450722": "浦北县",
}
MERGED_CODES = {"RED_ORANGE", "BLUE"}
COLUMNS = ["省", "市", "县", "乡镇", "乡镇码", "要素数", "面积km2", "geometry"]


def backup(path: Path) -> None:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
        src = path.with_suffix(ext)
        if src.exists():
            shutil.copy2(src, path.with_name(f"{path.stem}_备份_{stamp}{ext}"))
    print("已备份原《华南乡镇界线》")


def polygon_only(geom, min_area=1.0):
    """make_valid 后仅保留面状部分（处理 GeometryCollection）"""
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    polys, stack = [], [geom]
    while stack:
        g = stack.pop()
        if g is None or g.is_empty:
            continue
        if g.geom_type == "Polygon":
            if g.area >= min_area:
                polys.append(g)
        elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
            stack.extend(list(g.geoms))
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else shapely.MultiPolygon(polys)


def load_guangxi() -> gpd.GeoDataFrame:
    g = gpd.read_file(GX_NEW, encoding="GBK").to_crs(TARGET_CRS)
    g["geometry"] = [shapely.force_2d(x) for x in g.geometry]
    g["乡镇码"] = g["乡镇码"].astype(str).str.strip()
    g["要素数"] = g["要素数"].fillna(0).astype("int32")

    code4 = g["乡镇码"].str[:4]
    code6 = g["乡镇码"].str[:6]
    merged = g["乡镇码"].isin(MERGED_CODES)

    g["省"] = "广西壮族自治区"
    move = code4.isin(["4505", "4506", "4507"]) & (code6 != "450621")
    g.loc[move, "省"] = "广东省"
    g.loc[g["乡镇码"] == "RED_ORANGE", "省"] = "广东省"

    g["市"] = g["市级代码"].astype(str)
    g["县"] = g["县代码"].astype(str)
    g["乡镇"] = g["乡镇级"].fillna("")
    for c, n in CITY_NAMES.items():
        g.loc[code4 == c, "市"] = n
    for c, n in COUNTY_NAMES.items():
        g.loc[code6 == c, "县"] = n

    red = g["乡镇码"] == "RED_ORANGE"
    g.loc[red, "市"] = "钦州市"
    g.loc[red, "县"] = "良庆区、邕宁区、钦北区"
    g.loc[red, "乡镇"] = "红+橙合并区"
    blue = g["乡镇码"] == "BLUE"
    g.loc[blue, "市"] = "南宁市"
    g.loc[blue, "县"] = "钦北区、灵山县"
    g.loc[blue, "乡镇"] = "蓝色合并区"

    g["面积km2"] = (g.geometry.area / 1e6).round(3)
    return g[COLUMNS]


def main() -> None:
    print("读取:", SC_SHP)
    sc = gpd.read_file(SC_SHP, encoding="UTF-8")
    sc["乡镇码"] = sc["乡镇码"].astype(str)
    n_all = len(sc)
    old = sc["乡镇码"].str.match(r"^45") | sc["乡镇码"].isin(MERGED_CODES)
    print(f"  华南要素 {n_all}，其中广西来源 {int(old.sum())}，保留 {int((~old).sum())}")
    keep = sc[~old].copy()

    print("读取:", GX_NEW)
    gx = load_guangxi()
    print("  新广西要素:", len(gx))
    print(gx.groupby("省").size().to_string())

    out = gpd.GeoDataFrame(
        pd.concat([keep[COLUMNS], gx], ignore_index=True),
        geometry="geometry", crs=TARGET_CRS,
    )
    out = out[out.geometry.notna() & ~out.geometry.is_empty]
    invalid = ~out.geometry.is_valid
    if invalid.any():
        print(f"修复无效几何: {int(invalid.sum())} 个")
    out["geometry"] = [polygon_only(g) for g in out.geometry]
    out = out[out.geometry.notna() & ~out.geometry.is_empty]

    print("\n更新后各省:")
    print(out.groupby("省").agg(要素数=("乡镇码", "size"),
                                面积km2=("面积km2", "sum")).round(1).to_string())
    print(f"总要素: {len(out)}")

    lock = SC_SHP.with_suffix(".shp")
    try:
        with open(lock, "r+b"):
            pass
        writable = True
    except OSError:
        writable = False

    if writable:
        backup(SC_SHP)
        dst = SC_SHP
    else:
        dst = HERE / "华南乡镇界线_更新.shp"
        print(f"⚠️ 原文件被占用，改存 {dst}")

    out.to_file(dst, encoding="UTF-8")
    print("已写出:", dst)

    try:
        sys.path.insert(0, str(HERE))
        from adjust_province_transfer import save_preview
        png = PREVIEW if writable else HERE / "华南乡镇界线_更新_preview.png"
        save_preview(out, png)
        print("已写出:", png)
    except Exception as e:
        print(f"⚠️ 预览图未更新（{e}）")


if __name__ == "__main__":
    main()
