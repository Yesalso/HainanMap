# -*- coding: utf-8 -*-
"""
把手动调整的三亚 2002 乡镇图 (town/Sanya_2002.png) 映射回 SHP 边界。

要点：
  * 海岸线用原 SHP（海南村界.shp）的三亚外边界，不采用手绘图的海岸线；
  * 手绘图只提供"内部乡镇界线"，并去除文字标注；
  * 手动绘画误差会产生细缝/碎块：统一按"最近区域"归属，
    使相邻区域共用一条界线（无缝、无重叠）；
  * 离岸岛屿并入最近的陆地大区域；
  * 区域名称暂用 1、2、… 数字编号。

输出：
  town/三亚2002.shp                     三亚 2002 乡镇（Albers，与源数据一致）
  town/Hainan_town.shp                  用上述三亚边界替换全岛图层中的三亚部分
  town/三亚2002_preview.png             校验预览图
"""

import os
import shutil
import numpy as np
import cv2
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union
from shapely.validation import make_valid

# ==================== 配置 ====================
BASE = r"D:\Windows\Documents\海南省村界\海南省村界"
TOWN_DIR = os.path.join(BASE, "town")

SHP_PATH = os.path.join(BASE, "海南村界.shp")
IMG_2002 = os.path.join(TOWN_DIR, "Sanya_2002.png")

OUT_SANYA = os.path.join(TOWN_DIR, "三亚2002.shp")
HAINAN_TOWN = os.path.join(TOWN_DIR, "Hainan_town.shp")
HAINAN_TOWN_BAK = os.path.join(TOWN_DIR, "Hainan_town_backup.shp")
PREVIEW = os.path.join(TOWN_DIR, "三亚2002_preview.png")

SHP_ENCODING = "gbk"
OUT_ENCODING = "utf-8"
TARGET_CRS = "EPSG:32649"
SANYA_PREFIX = "4602"

MIN_MAIN_PX = 5000      # 小于此面积(px²)的白色区域视为碎块，并入最近区域
CLOSE_ITER = 2          # 形态学闭运算，桥接手绘断线
SNAP_M = 60.0           # 海岸线缝隙吸附距离(米)

# 手动图 16 个区域的参考名（用于日志/参考，不写入 TOWN 字段）
REF_BY_CENTROID = [
    ((1155, 366), "雅亮乡"), ((1727, 593), "高峰乡"), ((1288, 638), "育才乡"),
    ((2786, 709), "藤桥镇"), ((859, 890), "崖城镇"), ((392, 786), "梅山镇"),
    ((612, 908), "保港镇"), ((2370, 1301), "田独镇"), ((2113, 1057), "荔枝沟镇"),
    ((1248, 1056), "天涯镇"), ((1692, 1086), "羊栏镇"), ((2617, 1158), "林旺镇"),
    ((2129, 1318), "红沙镇"), ((1904, 1301), "河西区"), ((1990, 1400), "河东区"),
    ((1870, 1540), "南海街道"),
]
# ==============================================


def imread_unicode(path, flag=cv2.IMREAD_GRAYSCALE):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), flag)


def rasterize_rings(geom, H, W, minx, miny, maxx, maxy):
    """把几何边界栅格化为 1px 线。"""
    m = np.zeros((H, W), np.uint8)
    ring = geom.boundary
    lines = list(ring.geoms) if ring.geom_type == "MultiLineString" else [ring]
    gw, gh = maxx - minx, maxy - miny
    for ln in lines:
        c = np.array(ln.coords)
        if len(c) < 2:
            continue
        px = (c[:, 0] - minx) / gw * W
        py = (maxy - c[:, 1]) / gh * H
        cv2.polylines(m, [np.stack([px, py], 1).astype(np.int32)], False, 255, 1, cv2.LINE_8)
    return (m == 255).astype(np.uint8)


def fill_polygons(geom, H, W, minx, miny, maxx, maxy):
    """把多边形填充为 mask。"""
    m = np.zeros((H, W), np.uint8)
    gw, gh = maxx - minx, maxy - miny
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for p in polys:
        c = np.array(p.exterior.coords)
        if len(c) < 3:
            continue
        px = (c[:, 0] - minx) / gw * W
        py = (maxy - c[:, 1]) / gh * H
        cv2.fillPoly(m, [np.stack([px, py], 1).astype(np.int32)], 1)
    return m.astype(bool)


