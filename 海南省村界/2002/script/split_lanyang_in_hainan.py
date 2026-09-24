# -*- coding: utf-8 -*-
"""
split_lanyang_in_hainan.py —— 把 Hainan_town.shp 中的兰洋镇（469003105，
目前是 MultiPolygon 两块）拆成：
  北块 → 兰洋镇（沿用，469003105）
  南块 → 番加乡（2002新增，469003122，参考点 492613,1998729 位于南块）
插回原位、顺序不变，写回前备份为 Hainan_town_before_lanyang_split.shp。

用法：
  python split_lanyang_in_hainan.py
"""
import os
import sys

import geopandas as gpd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from merge_7counties_into_hainan import backup_once, write_shp  # noqa: E402

DIR_2002 = os.path.dirname(HERE)
HAINAN = os.path.join(DIR_2002, "hainan")
BASE_SHP = os.path.join(HAINAN, "Hainan_town.shp")
OUT_SHP = os.path.join(HAINAN, "Hainan_town.shp")

LANYANG_CODE = "469003105"
LANYANG_NAME = "兰洋镇"
FANJIA_CODE = "469003122"
FANJIA_NAME = "番加乡"
BACKUP_TAG = "before_lanyang_split"


def main():
    hn = gpd.read_file(BASE_SHP, encoding="utf-8")
    mask = (hn["CODE"].astype(str) == LANYANG_CODE) & (hn["TOWN"].astype(str) == LANYANG_NAME)
    assert mask.sum() == 1, f"基图中兰洋镇（{LANYANG_CODE}）应有且仅有一条，实际 {int(mask.sum())} 条"
    idx = hn.index[mask][0]
    g = hn.loc[idx, "geometry"]
    if g.geom_type == "MultiPolygon":
        parts = sorted(list(g.geoms), key=lambda p: p.centroid.y, reverse=True)
    else:
        parts = [g]
    assert len(parts) == 2, f"兰洋镇应有 2 块（北块/南块），实际 {len(parts)} 块"

    north, south = parts[0], parts[1]
    base = dict(CITY=hn.loc[idx, "CITY"], EN=hn.loc[idx, "EN"])
    print(f"待拆分：{LANYANG_NAME} {round(g.area/1e6, 3)} km2 → "
          f"北 {LANYANG_NAME} {round(north.area/1e6, 3)} km2 / "
          f"南 {FANJIA_NAME} {round(south.area/1e6, 3)} km2")

    backup_once(OUT_SHP, tag=BACKUP_TAG)

    north_row = {
        "CODE": LANYANG_CODE, "TOWN": LANYANG_NAME,
        **base, "N_FEAT": int(hn.loc[idx, "N_FEAT"]),
        "AREA_KM2": round(north.area / 1e6, 3), "geometry": north,
    }
    south_row = {
        "CODE": FANJIA_CODE, "TOWN": FANJIA_NAME,
        **base, "N_FEAT": 0,
        "AREA_KM2": round(south.area / 1e6, 3), "geometry": south,
    }
    rows = hn.to_dict("records")
    rows = rows[:int(hn.index.get_loc(idx))] + [north_row, south_row] \
        + rows[int(hn.index.get_loc(idx)) + 1:]
    merged = gpd.GeoDataFrame(rows, geometry="geometry", crs=hn.crs)
    print(f"拆分成功：{len(hn)} → {len(merged)} 要素（儋州 +1）")

    sub = merged[(merged["CODE"].astype(str) == LANYANG_CODE) |
                 (merged["CODE"].astype(str) == FANJIA_CODE)]
    print(sub[["CODE", "TOWN", "N_FEAT", "AREA_KM2"]].to_string(index=False))
    gap = sub.geometry.union_all().area / 1e6 - g.area / 1e6
    print(f"拆分前后面积差：{gap:.6f} km2")
    print(f"有效几何：{int(merged.geometry.is_valid.sum())}/{len(merged)}")

    write_shp(merged, OUT_SHP)
    print(f"完成，写出：{OUT_SHP}")


if __name__ == "__main__":
    main()