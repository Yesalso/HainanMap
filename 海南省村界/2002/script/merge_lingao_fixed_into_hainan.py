# -*- coding: utf-8 -*-
"""
把 2002/hainan/lingao/lingao2002_fixed_Albers.shp（临高县 2002 乡镇/乡，
共 21 个单元：沿用 9 + 2002 新增 12，含临城/波莲/东英/博厚/皇桐/多文拆分
出的乡、曲线化修复后的结果）整体替换进全省乡镇图层 Hainan_town.shp。

原理与 merge_sanya_fixed_into_hainan.py 完全一致：
  1. 以当前 Hainan_town.shp 中临高旧乡镇（11 个）的并集作为「县界掩膜」；
  2. 把新 21 个单元逐一裁剪进掩膜，超出旧县界的部分切掉；
  3. 掩膜内未被覆盖的剩余缺口按共享边界就近并入相邻单元，保证县界严丝合缝；
  4. 临高块仍插回它原来的位置，其余市县顺序不变；
  5. 写回前备份一次为 Hainan_town_before_lingao.shp。

用法：
  python merge_lingao_fixed_into_hainan.py
"""
import os
import sys

import geopandas as gpd
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from merge_7counties_into_hainan import (  # noqa: E402
    sanitize, load_layer, clip_to_county_boundary, backup_once,
    is_county, replace_block, check_county, write_shp, TARGET_COLS,
)

DIR_2002 = os.path.dirname(HERE)
HAINAN = os.path.join(DIR_2002, "hainan")
BASE_SHP = os.path.join(HAINAN, "Hainan_town.shp")
OUT_SHP = os.path.join(HAINAN, "Hainan_town.shp")
LG_SHP = os.path.join(HAINAN, "lingao", "lingao2002_fixed_Albers.shp")

CITY = "临高县"
EN = "Lingao"
PREFIX = "469024"
BACKUP_TAG = "before_lingao"


def main():
    hn = gpd.read_file(BASE_SHP, encoding="utf-8")
    if hn.crs is None:
        hn = hn.set_crs("EPSG:4490")
    print(f"基图：{BASE_SHP}（{len(hn)} 要素，CRS={hn.crs.name if hn.crs else hn.crs}）")

    backup_once(OUT_SHP, tag=BACKUP_TAG)

    old_mask = is_county(hn, PREFIX, CITY).to_numpy()
    assert old_mask.any(), "基图中未找到临高县要素"
    old_union = sanitize(unary_union(list(hn[old_mask].geometry)).buffer(0))
    print(f"原临高：{int(old_mask.sum())} 个单元，县界面积 {old_union.area/1e6:.4f} km²")

    layer = load_layer(LG_SHP, hn.crs, CITY, EN)
    print(f"新临高：{len(layer)} 个单元")
    layer = clip_to_county_boundary(layer, old_union)
    print(f"裁剪吸附后：{len(layer)} 个单元")

    rows = hn.to_dict("records")
    rows, inserted = replace_block(rows, old_mask, layer.to_dict("records"))
    merged = gpd.GeoDataFrame(rows, geometry="geometry", crs=hn.crs)
    merged["AREA_KM2"] = (merged.geometry.area / 1e6).round(3)
    print(f"替换 {'成功' if inserted else '失败'}：{len(hn)} → {len(merged)} 要素")

    mask = is_county(merged, PREFIX, CITY)
    check_county(merged, mask, "临高县", old_union)
    print(f"有效几何：{int(merged.geometry.is_valid.sum())}/{len(merged)}")

    write_shp(merged, OUT_SHP)
    print(f"完成，写出：{OUT_SHP}")


if __name__ == "__main__":
    main()