def sanitize_polygonal(geom):
    """修复无效几何，并保证结果为 Polygon/MultiPolygon。"""
    if geom is None or geom.is_empty:
        return geom
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    if parts:
        return unary_union(parts)
    return geom.buffer(0)


def main():
    # ---------- 1. 读取手绘图，取最大连通域 = 边界线网（去掉文字） ----------
    img = imread_unicode(IMG_2002)
    H, W = img.shape
    dark = (img < 128).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    img_lines = (lab == big).astype(np.uint8)
    print(f"手绘图 {W}x{H}：黑色像素 {int(dark.sum())}，"
          f"最大线网连通域 {int(img_lines.sum())}（已剔除文字 {n - 1 - 1} 个连通域）")

    # ---------- 2. 读取 SHP，取三亚外边界 ----------
    gdf = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING).to_crs(TARGET_CRS)
    gdf = gdf[~gdf["XZQMC"].astype(str).str.startswith("三沙市")].copy()
    gdf["code"] = gdf["XZQDM"].astype(str).str[:9]
    sanya = gdf[gdf["code"].str.startswith(SANYA_PREFIX)].copy()
    if sanya.empty:
        raise ValueError("未找到三亚数据")

    minx, miny, maxx, maxy = sanya.total_bounds
    outer = unary_union(list(sanya.geometry)).buffer(0)
    outer_polys = list(outer.geoms) if outer.geom_type == "MultiPolygon" else [outer]
    main_land = max(outer_polys, key=lambda p: p.area)
    print(f"三亚范围 [{minx:.0f},{miny:.0f},{maxx:.0f},{maxy:.0f}]，"
          f"陆地 {outer.area / 1e6:.1f} km²，主岛 {main_land.area / 1e6:.1f} km²")

    # ---------- 3. 线网 = 手绘内部线 + SHP 海岸线 ----------
    coast = rasterize_rings(outer, H, W, minx, miny, maxx, maxy)
    net = np.maximum(img_lines, coast).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    net = cv2.morphologyEx(net, cv2.MORPH_CLOSE, k, iterations=CLOSE_ITER)
    net_thick = cv2.dilate(net, k, iterations=1)

    # 陆地 mask（SHP 外边界填充），海面不参与分区
    land_mask = fill_polygons(outer, H, W, minx, miny, maxx, maxy)

    # ---------- 4. 白色区域连通域，挑出 16 个陆地大区域作为种子 ----------
    white = (net_thick == 0).astype(np.uint8)
    nreg, rlab, rstats, rcent = cv2.connectedComponentsWithStats(white, 8)
    gw, gh = maxx - minx, maxy - miny

    def px2geo(x, y):
        return (minx + x * gw / W, maxy - y * gh / H)

    main_land_buf = main_land.buffer(1)

    def nearest_ref(cx, cy):
        return min(REF_BY_CENTROID, key=lambda t: (t[0][0] - cx) ** 2 + (t[0][1] - cy) ** 2)

    yy, xx = np.mgrid[0:H, 0:W]
    xx = xx.astype(np.int32)
    yy = yy.astype(np.int32)

    seeds = []   # [ref_name, mask, (cx,cy)]
    for i in range(1, nreg):
        area = int(rstats[i, cv2.CC_STAT_AREA])
        if area < MIN_MAIN_PX:
            continue
        mask = (rlab == i)
        # 与陆地重合率 > 50% 且质心在主岛内 → 陆地大区域（排除离岸岛屿）
        inter = int((mask & land_mask).sum())
        cx, cy = rcent[i]
        if inter < 0.5 * area:
            continue
        if not main_land_buf.contains(Point(px2geo(cx, cy))):
            continue
        # 该连通域内包含哪些参考名（手绘图上可能有两个标签连成一片）
        pts = [(pt, nm) for pt, nm in REF_BY_CENTROID if mask[pt[1], pt[0]]]
        if len(pts) <= 1:
            nm, pt = (pts[0][1], pts[0][0]) if pts else (nearest_ref(cx, cy)[1], (cx, cy))
            seeds.append([nm, mask, pt])
        else:
            dists = np.stack([(xx - px).astype(np.int64) ** 2 +
                              (yy - py).astype(np.int64) ** 2 for (px, py), _ in pts])
            arg = np.argmin(dists, axis=0)
            for k, ((px, py), nm) in enumerate(pts):
                sub = mask & (arg == k)
                if int(sub.sum()) >= MIN_MAIN_PX:
                    seeds.append([nm, sub, (px, py)])
    print(f"识别到陆地大区域 {len(seeds)} 个")
    for j, s in enumerate(seeds, 1):
        print(f"  seed{j:2d} 参考名={s[0]:<5} 质心=({s[2][0]:.0f},{s[2][1]:.0f}) 面积={int(s[1].sum())}px")

    # ---------- 5. 线网/细缝/碎块/岛屿 → 归最近种子（无缝分区） ----------
    seed_lab = np.zeros((H, W), np.int32)
    for idx, s in enumerate(seeds, 1):
        seed_lab[s[1]] = idx

    assign_mask = land_mask & (seed_lab == 0)
    best_d = np.full((H, W), np.inf, np.float32)
    best_id = np.zeros((H, W), np.int32)
    for idx, s in enumerate(seeds, 1):
        dt = cv2.distanceTransform((s[1] == 0).astype(np.uint8), cv2.DIST_L2, 5)
        upd = dt < best_d
        best_d[upd] = dt[upd]
        best_id[upd] = idx
    final_lab = seed_lab.copy()
    final_lab[assign_mask] = best_id[assign_mask]
    print(f"陆地像素 {int(land_mask.sum())}，已分配 {int((final_lab > 0).sum())}，"
          f"未分配 {int((land_mask & (final_lab == 0)).sum())}")

    # ---------- 6. 栅格 → 矢量（+0.5px 保证相邻共用一条线） ----------
    STEP = (gw / W + gh / H) / 2.0

    def mask_to_geo(mask):
        cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        polys = []
        for c in cs:
            c = c[:, 0, :]
            if len(c) < 3:
                continue
            p = Polygon([px2geo(float(x), float(y)) for x, y in c]).buffer(0)
            if p.is_empty or p.area <= 0:
                continue
            p = p.buffer(0.5 * STEP, join_style=2)
            if not p.is_empty and p.area > 0:
                polys.append(p)
        if not polys:
            return None
        return unary_union(polys)

    regions = []
    for idx, s in enumerate(seeds, 1):
        g = mask_to_geo(final_lab == idx)
        if g is None:
            continue
        g = g.intersection(outer).buffer(0)
        if g.is_empty or g.area <= 0:
            continue
        regions.append({"ref": s[0], "centroid": s[2], "geom": g})
    print(f"矢量化区域 {len(regions)} 个")

    # ---------- 7. 海岸线缝隙填充（以 SHP 陆地为权威） ----------
    covered = unary_union([r["geom"] for r in regions])
    missing = outer.difference(covered)
    if not missing.is_empty:
        frags = list(missing.geoms) if missing.geom_type == "MultiPolygon" else [missing]
        for f in sorted(frags, key=lambda p: -p.area):
            if f.area <= 0:
                continue
            fb = f.buffer(SNAP_M, quad_segs=1)
            best, bov = None, 0.0
            for r in regions:
                ov = r["geom"].intersection(fb).area
                if ov > bov:
                    bov, best = ov, r
            if best is not None:
                best["geom"] = unary_union([best["geom"], f])
        print(f"缝隙填充：{len(frags)} 个碎块已并入最近区域")

    # 去掉重叠：按面积从大到小依次 difference，保证分区不重叠
    regions.sort(key=lambda r: -r["geom"].area)
    for a in range(len(regions)):
        for b in range(a + 1, len(regions)):
            if regions[a]["geom"].intersects(regions[b]["geom"]):
                regions[b]["geom"] = regions[b]["geom"].difference(regions[a]["geom"]).buffer(0)
                if regions[b]["geom"].is_empty:
                    break

    # 几何净化
    for r in regions:
        if not r["geom"].is_valid:
            r["geom"] = make_valid(r["geom"]).buffer(0)
        if r["geom"].geom_type not in ("Polygon", "MultiPolygon"):
            r["geom"] = r["geom"].buffer(0)

    # ---------- 8. 排序（按面积从大到小），TOWN 直接使用 2002 乡镇名 ----------
    regions = [r for r in regions
               if r["geom"].geom_type in ("Polygon", "MultiPolygon")
               and not r["geom"].is_empty and r["geom"].area > 0]
    regions.sort(key=lambda r: -r["geom"].area)
    rows = []
    for i, r in enumerate(regions, 1):
        rows.append({
            "TOWN": r["ref"],
            "AREA_KM2": round(r["geom"].area / 1e6, 3),
            "geometry": r["geom"],
        })
    print("\n面积 → 乡镇名：")
    for r in rows:
        print(f"  {r['TOWN']:<5} {r['AREA_KM2']:>8.2f} km²")

    # 转回源数据 CRS（Albers）输出
    src_prj = gpd.read_file(SHP_PATH, encoding=SHP_ENCODING, rows=1).crs
    sanya_out = gpd.GeoDataFrame(rows, geometry="geometry", crs=TARGET_CRS).to_crs(src_prj)
    # 重投影/差值后可能产生自交，做一次净化，只保留面
    sanya_out["geometry"] = sanya_out.geometry.apply(sanitize_polygonal)
    sanya_out.insert(0, "CODE", [f"SY{i:02d}" for i in range(1, len(sanya_out) + 1)])
    sanya_out.insert(3, "CITY", "三亚市")
    sanya_out.insert(4, "EN", "Sanya")
    sanya_out.insert(5, "N_FEAT", 0)
    sanya_out = sanya_out[["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]]

    if os.path.exists(OUT_SANYA):
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            p = OUT_SANYA[:-4] + ext
            if os.path.exists(p):
                os.remove(p)
    sanya_out.to_file(OUT_SANYA, encoding=OUT_ENCODING)
    print(f"\n已输出三亚：{OUT_SANYA}")

    # ---------- 9. 替换全岛图层中的三亚部分 ----------
    hainan = gpd.read_file(HAINAN_TOWN, encoding=OUT_ENCODING)
    is_sanya = (hainan["CODE"].astype(str).str.startswith(SANYA_PREFIX)
                | (hainan["CITY"].astype(str) == "三亚市"))
    if is_sanya.any():
        if not os.path.exists(HAINAN_TOWN_BAK):
            shutil.copyfile(HAINAN_TOWN, HAINAN_TOWN_BAK)
            print(f"已备份原全岛图层：{HAINAN_TOWN_BAK}")
        keep = hainan[~is_sanya].copy()
        keep = keep.drop(columns=[c for c in ("REF",) if c in keep.columns])
        sanya_part = sanya_out.to_crs(hainan.crs).copy()
        merged = gpd.GeoDataFrame(
            pd.concat([keep, sanya_part], ignore_index=True), crs=hainan.crs)
        merged["geometry"] = merged.geometry.apply(sanitize_polygonal)
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            p = HAINAN_TOWN[:-4] + ext
            if os.path.exists(p):
                os.remove(p)
        merged.to_file(HAINAN_TOWN, encoding=OUT_ENCODING)
        print(f"已替换全岛图层三亚部分：{HAINAN_TOWN}（{len(keep)} + {len(sanya_part)} = {len(merged)}）")
    else:
        print("警告：全岛图层中未找到三亚要素，未替换")

    # ---------- 10. 预览图 ----------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        names = {f.name for f in font_manager.fontManager.ttflist}
        cjk = next((n for n in ["Microsoft YaHei", "SimHei", "SimSun", "PMingLiU"]
                    if n in names), None)
        if cjk:
            plt.rcParams["font.sans-serif"] = [cjk]
            plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(14, 8), dpi=100)
        sanya_out.to_crs(TARGET_CRS).plot(ax=ax, column="TOWN", cmap="tab20",
                                          edgecolor="black", linewidth=0.4)
        for _, r in sanya_out.to_crs(TARGET_CRS).iterrows():
            p = r.geometry.representative_point()
            ax.text(p.x, p.y, r["TOWN"], ha="center", va="center", fontsize=10)
        ax.set_aspect("equal")
        ax.axis("off")
        plt.savefig(PREVIEW, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"已输出预览：{PREVIEW}")
    except Exception as e:
        print(f"预览生成失败：{e}")


if __name__ == "__main__":
    main()
