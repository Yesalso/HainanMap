# -*- coding: utf-8 -*-
"""
Sanya2002.png -> 乡镇级 SHP 反向映射（v3，洪泛区域法）
======================================================
依据 输出各县市地图.txt 的制图参数（EPSG:32649, 1px≈30m, 四至范围）把手动修改后的
Sanya2002.png 内部乡镇界线反向映射回地理坐标，外部海岸线/市界保持原 SHP 版。

流程：
  1. 文字掩膜 = 2007 图像黑像素 − 原 SHP 边界栅格（文字在两年图中完全相同）
  2. 2002 线网 = 2002 黑像素 − 文字掩膜          （得到纯边界线，无文字干扰）
  3. 白色区域洪泛（4连通）= 各乡镇内部
  4. 白色区域轮廓 -> 像素环 -> 地理多边形
  5. 与三亚市外部陆地求交 + 消除重叠/缝隙
"""
import os
import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
SHANA_DIR = os.path.dirname(HERE)
SHP_PATH = os.path.join(SHANA_DIR, "海南村界.shp")
IMG_2002 = os.path.join(HERE, "Sanya2002.png")
IMG_2007 = os.path.join(HERE, "Sanya2007.png")
OUT_BASE = os.path.join(HERE, "三亚乡镇2002_123")
MIN_REGION_PX = 400          # 最小白色区域面积(px²) ~ 0.36 km² 以下视为噪点
MIN_KEEP_M2 = 0.5e6          # 过滤掉 <0.5 km² 的碎屑区域
SNAP_M = 40.0                # 缝隙吸附到相邻区域的距离

# 2002 年三亚乡镇名（按乡镇码，SHP 中无乡镇级名称行，经核对的 2002 名称）
TOWN_NAMES = {
    "460200001": "海棠湾镇",
    "460200002": "田独镇",
    "460200003": "凤凰镇",
    "460200004": "崖城镇",
    "460200005": "天涯镇",
    "460200006": "育才乡",
    "460200101": "河东区管委会",
    "460200102": "河西区管委会",
    "460200103": "海岛",
}
W, H = 3023, 1724
TARGET_CRS = "EPSG:32649"

gdf = gpd.read_file(SHP_PATH, encoding="gbk").to_crs(TARGET_CRS)
gdf = gdf[~gdf["XZQMC"].astype(str).str.startswith("三沙市")].copy()
gdf["乡镇码"] = gdf["XZQDM"].astype(str).str[:9]
sanya = gdf[gdf["乡镇码"].astype(str).str.startswith("4602")].copy()
print(f"Sanya villages in SHP: {len(sanya)}")

minx, miny, maxx, maxy = sanya.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny
step_x, step_y = geo_w / W, geo_h / H


def px2geo(i, j):
    return minx + (i + 0.5) * step_x, maxy - (j + 0.5) * step_y


outer = unary_union(list(sanya.geometry)).buffer(0)
outer_ring = outer.boundary
print(f"bounds: [{minx:.3f}, {miny:.3f}, {maxx:.3f}, {maxy:.3f}] | outer land={outer.area/1e6:.1f} km2")


