# -*- coding: utf-8 -*-
"""
按 Haikou_2002_a.png 的色块，把海口 9 个 2002 乡镇"以村界为单位"切出（写回 Hainan2002/Hainan_town.shp）。

思路（采纳 2002海口转换总结.txt 的"原始画法"）：
  * 手绘图 30m/px 且为手绘，直接栅格矢量化边界必然有量化/配准误差，产生"双线/细缝"。
  * 因此新乡镇边界不采用栅格线，而是"归并村界"：把落在该新乡镇色块内的
    村级面整块并入 → 新乡镇边界 = 村界（与 base 严丝合缝，无缝隙）。
  * 每个村按其在色块图中的多数颜色归属；父级乡镇 = 原几何 − 新乡镇。
  * 新乡镇再与"现父级几何"求交，保证海岸线/县市边界/周边界线全部沿用现状。

输出：town/Hainan2002/Hainan_town.shp（海口块 41 → 50 单元）
"""

import os
import shutil

import numpy as np
import pandas as pd
import geopandas as gpd
from PIL import Image
from shapely.geometry import MultiPolygon
from shapely.ops import unary_union
from shapely.validation import make_valid
from rasterio.transform import Affine
from rasterio.features import rasterize

# ==================== 配置 ====================
BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN = os.path.join(BASE, "town")
EMPTY = os.path.join(TOWN, "Empty_map")
VILL = os.path.join(BASE, "海南村界.shp")
HT = os.path.join(TOWN, "Hainan2002", "Hainan_town.shp")
HT_BAK = os.path.join(TOWN, "Hainan2002", "Hainan_town_before_haikou2002.shp")

TARGET_CRS = "EPSG:32649"
HAIKOU = "4601"
Wr, Hr, OX, OY = 2036, 2097, 0, 310
SHP_ENCODING = "gbk"

COLORS = [
    ("新海乡", "00A2E8"), ("薛样乡", "7F7F7F"), ("美安镇", "880015"),
    ("东营镇", "3F48CC"), ("桂林洋镇", "B5E61D"), ("演海镇", "22B14C"),
    ("美仁坡乡", "C3C3C3"), ("新民乡", "73FBFD"), ("谭文镇", "FFAEC9"),
]
PARENTS = {
    "新海乡": ["西秀镇", "长流镇"],
    "薛样乡": ["城西镇"], "美安镇": ["石山镇"], "东营镇": ["灵山镇"],
    "桂林洋镇": ["演丰镇"], "演海镇": ["三江镇", "演丰镇"],
    "美仁坡乡": ["龙泉镇"], "新民乡": ["甲子镇"], "谭文镇": ["三门坡镇"],
}
WEST = ["新海乡", "长流镇", "西秀镇"]
WEST_PARENTS = ["西秀镇", "长流镇"]
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
# ==============================================


def sanitize(g):
    if g is None or g.is_empty:
        return g
    if not g.is_valid:
        try:
            g = make_valid(g)
        except Exception:
            g = g.buffer(0)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else g.buffer(0)


def as_multi(g):
    g = sanitize(g)
    if g is None or g.is_empty:
        return g
    if g.geom_type == "Polygon":
        return MultiPolygon([g])
    return g


