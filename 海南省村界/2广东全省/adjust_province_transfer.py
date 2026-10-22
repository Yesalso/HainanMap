# -*- coding: utf-8 -*-
"""按规则调整华南乡镇界线的省份归属（省界调整）。

规则（可自由编辑 RULES，按顺序应用）：
    4505 北海市                        -> 广东省
    4506 防城港市（除 450621 上思县）  -> 广东省
    4507 钦州市                        -> 广东省
    441223 广宁县                      -> 广东省（恢复归属）
    441224 怀集县（广东肇庆）           -> 广西壮族自治区

字段规范化：
    CITY_NAMES / COUNTY_NAMES  转移涉及区域的 市/县 代码 -> 中文地名
    CITY_OVERRIDE              怀集县(441224) 划入广西后 市 -> 梧州专区

香港/深圳交界：
    香港面与深圳面重叠时，重叠部分一律取深圳界线（从香港面中扣除）。
    交界中间未覆盖的无效区，按共享边界就近划给相邻行政区。

合并区：
    红+橙合并区(RED_ORANGE) 整体划给 钦州市。

用法：
    python adjust_province_transfer.py                 # 输出 华南乡镇界线_调整.shp
    python adjust_province_transfer.py --inplace       # 直接覆盖 华南乡镇界线.shp
    python adjust_province_transfer.py --dry-run       # 只统计不写文件
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
SRC_SHP = HERE / "华南乡镇界线.shp"
OUT_SHP = HERE / "华南乡镇界线_调整.shp"
OUT_PNG = HERE / "华南乡镇界线_调整_preview.png"

RULES = [
    {
        "name": "北海市(4505)划入广东",
        "match4": ["4505"],
        "exclude6": [],
        "target": "广东省",
    },
    {
        "name": "防城港市(4506)除上思县外划入广东",
        "match4": ["4506"],
        "exclude6": ["450621"],
        "target": "广东省",
    },
    {
        "name": "钦州市(4507)划入广东",
        "match4": ["4507"],
        "exclude6": [],
        "target": "广东省",
    },
    {
        "name": "广宁县(441223)恢复归属广东",
        "match4": [],
        "match6": ["441223"],
        "exclude6": [],
        "target": "广东省",
        "fix_county": "广宁县",
    },
    {
        "name": "怀集县(441224)划入广西",
        "match4": [],
        "match6": ["441224"],
        "exclude6": [],
        "target": "广西壮族自治区",
        "fix_county": "怀集县",
    },
]

COLORS = {
    "广东省": "#cfe8ff",
    "广西壮族自治区": "#ffe3bf",
    "海南省": "#d6f0d6",
    "香港特别行政区": "#f7cfe0",
    "澳门特别行政区": "#f5c9a0",
}

# 转移涉及区域的 市 代码 -> 中文地名
CITY_NAMES = {
    "4505": "北海市",
    "4506": "防城港市",
    "4507": "钦州市",
}

# 转移涉及区域的 县 代码 -> 中文地名
COUNTY_NAMES = {
    "450502": "海城区",
    "450503": "银海区",
    "450512": "合浦县",
    "450521": "铁山港区",
    "450602": "港口区",
    "450603": "防城区",
    "450621": "上思县",
    "450681": "东兴市",
    "450702": "钦南区",
    "450703": "钦北区",
    "450721": "灵山县",
    "450722": "浦北县",
}

# 指定 县 代码 -> 新的 市 名称（历史归属）
CITY_OVERRIDE = {
    "441224": "梧州专区",
}


def load_merged() -> gpd.GeoDataFrame:
    print("读取:", SRC_SHP)
    g = gpd.read_file(SRC_SHP, encoding="UTF-8")
    g["乡镇码"] = g["乡镇码"].astype(str)
    g["code4"] = g["乡镇码"].str[:4]
    g["code6"] = g["乡镇码"].str[:6]
    print("  要素数:", len(g))
    return g


def apply_rules(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = g.copy()
    out["原省"] = out["省"]
    out["面积km2"] = (out.area / 1e6).round(3)

    for rule in RULES:
        mask = pd.Series(False, index=out.index)
        if rule.get("match4"):
            mask |= out["code4"].isin(rule["match4"])
        if rule.get("match6"):
            mask |= out["code6"].isin(rule["match6"])
        if rule.get("exclude6"):
            mask &= ~out["code6"].isin(rule["exclude6"])
        mask &= out["原省"] != rule["target"]

        sub = out[mask]
        print(f"\n规则: {rule['name']}")
        print(f"  涉及要素: {len(sub)}  面积: {sub['面积km2'].sum():.1f} km2")
        if len(sub):
            detail = (
                sub.groupby(["code4", "code6", "原省", "省"])
                .agg(要素数=("乡镇码", "size"), km2=("面积km2", "sum"))
                .round(1)
                .to_string()
            )
            print(detail)
            out.loc[mask, "省"] = rule["target"]
            if rule.get("fix_county"):
                out.loc[mask, "县"] = rule["fix_county"]
                print(f"  已修正源数据中错误的县名 -> {rule['fix_county']}")

    out["面积km2"] = out["面积km2"].astype("float32")
    return out


MIN_AREA_M2 = 1.0  # 小于 1 平方米的碎片视为拓扑噪声
SHENZHEN_PREFIX = "4403"
GAP_MIN_AREA_M2 = 1.0  # 小于 1 平方米的空洞不参与就近归属


def clean_geometry(geom, min_area=MIN_AREA_M2):
    """去除碎片（极小面片 / 极小孔洞），消除 nested shells / sliver 拓扑错误。"""
    from shapely.geometry import MultiPolygon, Polygon

    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "GeometryCollection":
        parts = []
        for sub in geom.geoms:
            cleaned = clean_geometry(sub, min_area)
            if cleaned is None:
                continue
            parts.extend(list(cleaned.geoms) if cleaned.geom_type == "MultiPolygon" else [cleaned])
    elif geom.geom_type == "Polygon":
        holes = []
        for ring in geom.interiors:
            if Polygon(ring).area >= min_area:
                holes.append(ring)
        poly = Polygon(geom.exterior, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None
        parts = list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
    elif geom.geom_type == "MultiPolygon":
        parts = []
        for sub in geom.geoms:
            cleaned = clean_geometry(sub, min_area)
            if cleaned is None:
                continue
            parts.extend(list(cleaned.geoms) if cleaned.geom_type == "MultiPolygon" else [cleaned])
    else:
        return None

    kept = [p for p in parts if p.geom_type == "Polygon" and not p.is_empty and p.area >= min_area]
    if not kept:
        return None
    return kept[0] if len(kept) == 1 else MultiPolygon(kept)


def take_shenzhen_boundary(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """香港与深圳交界重叠时，重叠部分一律取深圳界线（从香港面中扣除）。"""
    hk_mask = g["省"] == "香港特别行政区"
    sz_mask = (g["省"] == "广东省") & g["乡镇码"].str.startswith(SHENZHEN_PREFIX)
    if not hk_mask.any() or not sz_mask.any():
        return g

    boundary = g.loc[sz_mask, "geometry"].union_all()
    out = g.copy()
    hk_idx = out.index[hk_mask]
    before = out.loc[hk_idx, "geometry"].area.sum() / 1e6
    new_geoms = [
        clean_geometry(geom.difference(boundary)) if geom is not None else None
        for geom in out.loc[hk_idx, "geometry"]
    ]
    out.loc[hk_idx, "geometry"] = gpd.GeoSeries(new_geoms, index=hk_idx, crs=out.crs)
    out = out[out.geometry.notna() & ~out.geometry.is_empty]
    after = out.loc[out["省"] == "香港特别行政区", "geometry"].area.sum() / 1e6
    removed = max(before - after, 0.0)
    if removed > 1e-6:
        print(f"\n香港/深圳交界: 重叠 {removed:.3f} km2 划归深圳，香港要素 {int(hk_mask.sum())}")
    else:
        print(f"\n香港/深圳交界: 无重叠，香港要素 {int(hk_mask.sum())}")
    return out


def fill_hk_shenzhen_gaps(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """香港/深圳交界中间的无效空洞，按共享边界就近划给相邻行政区。"""
    hk_mask = g["省"] == "香港特别行政区"
    sz_mask = (g["省"] == "广东省") & g["乡镇码"].str.startswith(SHENZHEN_PREFIX)
    if not hk_mask.any() or not sz_mask.any():
        return g

    sub = g.loc[hk_mask | sz_mask]
    merged = shapely.union_all(sub.geometry.values)
    parts = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]

    holes = [
        shapely.Polygon(ring)
        for part in parts
        for ring in part.interiors
        if shapely.Polygon(ring).area >= GAP_MIN_AREA_M2
    ]
    if not holes:
        print("\n香港/深圳交界: 无待填补无效区")
        return g

    tree = sub.sindex
    assign: dict = {}
    for hole in holes:
        cand = list(tree.query(hole, predicate="intersects"))
        if not cand:
            continue
        feats = sub.iloc[cand]
        if feats["省"].nunique() < 2:
            continue  # 只处理横跨香港/深圳两边的无效区
        best_label, best_len = None, -1.0
        for pos in cand:
            feat = sub.iloc[int(pos)]
            shared = hole.boundary.intersection(feat.geometry.boundary).length
            if shared > best_len:
                best_label, best_len = feat.name, shared
        assign.setdefault(best_label, []).append(hole)

    if not assign:
        print("\n香港/深圳交界: 无横跨两地的无效区")
        return g

    out = g.copy()
    for label, hs in assign.items():
        geom = shapely.union_all([out.at[label, "geometry"], *hs])
        out.at[label, "geometry"] = clean_geometry(geom)
    n = sum(len(v) for v in assign.values())
    area = sum(h.area for v in assign.values() for h in v) / 1e6
    print(f"\n香港/深圳交界: 无效区 {n} 个、{area:.3f} km2 已按就近划给行政区")
    return out


def repair_invalid(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """修复自相交/碎片拓扑错误：make_valid + 去碎片 + 提取面状部分。"""
    n_invalid = int((~g.geometry.is_valid).sum())
    n_sliver = 0
    for geom in g.geometry:
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            n_sliver += sum(1 for p in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]) if p.area < MIN_AREA_M2)

    print(f"\n几何检查: 无效几何 {n_invalid} 个，含碎片要素 {n_sliver} 个")

    fixed = g.geometry.make_valid()
    out = g.copy()
    out["geometry"] = [clean_geometry(fixed.iloc[i]) for i in range(len(out))]

    still = ~out.geometry.is_valid
    if still.any():
        out.loc[still, "geometry"] = out.loc[still, "geometry"].buffer(0)
        print(f"  buffer(0) 二次修复: {int(still.sum())} 个")

    out = out[out.geometry.notna() & ~out.geometry.is_empty]
    out = out.set_geometry("geometry")
    print(f"  清理后要素数: {len(out)}，无效几何: {(~out.geometry.is_valid).sum()}")
    return out


def fix_names(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """把转移涉及区域的 市/县 代码换成中文地名。"""
    out = g.copy()

    city = out["code4"].map(CITY_NAMES)
    m = city.notna() & (out["市"] != city)
    if m.any():
        print(f"\n字段规范化: 市 代码->地名 {int(m.sum())} 个要素")
        out.loc[m, "市"] = city[m]
        out.loc[m, ["code4", "市"]].drop_duplicates().to_string()

    county = out["code6"].map(COUNTY_NAMES)
    m = county.notna() & (out["县"] != county)
    if m.any():
        print(f"\n字段规范化: 县 代码->地名 {int(m.sum())} 个要素")
        out.loc[m, "县"] = county[m]
        out.loc[m, ["code6", "县"]].drop_duplicates().to_string()

    for code6, name in CITY_OVERRIDE.items():
        m = out["code6"] == code6
        if m.any():
            print(f"\n字段规范化: 市 覆盖 {code6} -> {name}（{int(m.sum())} 个要素）")
            out.loc[m, "市"] = name

    m = out["乡镇码"] == "RED_ORANGE"
    if m.any():
        print(f"\n字段规范化: 红+橙合并区划给钦州市（{int(m.sum())} 个要素）")
        out.loc[m, "省"] = "广东省"
        out.loc[m, "市"] = "钦州市"

    return out


def report(g: gpd.GeoDataFrame) -> None:
    print("\n=== 调整前各省 ===")
    print(g.groupby("原省").agg(要素数=("乡镇码", "size"), km2=("面积km2", "sum")).round(1).to_string())
    print("\n=== 调整后各省 ===")
    print(g.groupby("省").agg(要素数=("乡镇码", "size"), km2=("面积km2", "sum")).round(1).to_string())


def save_preview(g: gpd.GeoDataFrame, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(13, 10))
    for prov, grp in g.groupby("省"):
        grp.plot(
            ax=ax,
            color=COLORS.get(prov, "#e6e6e6"),
            edgecolor="#666666",
            linewidth=0.2,
        )
    ax.set_axis_off()
    ax.set_title("省界调整后的华南乡镇界线")
    handles = [Patch(facecolor=c, edgecolor="#666666", label=p) for p, c in COLORS.items()]
    ax.legend(handles=handles, loc="lower right", frameon=False)

    hkmo = g[g["省"].isin(["香港特别行政区", "澳门特别行政区"])]
    if len(hkmo):
        axin = ax.inset_axes([0.02, 0.02, 0.32, 0.32])
        for prov, grp in hkmo.groupby("省"):
            grp.plot(ax=axin, color=COLORS.get(prov, "#e6e6e6"), edgecolor="#444444", linewidth=0.4)
        xmin, ymin, xmax, ymax = hkmo.total_bounds
        axin.set_xlim(xmin - 800, xmax + 800)
        axin.set_ylim(ymin - 800, ymax + 800)
        axin.set_xticks([])
        axin.set_yticks([])
        axin.set_title("港澳放大", fontsize=9)

    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="调整华南乡镇界线的省份归属")
    ap.add_argument("--inplace", action="store_true", help="覆盖原文件")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    ap.add_argument("--no-png", action="store_true", help="不输出预览图")
    args = ap.parse_args()

    g = load_merged()
    g = repair_invalid(g)
    g = take_shenzhen_boundary(g)
    g = fill_hk_shenzhen_gaps(g)
    out = apply_rules(g)
    out = fix_names(out)
    report(out)

    if args.dry_run:
        print("\n(--dry-run，未写出文件)")
        return

    dst = SRC_SHP if args.inplace else OUT_SHP
    keep = [c for c in out.columns if c not in ("code4", "code6", "原省")]
    out[keep].to_file(dst, encoding="UTF-8")
    print("\n已写出:", dst)

    if not args.no_png:
        png = OUT_PNG if not args.inplace else HERE / "华南乡镇界线_preview.png"
        save_preview(out, png)
        print("已写出:", png)


if __name__ == "__main__":
    main()
