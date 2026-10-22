# -*- coding: utf-8 -*-
"""合并广东、广西、海南、香港的乡镇/区界线为一份地图数据。

输入（与本脚本同目录）：
    广东省乡镇界线.shp                       EPSG:3857
    2广西村界/广西乡镇界线.shp               EPSG:4490
    行政区划/Hainan_town_topo_v3.shp         CGCS2000 Albers
    行政区划/香港/香港18区.shp               EPSG:2326（由细分单元合并生成的 18 区）
    行政区划/澳门/行政区划.shp               EPSG:4326（澳门 11 个堂区/填海区）

输出：
    华南乡镇界线.shp                         统一字段 + CGCS2000 Albers
    华南乡镇界线_preview.png                 快速预览图
    华南乡镇界线.geojson                     可选，--geojson

用法：
    python merge_southchina_township.py
    python merge_southchina_township.py --geojson

注意：香港与深圳交界存在重叠时，重叠部分一律取深圳界线（从香港面中扣除）。
    交界中间的无效空洞由 adjust_province_transfer.py 就近划给相邻行政区。
    本脚本产出的是「现行政区划」底图。1949-1951 年两广分界的调整
（北海/钦州/防城港划入广东、怀集县划入广西、市县字段规范化、几何修复）
由 adjust_province_transfer.py 完成，重跑本脚本后需再执行：
    python adjust_province_transfer.py --inplace
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from matplotlib.patches import Patch
from pyproj import CRS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
GD_SHP = HERE / "广东省乡镇界线.shp"
GX_SHP = HERE / "2广西村界" / "广西乡镇界线.shp"
HN_SHP = HERE / "行政区划" / "Hainan_town_topo_v3.shp"
HK_SHP = HERE / "行政区划" / "香港" / "香港18区.shp"
MO_SHP = HERE / "行政区划" / "澳门" / "行政区划.shp"
OUT_SHP = HERE / "华南乡镇界线.shp"
OUT_JSON = HERE / "华南乡镇界线.geojson"
OUT_PNG = HERE / "华南乡镇界线_preview.png"

# CGCS2000 Albers，中央经线 110°，标准纬线 21°/27°（覆盖华南）
TARGET_CRS = CRS.from_proj4(
    "+proj=aea +lat_1=21 +lat_2=27 +lat_0=0 +lon_0=110 "
    "+x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs"
)

COLUMNS = ["省", "市", "县", "乡镇", "乡镇码", "要素数", "geometry"]


def load_guangdong() -> gpd.GeoDataFrame:
    g = gpd.read_file(
        GD_SHP,
        encoding="GBK",
        columns=["省级", "市级", "区县级", "乡镇级", "乡镇码", "要素数", "geometry"],
    )
    g = g.rename(columns={"省级": "省", "市级": "市", "区县级": "县", "乡镇级": "乡镇"})
    g["省"] = g["省"].fillna("广东省")
    return g[COLUMNS]


def load_guangxi() -> gpd.GeoDataFrame:
    g = gpd.read_file(
        GX_SHP,
        encoding="GBK",
        columns=["市级代码", "县代码", "乡镇级", "乡镇码", "要素数", "geometry"],
    )
    # 广西源数据只有代码，没有市县名称
    g["省"] = "广西壮族自治区"
    g["市"] = g["市级代码"].astype(str)
    g["县"] = g["县代码"].astype(str)
    g["乡镇"] = g["乡镇级"].fillna("")
    return g[COLUMNS]


def load_hainan() -> gpd.GeoDataFrame:
    g = gpd.read_file(
        HN_SHP,
        encoding="UTF-8",
        columns=["CODE", "TOWN", "CITY", "N_FEAT", "geometry"],
    )
    g["省"] = "海南省"
    g["市"] = g["CITY"].fillna("")
    g["县"] = ""
    g["乡镇"] = g["TOWN"].fillna("")
    g["乡镇码"] = g["CODE"].astype(str)
    g["要素数"] = g["N_FEAT"]
    return g[COLUMNS]


def load_hongkong() -> gpd.GeoDataFrame:
    g = gpd.read_file(
        HK_SHP,
        encoding="UTF-8",
        columns=["省", "市", "县", "乡镇", "乡镇码", "要素数", "geometry"],
    )
    g["省"] = g["省"].fillna("香港特别行政区")
    g["市"] = g["市"].fillna("香港特别行政区")
    g["县"] = g["县"].fillna("")
    g["乡镇"] = g["乡镇"].fillna("")
    g["乡镇码"] = g["乡镇码"].astype(str)
    return g[COLUMNS]


def load_macau() -> gpd.GeoDataFrame:
    g = gpd.read_file(
        MO_SHP,
        encoding="UTF-8",
        columns=["OBJECTID", "CNAME", "SNAME", "geometry"],
    )
    # CNAME=繁体中文，SNAME=简体中文，PNAME=葡萄牙语
    g["省"] = "澳门特别行政区"
    g["市"] = "澳门特别行政区"
    g["县"] = ""
    g["乡镇"] = g["SNAME"].fillna(g["CNAME"]).fillna("")
    g["乡镇码"] = "MO" + g["OBJECTID"].astype("int").map(lambda v: f"{v:02d}")
    g["要素数"] = 1
    return g[COLUMNS]


SHENZHEN_PREFIX = "4403"


def polygon_only(geom, min_area=1.0):
    """保留面状部分，处理 difference 产生的 GeometryCollection。"""
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


def take_shenzhen_boundary(hk, gd):
    """香港与深圳交界有重叠时，重叠部分一律取深圳界线（从香港面中扣除）。"""
    sz = gd[gd["乡镇码"].astype(str).str.startswith(SHENZHEN_PREFIX)]
    if hk.empty or sz.empty:
        return hk
    boundary = shapely.union_all(sz.geometry.values)
    before = hk.geometry.area.sum() / 1e6
    out = hk.copy()
    out["geometry"] = [polygon_only(g.difference(boundary)) for g in out.geometry]
    out = out[out.geometry.notna() & ~out.geometry.is_empty].copy()
    after = out.geometry.area.sum() / 1e6
    print(f"  香港-深圳重叠划归深圳: {before - after:.3f} km2，香港要素 {len(out)}")
    return out


def merge(layers: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    out = gpd.GeoDataFrame(
        pd.concat(layers, ignore_index=True), geometry="geometry", crs=TARGET_CRS
    )
    out["乡镇码"] = out["乡镇码"].astype(str)
    out["要素数"] = out["要素数"].fillna(0).astype("int32")
    return out.sort_values("乡镇码").reset_index(drop=True)


def save_preview(gdf: gpd.GeoDataFrame, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False

    colors = {
        "广东省": "#cfe8ff",
        "广西壮族自治区": "#ffe3bf",
        "海南省": "#d6f0d6",
        "香港特别行政区": "#f7cfe0",
        "澳门特别行政区": "#f5c9a0",
    }
    fig, ax = plt.subplots(figsize=(13, 10))
    for prov, grp in gdf.groupby("省"):
        grp.plot(
            ax=ax,
            color=colors.get(prov, "#e6e6e6"),
            edgecolor="#666666",
            linewidth=0.2,
        )
    ax.set_axis_off()
    ax.set_title("广东、广西、海南、香港、澳门乡镇（区）界线")
    handles = [Patch(facecolor=c, edgecolor="#666666", label=p) for p, c in colors.items()]
    ax.legend(handles=handles, loc="lower right", frameon=False)

    # 港澳在全区图上很小，加一个局部放大插图
    hkmo = gdf[gdf["省"].isin(["香港特别行政区", "澳门特别行政区"])]
    if len(hkmo):
        axin = ax.inset_axes([0.02, 0.02, 0.32, 0.32])
        for prov, grp in hkmo.groupby("省"):
            grp.plot(
                ax=axin,
                color=colors.get(prov, "#e6e6e6"),
                edgecolor="#444444",
                linewidth=0.4,
            )
        xmin, ymin, xmax, ymax = hkmo.total_bounds
        pad_x = max((xmax - xmin) * 0.08, 800)
        pad_y = max((ymax - ymin) * 0.08, 800)
        axin.set_xlim(xmin - pad_x, xmax + pad_x)
        axin.set_ylim(ymin - pad_y, ymax + pad_y)
        axin.set_xticks([])
        axin.set_yticks([])
        for spine in axin.spines.values():
            spine.set_edgecolor("#888888")
        axin.set_title("港澳放大", fontsize=9)

    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="合并广东、广西、海南、香港、澳门乡镇界线")
    ap.add_argument("--geojson", action="store_true", help="同时输出 GeoJSON")
    ap.add_argument("--no-png", action="store_true", help="不输出预览图")
    args = ap.parse_args()

    sources = [
        ("广东", GD_SHP, load_guangdong),
        ("广西", GX_SHP, load_guangxi),
        ("海南", HN_SHP, load_hainan),
        ("香港", HK_SHP, load_hongkong),
        ("澳门", MO_SHP, load_macau),
    ]
    for _, path, _ in sources:
        if not path.exists():
            sys.exit(f"找不到输入文件: {path}")

    loaded = {}
    for name, path, loader in sources:
        print(f"读取{name}:", path)
        layer = loader().to_crs(TARGET_CRS)
        print("  要素数:", len(layer))
        loaded[name] = layer

    loaded["香港"] = take_shenzhen_boundary(loaded["香港"], loaded["广东"])
    out = merge([loaded[name] for name, _, _ in sources])
    print("合并后要素数:", len(out))
    print(out["省"].value_counts().to_string())

    out.to_file(OUT_SHP, encoding="UTF-8")
    print("已写出:", OUT_SHP)

    if args.geojson:
        out.to_crs("EPSG:4326").to_file(OUT_JSON, driver="GeoJSON", encoding="UTF-8")
        print("已写出:", OUT_JSON)

    if not args.no_png:
        save_preview(out, OUT_PNG)
        print("已写出:", OUT_PNG)

    chk = gpd.read_file(OUT_SHP, encoding="UTF-8", columns=["省", "乡镇码"])
    print("校验输出要素数:", len(chk), "CRS:", chk.crs)


if __name__ == "__main__":
    main()
