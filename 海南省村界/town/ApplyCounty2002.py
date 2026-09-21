# -*- coding: utf-8 -*-
"""
ApplyCounty2002.py —— 把某县 2002 反向结果合并回 Hainan_town.shp
================================================================
输入：
  --hainan  全岛乡镇图层（town/Hainan2002/Hainan_town.shp）
  --county  反向结果 SHP（含 乡镇码 字段，EPSG:32649 或任意 CRS）
  --prefix  县码前缀（如 469005）

做法：
  1. 备份全岛图层为 Hainan_town_before_<name>.shp（仅当不存在时备份一次）；
  2. 保留全岛中非目标县的行（几何、属性逐字节不变），目标县按 乡镇码 用新几何替换；
  3. 目标县属性（TOWN/CITY/EN/N_FEAT）按 乡镇码 从原行继承，缺失则从权威村界统计；
  4. 原子写回（规避沙箱删除限制），输出 QA 预览图与面积变化表。

用法：
  python ApplyCounty2002.py --hainan Hainan2002/Hainan_town.shp \
      --county 文昌2002_fixed.shp --prefix 469005 --name wenchang
"""
from __future__ import annotations

import argparse
import os
import shutil

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.ops import unary_union
from shapely import make_valid

SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")


def log(m=""):
    print(m, flush=True)


def atomic_write(gdf, path, encoding="utf-8"):
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