def write_shp(gdf, path, encoding="utf-8"):
    tmp = path[:-4] + "__tmp.shp"
    for ext in SIDECARS:
        p = tmp[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    gdf.to_file(tmp, encoding=encoding)
    for ext in SIDECARS:
        s, d = tmp[:-4] + ext, path[:-4] + ext
        if os.path.exists(s):
            os.replace(s, d)


def main():
    # ---------- 1. base（现状，含已调好的海岸线） ----------
    src = gpd.read_file(HT, encoding="utf-8")
    src["CODE"] = src["CODE"].astype(str)
    is_hk = src["CODE"].str.startswith(HAIKOU) | (src["CITY"].astype(str) == "海口市")
    keep = src[~is_hk].copy().reset_index(drop=True)
    hk = src[is_hk].copy().to_crs(TARGET_CRS).reset_index(drop=True)
    print(f"全岛 {len(src)}：海口 {len(hk)} + 其余 {len(keep)}")
    base_geo = {hk.CODE[i]: hk.geometry[i] for i in hk.index}
    code_town = dict(zip(hk.CODE, hk.TOWN))

    minx, miny, maxx, maxy = unary_union([x.buffer(0) for x in hk.geometry]).bounds
    sx = (maxx - minx) / Wr
    sy = (maxy - miny) / Hr
    tr = Affine(sx, 0, minx, 0, -sy, maxy)
    print(f"bbox {minx:.1f},{miny:.1f}→{maxx:.1f},{maxy:.1f} 格网 {sx:.2f}m")

    # ---------- 2. 色块类别栅格 ----------
    aimg = np.array(Image.open(os.path.join(EMPTY, "Haikou_2002_a.png")).convert("RGB")).astype(np.int32)
    acrop = aimg[OY:OY + Hr, OX:OX + Wr]
    cls = np.zeros((Hr, Wr), np.int8)
    for k, (nm, hx) in enumerate(COLORS, 1):
        rgb = np.array([int(hx[i:i + 2], 16) for i in (0, 2, 4)])
        cls[np.abs(acrop - rgb).max(2) <= 20] = k
    color_id = {nm: k for k, (nm, _) in enumerate(COLORS, 1)}

    # ---------- 3. 读村界，按多数颜色归入新乡镇 ----------
    v = gpd.read_file(VILL, encoding=SHP_ENCODING)
    v["CODE"] = v["XZQDM"].astype(str).str[:9]
    v = v[~v["XZQMC"].astype(str).str.contains("国有滩涂")].copy()
    v = v[v["CODE"].str.startswith(HAIKOU)].to_crs(TARGET_CRS).reset_index(drop=True)
    print(f"海口村级要素 {len(v)}")

    # 新乡镇 -> 允许的父级 CODE 集合
    parent_codes = {nm: {c for c, t in code_town.items() if t in ps}
                    for nm, ps in PARENTS.items()}
    # 西部：西秀/长流 的村，颜色为新海乡 → 新海乡
    assigned = {nm: [] for nm, _ in COLORS}
    assigned["长流镇"] = []
    assigned["西秀镇"] = []

    for i in v.index:
        geom = v.geometry[i].buffer(0)
        code = v.CODE[i]
        if geom.is_empty or geom.area <= 0:
            continue
        r = rasterize([(geom, 1)], out_shape=(Hr, Wr), transform=tr,
                      fill=0, dtype="uint8") > 0
        if not r.any():
            continue
        cnt = np.bincount(cls[r].ravel(), minlength=len(COLORS) + 1)
        if cnt.sum() == 0:
            continue
        cc = cnt.copy()
        cc[0] = 0                       # 0=无色，不作为竞争类
        k = int(cc.argmax())
        if cc[k] == 0 or cc[k] / float(r.sum()) < 0.30:
            continue
        nm = COLORS[k - 1][0]
        if nm == "新海乡":
            if code in parent_codes["新海乡"]:
                assigned["新海乡"].append(geom)
        elif nm in ("长流镇", "西秀镇"):
            continue
        elif code in parent_codes[nm]:
            assigned[nm].append(geom)
    for nm, _ in COLORS:
        print(f"  {nm:6s} 归入村数 {len(assigned[nm])}")

    # ---------- 4. 生成新几何 ----------
    new_geoms = {}
    # 西部：新海乡 = 村并 ∩ (西秀∪长流)；西秀/长流 = 现状 − 新海乡
    west_u = unary_union([base_geo[c] for c, t in code_town.items() if t in WEST_PARENTS]).buffer(0)
    if assigned["新海乡"]:
        xh = sanitize(unary_union(assigned["新海乡"]).intersection(west_u))
    else:
        xh = None
    new_geoms["新海乡"] = xh
    for t in WEST_PARENTS:
        c = [c for c, tt in code_town.items() if tt == t][0]
        new_geoms[t] = sanitize(base_geo[c].difference(xh)) if xh is not None else base_geo[c]

    # 其余 8 个：村并 ∩ 父级并集；父级 = 现状 − 新乡镇
    carved = {}          # 父级 code -> 要减去的并集
    for nm, _ in COLORS:
        if nm == "新海乡":
            continue
        if not assigned[nm]:
            new_geoms[nm] = None
            continue
        pu = unary_union([base_geo[c] for c in parent_codes[nm]]).buffer(0)
        g = sanitize(unary_union(assigned[nm]).intersection(pu))
        new_geoms[nm] = g
        for c in parent_codes[nm]:
            carved.setdefault(c, []).append(g)

    # ---------- 5. 组装海口图层 ----------
    rows = []
    for i in hk.index:
        c, t = hk.CODE[i], hk.TOWN[i]
        g = hk.geometry[i]
        if t in new_geoms and new_geoms[t] is not None:
            g = new_geoms[t]
        elif c in carved:
            g = sanitize(g.difference(unary_union(carved[c]).buffer(0)))
        rows.append(dict(CODE=c, TOWN=t, CITY=hk.CITY[i], EN=hk.EN[i],
                         N_FEAT=int(hk.N_FEAT[i]), geometry=g))
    k = 0
    for nm, _ in COLORS:
        if new_geoms.get(nm) is None:
            print(f"  !! {nm} 未生成")
            continue
        k += 1
        rows.append(dict(CODE="HK%02d" % k, TOWN=nm, CITY="海口市", EN="Haikou",
                         N_FEAT=0, geometry=new_geoms[nm]))

    out_hk = gpd.GeoDataFrame(rows, geometry="geometry", crs=TARGET_CRS)
    out_hk["geometry"] = out_hk.geometry.apply(as_multi)
    out_hk["AREA_KM2"] = (out_hk.geometry.area / 1e6).round(3)
    out_hk = out_hk[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
    print(f"\n新海口单元 {len(out_hk)}")
    print(out_hk[out_hk.CODE.astype(str).str.startswith("HK")][
        ["CODE", "TOWN", "AREA_KM2"]].to_string(index=False))

    # ---------- 6. 校验 ----------
    uu_old = unary_union([x.buffer(0) for x in hk.geometry]).buffer(0)
    uu_new = unary_union([x.buffer(0) for x in out_hk.geometry]).buffer(0)
    print(f"\n面积 old={uu_old.area/1e6:.3f} new={uu_new.area/1e6:.3f}"
          f" 差={(uu_new.area-uu_old.area)/1e6:+.6f}")
    ov = 0.0
    gl = list(out_hk.geometry)
    for a in range(len(gl)):
        for b in range(a + 1, len(gl)):
            if gl[a].intersects(gl[b]):
                ov += gl[a].intersection(gl[b]).area
    print(f"内部重叠 {ov/1e6:.6f}  无效 {int((~out_hk.geometry.is_valid).sum())}/{len(out_hk)}")
    print(f"与原轮廓对称差 {uu_old.symmetric_difference(uu_new).area/1e6:.6f}")

    # ---------- 7. 写回 ----------
    if not os.path.exists(HT_BAK):
        for ext in SIDECARS:
            shutil.copy2(HT[:-4] + ext, HT_BAK[:-4] + ext)
        print("已备份 →", HT_BAK)
    merged = gpd.GeoDataFrame(
        pd.concat([out_hk.to_crs(src.crs), keep], ignore_index=True), crs=src.crs)
    merged["geometry"] = merged.geometry.apply(as_multi)
    write_shp(merged, HT)
    print(f"已写回 {HT}：{len(merged)} 单元")


if __name__ == "__main__":
    main()
