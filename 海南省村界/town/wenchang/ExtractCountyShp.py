# -*- coding: utf-8 -*-
"""
ExtractCountyShp.py —— 从全岛乡镇图层中提取指定县市（如文昌市）并另存为新的 SHP
=================================================================================
输入：
  --input   全岛乡镇图层（如 town/Hainan2002/Current/Hainan_town_before_wenchang.shp）
  --outdir  输出目录（不存在则自动创建）
  --name    输出文件名前缀（如 wenchang，生成 wenchang.shp/wenchang.dbf/...）
  --code    县市行政区划代码前缀（6 位，如 469005=文昌市，与 --city 二选一）
  --city    县市名（按 CITY 字段精确匹配，如 文昌市，与 --code 二选一）

用法：
  python ExtractCountyShp.py --input Hainan2002/Current/Hainan_town_before_wenchang.shp \
      --outdir wenchang --name wenchang --code 469005
"""
from __future__ import annotations

import argparse
import os

import geopandas as gpd

SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")

CODE_FIELD = "CODE"   # 全岛图层的县/乡镇代码字段
CITY_FIELD = "CITY"   # 全岛图层的县市名称字段
ENCODING = "utf-8"


def log(m=""):
    print(m, flush=True)


def clean_output(dir_, name):
    """清掉残留的旧同名 sidecar，避免读到旧 .dbf 等。"""
    for ext in SIDECARS:
        p = os.path.join(dir_, name + ext)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="从全岛 SHP 提取指定县市并另存为新 SHP")
    ap.add_argument("--input", required=True, help="全岛乡镇 SHP 路径")
    ap.add_argument("--outdir", required=True, help="输出目录")
    ap.add_argument("--name", required=True, help="输出文件名前缀（不含扩展名）")
    ap.add_argument("--code", default=None, help="县市代码前缀（6 位），如 469005")
    ap.add_argument("--city", default=None, help="县市名称（CITY 字段精确匹配），如 文昌市")
    args = ap.parse_args(argv)

    if not args.code and not args.city:
        raise SystemExit("✗ 请至少提供 --code 或 --city 之一")
    if args.code and args.city:
        log("  ⚠ 同时给出 --code 与 --city，将按二者交集筛选")

    src = os.path.abspath(args.input)
    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    gdf = gpd.read_file(src, encoding=ENCODING)
    log(f"  读取全岛图层：{len(gdf)} 行，字段：{list(gdf.columns)}，CRS：{gdf.crs}")

    def _s_astr(v):
        return "" if v is None else str(v)

    mask = None
    if args.code:
        m = gdf[CODE_FIELD].astype(str).str.startswith(args.code)
        mask = m if mask is None else (mask & m)
    if args.city:
        m = gdf.assign(_city=gdf[CITY_FIELD].map(_s_astr))["_city"].eq(args.city)
        mask = m if mask is None else (mask & m)

    sub = gdf[mask].copy()
    if sub.empty:
        if args.code:
            codes = sorted(gdf[CODE_FIELD].astype(str).str[:6].unique())
            log(f"  ✗ 未匹配到县市。可选县市代码前缀：{codes}")
        if args.city:
            cities = sorted(gdf[CITY_FIELD].map(_s_astr).unique())
            log(f"  ✗ 未匹配到县市。可选县市名称：{cities}")
        raise SystemExit("✗ 无匹配要素，请检查筛选条件")

    clean_output(outdir, args.name)
    dst = os.path.join(outdir, args.name + ".shp")
    sub.to_file(dst, encoding=ENCODING)
    log(f"  已提取 {len(sub)} 个乡镇要素 → {dst}")
    log("  " + sub[[CODE_FIELD, "TOWN"]].to_string(index=False).replace("\n", "\n  "))
    log("DONE")


if __name__ == "__main__":
    main()