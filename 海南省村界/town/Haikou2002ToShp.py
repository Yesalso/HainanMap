# -*- coding: utf-8 -*-
"""
把手动调整的海口 2002 乡镇图 (town/Haikou_2002.png) 反向映射回 SHP 边界。

与三亚不同：海口的海岸线有变化，因此直接采用 2002 图自身的海岸线（不再用 SHP 外边界约束）。
手绘误差产生的细缝/碎块统一归入最近区域，保证相邻区域共用一条线（无缝无重叠）。
区域名称暂用 1、2、… 数字编号。

输出：
  town/海口2002.shp          海口 2002 乡镇（Albers）
  town/Hainan_town.shp       用上述海口边界替换全岛图层中的海口部分
"""

import os
import shutil
import numpy as np
import cv2
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid
from skimage import measure

# ==================== 配置 ====================
BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN_DIR = os.path.join(BASE, "town")

SHP_PATH = os.path.join(BASE, "海南村界.shp")
IMG_2002 = os.path.join(TOWN_DIR, "Haikou_2002.png")
IMG_REF = os.path.join(TOWN_DIR, "Haikou_2012.png")   # 用于确定图纸偏移，可不存在

OUT_SANYA = os.path.join(TOWN_DIR, "海口2002.shp")
HAINAN_TOWN = os.path.join(TOWN_DIR, "Hainan_town.shp")
HAINAN_TOWN_BAK = os.path.join(TOWN_DIR, "Hainan_town_backup.shp")

SHP_ENCODING = "gbk"
OUT_ENCODING = "utf-8"
TARGET_CRS = "EPSG:32649"
HAIKOU_PREFIX = "4601"

MIN_MAIN_PX = 2000     # 小于此面积的白色区域视为碎块，并入最近区域
CLOSE_ITER = 2
# ==============================================


def sanitize_polygonal(geom):
    if geom is None or geom.is_empty:
        return geom
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else geom.buffer(0)


def find_offset(dark02, dark_ref):
    """在 2002 大画布上找到参考图的左上角偏移（按重合像素最大化）。"""
    Hr, Wr = dark_ref.shape
    H2, W2 = dark02.shape
    best, best_ov = (0, 0), -1
    for dy in range(0, H2 - Hr + 1):
        ov = int((dark02[dy:dy + Hr, :Wr] & dark_ref).sum())
        if ov > best_ov:
            best_ov, best = ov, (0, dy)
    return best, best_ov