def load_gray(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


dark2007 = (load_gray(IMG_2007) == 0).astype(np.uint8)
dark2002 = (load_gray(IMG_2002) == 0).astype(np.uint8)


def rasterize_rings(geom):
    mask = np.zeros((H, W), np.uint8)
    ring = geom.boundary
    lines = list(ring.geoms) if ring.geom_type == "MultiLineString" else [ring]
    for ln in lines:
        coords = np.array(ln.coords)
        if len(coords) < 2:
            continue
        pxp = (coords[:, 0] - minx) / geo_w * W
        pyp = (maxy - coords[:, 1]) / geo_h * H
        pts = np.stack([pxp, pyp], 1).astype(np.int32)
        cv2.polylines(mask, [pts], False, 255, 1, cv2.LINE_8)
    return (mask == 255).astype(np.uint8)


# 原 SHP 乡镇边界（外轮廓+内部乡镇界）栅格化，作为 2007 线的几何真值
# 注意：必须逐乡镇栅格化，union().boundary 会抵消掉内部共享边界
town_geoms = list(sanya.dissolve(by="乡镇码").geometry)
shp_lines = np.zeros((H, W), np.uint8)
for _t in town_geoms:
    shp_lines = np.maximum(shp_lines, rasterize_rings(_t)).astype(np.uint8)
# 文字掩膜 = 2007 黑像素中不属于 SHP 线的部分（文字两图相同，直接用于 2002 剔除）
text_mask = (dark2007 == 1) & (shp_lines == 0)
text_mask = text_mask.astype(np.uint8)
# 去掉文字小连通域中的误判（表格线等不会出现，直接用）
# 2002 线网 = 2002 黑像素 − 文字
lines2002 = ((dark2002 == 1) & (text_mask == 0)).astype(np.uint8)
print(f"dark2007={np.count_nonzero(dark2007)}, dark2002={np.count_nonzero(dark2002)}, "
      f"text={np.count_nonzero(text_mask)}, lines2002={np.count_nonzero(lines2002)}")

# 保证外框存在（若用户在图上擦到了海岸线，用 SHP 外框补齐）
lines2002 = np.maximum(lines2002, shp_lines).astype(np.uint8)

# 形态学闭合桥接 1-2px 断点（绘画线难免有小缺口）
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
net = cv2.morphologyEx(lines2002, cv2.MORPH_CLOSE, kernel, iterations=2)
print(f"net px = {np.count_nonzero(net)}")

# ---------------- 白色区域洪泛 ----------------
# 把线网膨胀 1px，确保对角线也连成实墙；再以 8 连通对白色区域做连通域，
# 避免 1px 对角缝隙导致的区域合并
net_thick = cv2.dilate(net, kernel, iterations=1)
white = (net_thick == 0).astype(np.uint8) * 255
n_reg, reg_label, reg_stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
print(f"white regions: {n_reg - 1}")

polys_geo = []
for i in range(1, n_reg):
    area_px = int(reg_stats[i, cv2.CC_STAT_AREA])
    if area_px < MIN_REGION_PX:
        continue
    x0 = int(reg_stats[i, cv2.CC_STAT_LEFT]); y0 = int(reg_stats[i, cv2.CC_STAT_TOP])
    wb = int(reg_stats[i, cv2.CC_STAT_WIDTH]); hb = int(reg_stats[i, cv2.CC_STAT_HEIGHT])
    rmask = (reg_label[y0:y0 + hb, x0:x0 + wb] == i).astype(np.uint8) * 255
    contours, hier = cv2.findContours(rmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        continue
    c = max(contours, key=cv2.contourArea)
    if c.shape[0] < 3:
        continue
    # 把轮廓顶点吸附到线网中心像素（半径2内最近），消除半像素偏移
    ring = []
    for pt in c[:, 0, :]:
        ix, iy = int(pt[0]) + x0, int(pt[1]) + y0
        best, bd = None, 9
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                yy, xx = iy + dy, ix + dx
                if 0 <= yy < H and 0 <= xx < W and net[yy, xx]:
                    d = dx * dx + dy * dy
                    if d < bd:
                        bd, best = d, (xx, yy)
        if best is None:
            best = (ix, iy)
        ring.append(px2geo(*best))
    if len(ring) < 3:
        continue
    if abs(ring[0][0] - ring[-1][0]) > 1e-9 or abs(ring[0][1] - ring[-1][1]) > 1e-9:
        ring.append(ring[0])
    poly = Polygon(ring).buffer(0)
    if poly.is_empty or poly.area <= 0:
        continue
    polys_geo.append(poly)
print(f"raw region polygons: {len(polys_geo)}")

# ---------------- 与外部陆地求交 ----------------
land = []
for p in polys_geo:
    inter = p.intersection(outer).buffer(0)
    if inter.is_empty or inter.area <= 0:
        continue
    frac = inter.area / p.area
    # 只保留主体在陆地内的区域（离岛等在海水里的大区域排除）
    if inter.area > 2e6 and frac < 0.3 and p.area > 20e6:
        continue
    if inter.geom_type not in ("Polygon", "MultiPolygon"):
        continue
    land.append(inter)
print(f"after clip: {len(land)}  sum={sum(p.area for p in land)/1e6:.1f} km2")

# ---------------- 填充外边界缝隙（以 SHP 陆地为权威） ----------------
# 图像海岸线与 SHP 海岸线的细微差异会沿外沿产生小缝隙；把它们分配给相邻区域。
kept = [p for p in land if p.area > MIN_KEEP_M2]
kept = sorted(kept, key=lambda p: -p.area)
covered = unary_union(kept)
missing = outer.difference(covered)
if not missing.is_empty:
    frags = list(missing.geoms) if missing.geom_type == "MultiPolygon" else [missing]
    frags = [f for f in frags if f.area > 0]
    assigned = 0
    for f in sorted(frags, key=lambda p: -p.area):
        best, bestov = None, 0.0
        fb = f.buffer(SNAP_M, quad_segs=1)
        for k, p in enumerate(kept):
            ov = p.intersection(fb).area
            if ov > bestov:
                bestov, best = ov, k
        if best is not None:
            kept[best] = kept[best].union(f)
            assigned += 1
    print(f"gap fill: {len(frags)} fragments, assigned {assigned}, "
          f"remaining {round(outer.difference(unary_union(kept)).area/1e6, 2)} km2")
else:
    print("gap fill: no gaps")

# 剩余缝隙全部为 SHP 中的真实小海岛（离岸无人岛），补回输出
leftover = outer.difference(unary_union(kept))
if not leftover.is_empty:
    lf = list(leftover.geoms) if leftover.geom_type == "MultiPolygon" else [leftover]
    kept.extend(p for p in lf if p.area > 0)
    print(f"islands appended: {sum(1 for p in lf if p.area>0)}")
final = kept
print(f"final township polygons: {len(final)}")
print(f"total area: {sum(p.area for p in final)/1e6:.1f} km2 "
      f"(outer land {outer.area/1e6:.1f} km2)")

# ---------------- 属性继承 ----------------
towns = sanya.dissolve(by="乡镇码")[["XZQMC", "geometry"]].reset_index()
rows = []
for ridx, p in enumerate(final):
    best_code, best_ov = None, 0.0
    for _, r in towns.iterrows():
        ov = p.intersection(r.geometry).area
        if ov > best_ov:
            best_ov, best_code = ov, r["乡镇码"]
    name = TOWN_NAMES.get(str(best_code), '' if best_code is None else best_code)
    rows.append((ridx + 1, '' if best_code is None else best_code, name,
                 round(p.area / 1e6, 3)))

res = gpd.GeoDataFrame({"OBJECTID": [r[0] for r in rows],
                        "乡镇码": [r[1] for r in rows],
                        "乡镇名": [r[2] for r in rows],
                        "面积km2": [r[3] for r in rows]},
                       geometry=final, crs=TARGET_CRS)

res.to_file(OUT_BASE + ".shp", encoding="utf-8")
res.to_file(OUT_BASE + ".geojson", driver="GeoJSON", encoding="utf-8")
prj = open(os.path.join(SHANA_DIR, "海南村界.prj"), encoding="utf-8").read()
res_albers = res.to_crs(prj)
res_albers.to_file(OUT_BASE + "_Albers.shp", encoding="utf-8")
print(f"WRITTEN: {OUT_BASE}.shp / .geojson / _Albers.shp")

# ---------------- 校验：结果栅格化 vs 2002线网 ----------------
def render_polys(polys):
    m = np.zeros((H, W), np.uint8)
    for p in polys:
        gs = list(p.geoms) if p.geom_type == "MultiPolygon" else [p]
        for pg in gs:
            coords = np.array(pg.exterior.coords)
            pxp = (coords[:, 0] - minx) / geo_w * W
            pyp = (maxy - coords[:, 1]) / geo_h * H
            pts = np.stack([pxp, pyp], 1).astype(np.int32)
            cv2.polylines(m, [pts], True, 255, 1, cv2.LINE_8)
    return (m == 255)


rend = render_polys(final)
net_bool = (net > 0).astype(np.uint8)
dt = cv2.distanceTransform(255 - net_bool * 255, cv2.DIST_L2, 3)
dpx = dt[rend]
print(f"validation: rendered {np.count_nonzero(rend)} px")
for tol in (1.0, 1.5, 2.0, 3.0):
    print(f"  within {tol}px of net: {(dpx <= tol).mean():.3f}")
print(f"  mean {dpx.mean():.2f}px  median {np.median(dpx):.2f}px")
print("DONE")