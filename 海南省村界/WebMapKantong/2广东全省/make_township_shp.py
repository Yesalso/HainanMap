import sys
from pathlib import Path

import pandas as pd
import geopandas as gpd
import shapely

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

SRC = Path(__file__).resolve().parent / "广东省CJ.shp"
OUT = Path(__file__).resolve().parent / "广东省乡镇界线.shp"

COLS = ["NAME", "省级", "市级", "区县级", "乡镇级", "乡镇码", "geometry"]


def first_valid(series: pd.Series) -> str:
    s = series.dropna().astype(str).str.strip()
    s = s[s != ""]
    return s.iloc[0] if len(s) else ""


def merge_group(geoms):
    try:
        return shapely.union_all(geoms)
    except Exception:
        fixed = [gm if gm.is_valid else gm.buffer(0) for gm in geoms]
        return shapely.union_all(fixed)


def main():
    print("读取:", SRC)
    g = gpd.read_file(SRC, encoding="GBK", columns=COLS)
    print("要素数:", len(g))

    code = g["乡镇码"].astype(str).str.strip()
    valid = code.str.match(r"^\d{9,12}$", na=False)
    if (~valid).any():
        print("剔除乡镇码异常要素:", int((~valid).sum()))
        g = g[valid]

    g["code9"] = g["乡镇码"].astype(str).str.strip().str[:9]

    bad = g["code9"].str.endswith("000")
    if bad.any():
        print("剔除无效乡镇代码要素:", int(bad.sum()))
        g = g[~bad]

    n = g["code9"].nunique()
    print("待融合乡镇代码数:", n)

    rows = []
    for i, (code9, grp) in enumerate(g.groupby("code9", sort=False), 1):
        geoms = [gm for gm in grp.geometry if gm is not None and not gm.is_empty]
        if not geoms:
            continue
        rows.append(
            {
                "省级": first_valid(grp["省级"]),
                "市级": first_valid(grp["市级"]),
                "区县级": first_valid(grp["区县级"]),
                "乡镇级": first_valid(grp["乡镇级"]) or first_valid(grp["NAME"]),
                "乡镇码": code9,
                "要素数": len(grp),
                "geometry": merge_group(geoms),
            }
        )
        if i % 200 == 0:
            print(f"融合进度 {i}/{n}")

    out = gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=g.crs)
    out = out.sort_values("乡镇码").reset_index(drop=True)
    out = out[["省级", "市级", "区县级", "乡镇级", "乡镇码", "要素数", "geometry"]]

    out.to_file(OUT, encoding="GBK")
    print("已写出:", OUT)

    chk = gpd.read_file(OUT, encoding="GBK")
    print("输出要素数:", len(chk))
    print("各地级市乡镇数量:")
    print(chk["市级"].value_counts().to_string())
    print("预览:")
    print(chk[["乡镇码", "乡镇级", "市级", "区县级", "要素数"]].head(10).to_string())


if __name__ == "__main__":
    main()