def backup_once(path, bak):
    if os.path.exists(bak):
        log(f"  备份已存在，跳过：{bak}")
        return
    for ext in SIDECARS:
        src = path[:-4] + ext
        if os.path.exists(src):
            shutil.copy2(src, bak[:-4] + ext)
    log(f"  已备份原文件 → {bak}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hainan", required=True)
    ap.add_argument("--county", required=True, help="反向结果 SHP")
    ap.add_argument("--prefix", required=True, help="县码前缀，如 469005")
    ap.add_argument("--code-field", default="CODE", help="全岛图层县码字段")
    ap.add_argument("--key", default="乡镇码", help="反向结果的码字段")
    ap.add_argument("--name", default=None, help="备份/输出后缀名，默认用 prefix")
    ap.add_argument("--ref", default=None, help="权威村界 SHP（用于补 N_FEAT 统计，可选）")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args(argv)

    name = args.name or args.prefix
    hainan_path = os.path.abspath(args.hainan)
    hn_dir = os.path.dirname(hainan_path)
    bak = os.path.join(hn_dir, f"Hainan_town_before_{name}.shp")
    preview = os.path.join(hn_dir, f"{name}_替换校验.png")

    hn = gpd.read_file(hainan_path, encoding="utf-8")
    hn["geometry"] = hn.geometry.apply(lambda g: g if g.is_valid else make_valid(g))
    cy = gpd.read_file(os.path.abspath(args.county), encoding="utf-8")
    if cy.crs is not None and hn.crs is not None and cy.crs != hn.crs:
        cy = cy.to_crs(hn.crs)
    cy["geometry"] = cy.geometry.apply(lambda g: g if g.is_valid else make_valid(g))
    if args.key not in cy.columns:
        raise SystemExit(f"✗ 反向结果缺少字段 {args.key}，现有 {list(cy.columns)}")
    cy = cy.copy()
    cy["_code"] = cy[args.key].astype(str).str[:9]
    cy = cy[~cy.geometry.is_empty & cy.geometry.notna()].copy()
    cy = cy.dissolve(by="_code", aggfunc="first").reset_index()
    log(f"  反向结果：{len(cy)} 个单元")

    is_target = hn[args.code_field].astype(str).str.startswith(str(args.prefix))
    old_idx = list(hn.index[is_target])
    if not old_idx:
        raise SystemExit(f"✗ 全岛图层中无县码 {args.prefix} 的要素")
    log(f"  全岛：{len(hn)} 行，目标县 {len(old_idx)} 行")

    # 目标县属性模板（按码）
    old_attr = hn.loc[old_idx, [args.code_field, "TOWN", "CITY", "EN", "N_FEAT"]].copy()
    old_attr["_code"] = old_attr[args.code_field].astype(str).str[:9]
    attr_map = old_attr.set_index("_code").to_dict("index")

    # N_FEAT 补全（可选）
    nfeat_map = {}
    if args.ref and os.path.exists(args.ref):
        ref = gpd.read_file(args.ref, encoding="gbk")
        ref = ref[ref["XZQDM"].astype(str).str[:6] == str(args.prefix)]
        nfeat_map = ref.groupby(ref["XZQDM"].astype(str).str[:9]).size().to_dict()

    new_rows = []
    missing_attr = []
    for _, r in cy.iterrows():
        code = str(r["_code"])
        a = attr_map.get(code)
        if a is None:
            missing_attr.append(code)
            a = {"TOWN": r.get("乡镇名", code), "CITY": "", "EN": "", "N_FEAT": 0}
        nf = int(nfeat_map.get(code, a.get("N_FEAT", 0) or 0))
        new_rows.append({
            args.code_field: code,
            "TOWN": a.get("TOWN", code),
            "CITY": a.get("CITY", ""),
            "EN": a.get("EN", ""),
            "N_FEAT": nf,
            "geometry": r.geometry,
        })
    cy_new = gpd.GeoDataFrame(new_rows, geometry="geometry", crs=hn.crs)
    if missing_attr:
        log(f"  ⚠ 反向结果中 {len(missing_attr)} 个码在原图层无属性模板：{missing_attr}")

    # 体检
    invalid = int((~cy_new.geometry.is_valid).sum())
    total = cy_new.geometry.area.sum()
    union_a = unary_union(list(cy_new.geometry)).buffer(0).area
    log(f"  目标县体检：{len(cy_new)} 单元，非法 {invalid}，"
        f"面积和 {total/1e6:.3f} km²，并 {union_a/1e6:.3f} km²，"
        f"内部重叠 {(total-union_a)/1e6:.5f} km²")

    # 邻县重叠检查
    rest = hn[~is_target]
    cu = unary_union(list(cy_new.geometry)).buffer(0)
    hits = {}
    for city, grp in rest.groupby("CITY"):
        gu = unary_union([g for g in grp.geometry if g is not None and not g.is_empty]).buffer(0)
        a = cu.intersection(gu).area / 1e6
        if a > 1e-3:
            hits[str(city)] = round(a, 4)
    log(f"  邻县重叠检查：{hits if hits else '通过（无重叠）'}")

    if not args.no_backup:
        backup_once(hainan_path, bak)

    # 保持行序：在目标县原位置替换
    keep = hn[~is_target].copy()
    insert_at = int((hn.index < min(old_idx)).sum())
    merged = pd.concat([keep.iloc[:insert_at], cy_new, keep.iloc[insert_at:]],
                       ignore_index=True)
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=hn.crs)
    merged["AREA_KM2"] = (merged.geometry.area / 1e6).round(3)
    log(f"  合并后：{len(merged)} 行（目标县 {len(cy_new)}）")

    atomic_write(merged, hainan_path)
    log(f"  已写回：{hainan_path}")

    # 面积变化表
    def agg(g):
        m = g[g[args.code_field].astype(str).str.startswith(str(args.prefix))]
        m = m.copy()
        m["_c"] = m[args.code_field].astype(str).str[:9]
        return m.dissolve(by="_c").area / 1e6
    try:
        before = agg(hn).round(4)
        after = agg(merged).round(4)
        tbl = pd.DataFrame({"修复前km2": before, "修复后km2": after})
        tbl["变化km2"] = (tbl["修复后km2"] - tbl["修复前km2"]).round(4)
        csv = os.path.join(hn_dir, f"{name}_面积变化表.csv")
        tbl.to_csv(csv, encoding="utf-8-sig")
        log(f"  面积表：{csv}")
        log(tbl.to_string())
    except Exception as e:
        log(f"  ⚠ 面积表失败：{e}")

    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            for f in ("Microsoft YaHei", "SimHei", "SimSun"):
                try:
                    matplotlib.rcParams["font.sans-serif"] = [f]
                    break
                except Exception:
                    continue
            fig, axes = plt.subplots(1, 2, figsize=(18, 9), dpi=110)
            merged[~is_target.values].plot(ax=axes[0], facecolor="#e8eaed",
                                           edgecolor="#9aa0a6", linewidth=0.3)
            merged[is_target.values].plot(ax=axes[0], facecolor="#c0392b",
                                          edgecolor="#7b1f13", linewidth=0.5)
            axes[0].set_title(f"全岛（红色=替换后的 {args.prefix}）", fontsize=13)
            axes[0].set_aspect("equal"); axes[0].axis("off")
            sub = merged[is_target.values]
            sub.plot(ax=axes[1], column="TOWN", cmap="tab20",
                     edgecolor="black", linewidth=0.5)
            for _, r in sub.iterrows():
                p = r.geometry.representative_point()
                axes[1].text(p.x, p.y, str(r["TOWN"]), ha="center", va="center",
                             fontsize=8, color="#111111")
            axes[1].set_title(f"{args.prefix} 2002（{len(sub)} 单元）", fontsize=13)
            axes[1].set_aspect("equal"); axes[1].axis("off")
            plt.tight_layout()
            plt.savefig(preview, bbox_inches="tight", facecolor="white")
            plt.close(fig)
            log(f"  预览图：{preview}")
        except Exception as e:
            log(f"  ⚠ 预览图失败：{e}")
    log("DONE")


if __name__ == "__main__":
    main()
