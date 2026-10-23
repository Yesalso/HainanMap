# -*- coding: utf-8 -*-
"""从《华南乡镇界线》中去除“事实上不存在”的行政区块。

- 广东部分依据 广东省CJ.shp（村居界）识别：
  名称含“岛屿 / 海域 / 滩涂 / 海洋局”的地块/海域；
- 广西部分依据 2广西村界/广西壮族自治区村居界4490.shp 识别：
  国有滩涂、国有岛屿、沿海岛屿、防城港市海洋局。

处理方式：
1. 整条属于伪乡镇/海域的要素直接删除；
2. 落在真实乡镇内部的岛屿/海域，从对应乡镇面中挖去；
3. 更新被修改乡镇的“要素数”“面积km2”；
4. 覆盖写回 华南乡镇界线.shp（先自动备份）。
"""
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import pyogrio
import shapely

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SC_SHP = HERE / "华南乡镇界线.shp"
GD_VILLAGE = HERE / "广东省CJ.shp"
GX_VILLAGE = HERE / "2广西村界" / "广西壮族自治区村居界4490.shp"

GD_PATTERN = "岛屿|海域|滩涂|海洋局"
GX_NAMES = ["国有滩涂", "国有岛屿", "沿海岛屿", "防城港市海洋局"]

COLUMNS = ["省", "市", "县", "乡镇", "乡镇码", "要素数", "面积km2", "geometry"]


def iter_polygons(geom):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            yield from iter_polygons(g)


def clean_geom(geom, min_hole_area=1.0, min_part_area=1.0):
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    try:
        geom = shapely.remove_repeated_points(geom)
    except Exception:
        pass
    cleaned = []
    for p in iter_polygons(geom):
        if p.area < min_part_area:
            continue
        holes = [ring for ring in p.interiors
                 if shapely.Polygon(ring).area >= min_hole_area]
        q = shapely.Polygon(p.exterior, holes)
        if q.area >= min_part_area:
            cleaned.append(q)
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    return shapely.MultiPolygon(cleaned)


def backup_sc():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_dir = HERE / f"backup_before_remove_non_admin_sc_{stamp}"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        src = SC_SHP.with_suffix(ext)
        if src.exists():
            shutil.copy2(src, dst_dir / src.name)
    return dst_dir


def load_gd_pseudo_codes():
    """返回（伪乡镇码集合，真实乡镇中被挖去的要素数统计，伪要素总数）"""
    gd = pyogrio.read_dataframe(
        GD_VILLAGE, read_geometry=False,
        columns=["NAME", "乡镇码", "乡镇级"])
    gd["NAME"] = gd["NAME"].astype(str)
    gd["乡镇码"] = gd["乡镇码"].astype(str).str.strip()
    gd["code9"] = gd["乡镇码"].str[:9]

    mask = gd["NAME"].str.contains(GD_PATTERN, regex=True, na=False)
    matched = gd[mask]
    if matched.empty:
        raise SystemExit("广东省CJ.shp 中未找到岛屿/海域等要素。")

    def has_real_town(code9):
        grp = gd[gd["code9"] == code9]
        codes = grp["乡镇码"]
        real = ((codes.str.len() == 12) & codes.str.endswith("000")
                & grp["乡镇级"].notna()
                & (grp["乡镇级"].astype(str).str.strip() != ""))
        return bool(real.any())

    matched_codes = set(matched["code9"])
    pseudo_codes = {c for c in matched_codes if not has_real_town(c)}
    real_codes = matched_codes - pseudo_codes
    per_real = (matched[matched["code9"].isin(real_codes)]
                .groupby("code9").size().to_dict())

    print("广东待处理要素名称：")
    print(matched.groupby("NAME").size().to_string())
    print(f"  伪乡镇码：{sorted(pseudo_codes)}")
    print(f"  真实乡镇内需挖去的要素："
          + ", ".join(f"{k}({v})" for k, v in sorted(per_real.items())))
    return pseudo_codes, per_real, len(matched)


def load_gx_pseudo_codes():
    gx = pyogrio.read_dataframe(
        GX_VILLAGE, read_geometry=False, columns=["name", "code"])
    gx["name"] = gx["name"].astype(str)
    gx["code"] = gx["code"].astype(str).str.strip()
    m = gx["name"].isin(GX_NAMES)
    if not m.any():
        raise SystemExit("广西村居界中未找到待去除要素。")
    print("广西待去除要素：")
    print(gx.loc[m, "name"].value_counts().to_string())
    return set(gx.loc[m, "code"].str[:9]), int(m.sum())