def main():
    # ---------- 1. 读取手绘图与参考图，确定图纸偏移 ----------
    g02 = cv2.imdecode(np.fromfile(IMG_2002, np.uint8), cv2.IMREAD_GRAYSCALE)
    H, W = g02.shape
    dark02 = (g02 < 128)
    gref = cv2.imdecode(np.fromfile(IMG_REF, np.uint8), cv2.IMREAD_GRAYSCALE) \
        if os.path.exists(IMG_REF) else None
    if gref is not None:
        (ox, oy), ov = find_offset(dark02, gref < 128)
        print(f"对齐参考图：偏移=({ox},{oy})，重合像素={ov}")
    else:
        ox, oy = 0, 0
    # 参考图的地理范围（与 SHP 海口范围一致）
    gdf = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING).to_crs(TARGET_CRS)
    gdf = gdf[~gdf["XZQMC"].astype(str).str.startswith("三沙市")].copy()
    gdf["code"] = gdf["XZQDM"].astype(str).str[:9]
    hk = gdf[gdf["code"].str.startswith(HAIKOU_PREFIX)].copy()
    minx, miny, maxx, maxy = hk.total_bounds
    Wr, Hr = (gref.shape[1], gref.shape[0]) if gref is not None else (W, H)
    sx, sy = (maxx - minx) / Wr, (maxy - miny) / Hr

    def px2geo(x, y):
        return (minx + (x - ox) * sx, maxy - (y - oy) * sy)

    # ---------- 2. 线网与陆地分区 ----------
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    net = cv2.morphologyEx(dark02.astype(np.uint8), cv2.MORPH_CLOSE, k, iterations=CLOSE_ITER)
    net = cv2.dilate(net, k, iterations=1)
    white = (net == 0).astype(np.uint8)
    nreg, rlab, rstats, rcent = cv2.connectedComponentsWithStats(white, 8)
    sea = set(rlab[0, :]) | set(rlab[-1, :]) | set(rlab[:, 0]) | set(rlab[:, -1])
    sea.discard(0)   # 0 是线网像素（深色），不是海域

    seeds = []
    for i in range(1, nreg):
        if i in sea:
            continue
        if int(rstats[i, cv2.CC_STAT_AREA]) >= MIN_MAIN_PX:
            seeds.append([rlab == i, (rcent[i][0], rcent[i][1])])
    print(f"陆地大区域 {len(seeds)} 个")

    # ---------- 3. 线网/细缝/碎块 → 归最近区域 ----------
    seed_lab = np.zeros((H, W), np.int32)
    for idx, s in enumerate(seeds, 1):
        seed_lab[s[0]] = idx
    land_mask = ~np.isin(rlab, list(sea))
    assign = land_mask & (seed_lab == 0)
    best_d = np.full((H, W), np.inf, np.float32)
    best_id = np.zeros((H, W), np.int32)
    for idx, s in enumerate(seeds, 1):
        dt = cv2.distanceTransform((s[0] == 0).astype(np.uint8), cv2.DIST_L2, 5)
        upd = dt < best_d
        best_d[upd] = dt[upd]
        best_id[upd] = idx
    final_lab = seed_lab.copy()
    final_lab[assign] = best_id[assign]

    # 海岸线：线网像素由"陆地/海域"各取一半，使外边界落在海岸线中心线上
    sea_mask = np.isin(rlab, list(sea))
    dt_sea = cv2.distanceTransform((sea_mask == 0).astype(np.uint8), cv2.DIST_L2, 5)
    final_lab[assign & (dt_sea < best_d)] = 0

    # 细缝/碎块整体并入其被归入最多的区域，避免在缝隙正中间画出一条新线
    is_seed = seed_lab > 0
    for i in range(1, nreg):
        if i in sea:
            continue
        m = (rlab == i) & (~is_seed)
        if not m.any():
            continue
        labels, counts = np.unique(final_lab[m], return_counts=True)
        valid = labels > 0
        if valid.any():
            final_lab[m] = labels[valid][np.argmax(counts[valid])]

    # ---------- 4. 栅格 → 矢量 ----------
    # 沿像素边界(0.5 等值线)取轮廓：相邻区域恰好共用同一条线，无缝无重叠。
    def label_to_geo(idx):
        polys = []
        for c in measure.find_contours((final_lab == idx).astype(np.float32), 0.5):
            if len(c) < 4:
                continue
            p = Polygon([px2geo(float(col), float(row)) for row, col in c]).buffer(0)
            if not p.is_empty and p.area > 0:
                polys.append(p)
        if not polys:
            return None
        outer = max(polys, key=lambda p: p.area)
        holes = [p for p in polys if p is not outer and outer.contains(p)]
        if holes:
            outer = outer.difference(unary_union(holes))
        return outer if not outer.is_empty else None

    regions = []
    for idx in range(1, len(seeds) + 1):
        g = label_to_geo(idx)
        if g is not None and not g.is_empty:
            g = sanitize_polygonal(g)
            if g is not None and not g.is_empty and g.area > 0:
                regions.append(g)
    regions.sort(key=lambda g: -g.area)
    print(f"矢量化区域 {len(regions)} 个")

    rows = [{"TOWN": str(i), "AREA_KM2": round(g.area / 1e6, 3), "geometry": g}
            for i, g in enumerate(regions, 1)]
    for r in rows:
        print(f"  {r['TOWN']:>2}  {r['AREA_KM2']:>8.2f} km²")

    # ---------- 5. 输出 ----------
    src_prj = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING, rows=1).crs
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=TARGET_CRS).to_crs(src_prj)
    out["geometry"] = out.geometry.apply(sanitize_polygonal)
    out.insert(0, "CODE", [f"HK{i:02d}" for i in range(1, len(out) + 1)])
    out.insert(3, "CITY", "海口市")
    out.insert(4, "EN", "Haikou")
    out.insert(5, "N_FEAT", 0)
    out = out[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = OUT_SANYA[:-4] + ext
        if os.path.exists(p):
            os.remove(p)
    out.to_file(OUT_SANYA, encoding=OUT_ENCODING)
    print(f"已输出海口：{OUT_SANYA}")

    hainan = gpd.read_file(HAINAN_TOWN, encoding=OUT_ENCODING)
    is_hk = (hainan["CODE"].astype(str).str.startswith(HAIKOU_PREFIX)
             | (hainan["CITY"].astype(str) == "海口市"))
    if is_hk.any():
        if not os.path.exists(HAINAN_TOWN_BAK):
            shutil.copyfile(HAINAN_TOWN, HAINAN_TOWN_BAK)
            print(f"已备份原全岛图层：{HAINAN_TOWN_BAK}")
        keep = hainan[~is_hk].copy()
        merged = gpd.GeoDataFrame(
            pd.concat([keep, out.to_crs(hainan.crs)], ignore_index=True), crs=hainan.crs)
        merged["geometry"] = merged.geometry.apply(sanitize_polygonal)
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            p = HAINAN_TOWN[:-4] + ext
            if os.path.exists(p):
                os.remove(p)
        merged.to_file(HAINAN_TOWN, encoding=OUT_ENCODING)
        print(f"已替换全岛图层海口部分：{HAINAN_TOWN}（{len(keep)} + {len(out)} = {len(merged)}）")
    else:
        print("警告：全岛图层中未找到海口要素，未替换")


if __name__ == "__main__":
    main()
