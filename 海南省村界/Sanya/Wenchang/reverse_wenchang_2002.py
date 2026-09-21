# -*- coding: utf-8 -*-
"""
文昌 2002 手绘乡镇界 -> 乡镇级 SHP 反向映射
==========================================
依据 手绘地图映射要点.txt / 三亚经验（reverse_sanya_2002.py）：
  - 正向制图参数：EPSG:32649，1px=20m，四至 = 文昌市当前乡镇外接矩形
  - 图幅 Wenchang2.png = 4252 x 4492（与 Hainan.py 出图 Wenchang3 完全同框）
  - 外轮廓（海岸线/县界）以当前 SHP 为权威；手绘图只提供内部乡镇界线
  - 白色区域洪泛 -> 轮廓 -> 顶点吸附到线网中心 -> 与陆地求交 -> 缝隙填充 -> 属性继承

输出：
  Wenchang/文昌乡镇2002.shp            （EPSG:32649）
  Wenchang/文昌乡镇2002_Albers.shp     （转回源 CRS CGCS2000 Albers，供合并）
"""
import os
import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
SHP_PATH = os.path.join(BASE, "town", "Hainan2002", "Hainan_town.shp")
IMG_2002 = os.path.join(BASE, "town", "Empty_map", "Wenchang2.png")
OUT_BASE = os.path.join(HERE, "文昌乡镇2002")

MIN_REGION_PX = 400          # 最小白色区域面积(px²) ~ 0.16 km² 以下视为噪点
MIN_KEEP_M2 = 0.5e6          # 过滤掉 <0.5 km² 的碎屑区域
SNAP_M = 40.0                # 缝隙吸附到相邻区域的距离(米)
TARGET_CRS = "EPSG:32649"