def main():
    t0 = time.time()

    print("读取广东村居界属性并识别伪行政区 ...")
    gd_pseudo_codes, per_real, n_gd = load_gd_pseudo_codes()
    print("读取广西村居界属性并识别伪行政区 ...")
    gx_pseudo_codes, n_gx = load_gx_pseudo_codes()

    print("读取广东伪要素几何 ...")
    where = ("NAME LIKE '%岛屿%' OR NAME LIKE '%海域%' OR "
             "NAME LIKE '%滩涂%' OR NAME LIKE '%海洋局%'")
    gd_pseudo = pyogrio.read_dataframe(
        GD_VILLAGE, where=where,
        columns=["NAME", "乡镇码", "乡镇级", "geometry"])
    gd_pseudo["NAME"] = gd_pseudo["NAME"].astype(str)
    gd_pseudo["乡镇码"] = gd_pseudo["乡镇码"].astype(str).str.strip()
    gd_pseudo["code9"] = gd_pseudo["乡镇码"].str[:9]
    print(f"  读取 {len(gd_pseudo)} 个要素（属性匹配 {n_gd} 个）")

    print("读取华南乡镇界线 ...")
    sc = pyogrio.read_dataframe(SC_SHP)
    sc["乡镇码"] = sc["乡镇码"].astype(str).str.strip()
    crs = sc.crs
    n_before = len(sc)
    area_before = sc.geometry.area.sum()
    print(f"  要素 {n_before}，CRS {crs.to_string() if crs else None}")

    gd_pseudo = gd_pseudo.to_crs(crs)
    cut_geom = shapely.union_all(
        [g for g in gd_pseudo.loc[
            ~gd_pseudo["code9"].isin(gd_pseudo_codes), "geometry"].values
         if g is not None and not g.is_empty])
    if not cut_geom.is_valid:
        cut_geom = shapely.make_valid(cut_geom)
    print(f"真实乡镇内待挖去区域面积 {cut_geom.area / 1e6:.3f} km²")

    drop_codes = gd_pseudo_codes | gx_pseudo_codes
    drop_mask = sc["乡镇码"].isin(drop_codes)
    print(f"删除整条伪乡镇/海域要素：{int(drop_mask.sum())} 个")
    if drop_mask.any():
        print(sc.loc[drop_mask, ["省", "市", "县", "乡镇", "乡镇码"]]
              .to_string(index=False))
    sc = sc[~drop_mask].reset_index(drop=True)

    candidates = list(sc.sindex.query(cut_geom, predicate="intersects"))
    print(f"与待挖去区域接触/相交的乡镇：{len(candidates)} 个")
    geoms = sc.geometry.values.copy()
    changed = []
    for i in candidates:
        inter_area = geoms[i].intersection(cut_geom).area
        if inter_area > 1.0:
            geoms[i] = clean_geom(geoms[i].difference(cut_geom))
            changed.append(i)
    sc["geometry"] = geoms
    print(f"实际挖去岛屿/海域的乡镇：{len(changed)} 个")

    for i in changed:
        code = sc.at[i, "乡镇码"]
        cnt = int(per_real.get(code, 0))
        if cnt:
            sc.at[i, "要素数"] = max(0, int(sc.at[i, "要素数"]) - cnt)
    if changed:
        sc.loc[changed, "面积km2"] = (
            sc.loc[changed].geometry.area / 1e6).round(3)

    empty = sc.geometry.is_empty | sc.geometry.isna()
    if empty.any():
        print(f"移除空几何要素：{int(empty.sum())} 个")
        sc = sc[~empty].reset_index(drop=True)

    area_after = sc.geometry.area.sum()
    print(f"要素数 {n_before} -> {len(sc)}；"
          f"面积减少 {(area_before - area_after) / 1e6:.3f} km²")

    if changed:
        print("被修改乡镇：")
        print(sc.loc[changed, ["省", "市", "县", "乡镇", "乡镇码",
                               "要素数", "面积km2"]].to_string(index=False))

    lock = SC_SHP
    try:
        with open(lock, "r+b"):
            pass
        writable = True
    except OSError:
        writable = False

    if writable:
        backup_dir = backup_sc()
        print(f"原文件已备份到：{backup_dir}")
        dst = SC_SHP
    else:
        dst = HERE / "华南乡镇界线_去除非行政区.shp"
        print(f"原文件被占用，改存：{dst}")

    out = sc[COLUMNS]
    print(f"写出 {dst} ...")
    out.to_file(dst, encoding="UTF-8", engine="pyogrio")

    chk = pyogrio.read_dataframe(dst, read_geometry=False)
    chk_codes = chk["乡镇码"].astype(str).str.strip()
    left = sorted(set(chk_codes) & drop_codes)
    print(f"输出要素：{len(chk)}；残留伪乡镇码：{left if left else '无'}")
    print(f"总用时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