def imread_gray(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def sanitize(g):
    from shapely.validation import make_valid
    if g is None or g.is_empty:
        return g
    if not g.is_valid:
        g = make_valid(g)
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [x for x in getattr(g, "geoms", []) if x.geom_type in ("Polygon", "MultiPolygon")]
    if parts:
        return unary_union(parts)
    return g.buffer(0)


def main():
    # ---------------- 1. 读取文昌当前 SHP 乡镇 ----------------
    gdf = gpd.read_file(SHP_PATH, encoding="utf-8").to_crs(TARGET_CRS)
    gdf["乡镇码"] = gdf["CODE"].astype(str).str[:9]
    wc = gdf[gdf["CITY"].astype(str).str.contains("文昌")].copy()
    print(f"文昌要素数 {len(wc)}，乡镇码数 {wc['乡镇码'].nunique()}")

    minx, miny, maxx, maxy = wc.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    step_x, step_y = geo_w / 4252, geo_h / 4492
    print(f"extent [{minx:.1f},{miny:.1f},{maxx:.1f},{maxy:.1f}] step={step_x:.3f}")

    outer = unary_union(list(wc.geometry)).buffer(0)
    print(f"文昌陆域 {outer.area / 1e6:.2f} km²")

    town_geoms = list(wc.dissolve(by="乡镇码").geometry)

    def px2geo(i, j):
        return minx + (i + 0.5) * step_x, maxy - (j + 0.5) * step_y

    # ---------------- 2. 栅格化当前 SHP 边界（逐乡镇） ----------------
    W, H = 4252, 4492

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

    shp_lines = np.zeros((H, W), np.uint8)
    for _t in town_geoms:
        shp_lines = np.maximum(shp_lines, rasterize_rings(_t))
    print(f"shp 边界栅格 px={int(shp_lines.sum())}")

    # ---------------- 3. 2002 线网（手绘黑线 + SHP 外框兜底） ----------------
    dark2002 = (imread_gray(IMG_2002) == 0).astype(np.uint8)
    lines2002 = np.maximum(dark2002, shp_lines).astype(np.uint8)
    print(f"w2 黑 px={int(dark2002.sum())} 线网 px={int(lines2002.sum())}")

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    net = cv2.morphologyEx(lines2002, cv2.MORPH_CLOSE, kernel, iterations=2)
    net_thick = cv2.dilate(net, kernel, iterations=1)

    # ---------------- 4. 白色区域洪泛 -> 轮廓 -> 顶点吸附 -> 多边形 ----------------
    white = (net_thick == 0).astype(np.uint8) * 255
    n_reg, reg_label, reg_stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)

    polys_geo = []
    for i in range(1, n_reg):
        area_px = int(reg_stats[i, cv2.CC_STAT_AREA])
        if area_px < MIN_REGION_PX:
            continue
        x0 = int(reg_stats[i, cv2.CC_STAT_LEFT]); y0 = int(reg_stats[i, cv2.CC_STAT_TOP])
        wb = int(reg_stats[i, cv2.CC_STAT_WIDTH]); hb = int(reg_stats[i, cv2.CC_STAT_HEIGHT])
        rmask = (reg_label[y0:y0 + hb, x0:x0 + wb] == i).astype(np.uint8) * 255
        contours, _ = cv2.findContours(rmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        c = max(contours, key=cv2.contourArea)
        if c.shape[0] < 3:
            continue
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
            ring.append(px2geo(*best) if best else px2geo(ix, iy))
        if abs(ring[0][0] - ring[-1][0]) > 1e-9 or abs(ring[0][1] - ring[-1][1]) > 1e-9:
            ring.append(ring[0])
        poly = Polygon(ring).buffer(0)
        if poly.is_empty or poly.area <= 0:
            continue
        polys_geo.append(poly)

    # ---------------- 5. 与文昌陆域求交（外轮廓以 SHP 为权威） ----------------
    land = []
    for p in polys_geo:
        inter = p.intersection(outer).buffer(0)
        if inter.is_empty or inter.area <= 0:
            continue
        frac = inter.area / p.area
        if inter.area > 2e6 and frac < 0.3 and p.area > 20e6:
            continue
        if inter.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        land.append(inter)
    print(f"裁剪后区域 {len(land)} 个，合计 {sum(x.area for x in land)/1e6:.2f} km²")

    # ---------------- 6. 缝隙填充（海岸线细微差异归于相邻区域） ----------------
    kept = sorted([p for p in land if p.area > MIN_KEEP_M2], key=lambda p: -p.area)
    covered = unary_union(kept)
    missing = outer.difference(covered)
    assigned = 0
    if not missing.is_empty:
        frags = [f for f in (list(missing.geoms) if missing.geom_type == "MultiPolygon" else [missing]) if f.area > 0]
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
        print(f"缝隙填充 {len(frags)} 块，并入 {assigned}，剩余 {outer.difference(unary_union(kept)).area/1e6:.2f} km²")

    leftover = outer.difference(unary_union(kept))
    if not leftover.is_empty:
        lf = list(leftover.geoms) if leftover.geom_type == "MultiPolygon" else [leftover]
        kept.extend(p for p in lf if p.area > 0)
        print(f"离岛补回 {len([p for p in lf if p.area>0])} 个")
    final = kept
    print(f"最终区域数 {len(final)}，总面积 {sum(p.area for p in final)/1e6:.2f} km²（陆域 {outer.area/1e6:.2f}）")

    # ---------------- 7. 属性继承（最大面积重叠 -> 乡镇码/乡镇名） ----------------
    towns = wc.dissolve(by="乡镇码")[["CODE", "TOWN", "geometry"]].reset_index()
    towns["乡镇码"] = towns["CODE"].astype(str).str[:9]
    rows = []
    for ridx, p in enumerate(final):
        best, best_ov = None, 0.0
        for _, r in towns.iterrows():
            ov = p.intersection(r.geometry).area
            if ov > best_ov:
                best_ov, best = ov, r
        rows.append({
            "OBJECTID": ridx + 1,
            "乡镇码": str(best["乡镇码"]) if best is not None else "",
            "乡镇名": best["TOWN"] if best is not None else "",
            "面积km2": round(p.area / 1e6, 3),
        })

    res = gpd.GeoDataFrame(rows, geometry=final, crs=TARGET_CRS)
    res.to_file(OUT_BASE + ".shp", encoding="utf-8")
    res.to_file(OUT_BASE + ".geojson", driver="GeoJSON", encoding="utf-8")

    src_crs = gpd.read_file(SHP_PATH, encoding="utf-8", rows=1).crs
    res_albers = res.to_crs(src_crs)
    res_albers["geometry"] = res_albers.geometry.apply(sanitize)
    res_albers.to_file(OUT_BASE + "_Albers.shp", encoding="utf-8")
    print(f"已输出 {OUT_BASE}.shp / .geojson / _Albers.shp")

    # ---------------- 8. 校验：结果栅格化 vs 2002线网 ----------------
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
    print(f"校验：结果边界 {int(rend.sum())} px")
    for tol in (1.0, 1.5, 2.0, 3.0, 5.0):
        print(f"  {tol}px 内占比 {(dpx <= tol).mean():.3f}")
    print(f"  平均 {dpx.mean():.2f}px  中位 {np.median(dpx):.2f}px")
    print("DONE")


if __name__ == "__main__":
    main()