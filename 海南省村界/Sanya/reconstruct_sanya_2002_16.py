# -*- coding: utf-8 -*-
"""
从 Sanya2002.png 重建 2002 年三亚市 16 个乡镇级单元。

2002 年三亚下辖 16 个乡镇/街道：
  梅山镇 保港镇 崖城镇 雅亮乡 育才乡 高峰乡 天涯镇 羊栏镇
  荔枝沟镇 藤桥镇 林旺镇 田独镇 红沙镇 河东区 河西区 南海街道

思路：
  1. 对 2002 线网做 4-连通洪泛，得到白色区域。
  2. 按面积/位置识别 16 个大陆区域、离岸海岛、以及"很近的线条"夹出的细缝。
  3. 细缝不单独成区（吸附到线中心后自然消失）。
  4. 河东区 / 南海街道 在图中连成一片，按颈部切成两块。
  5. 区域轮廓顶点吸附到最近线网像素（线中心），得到干净边界。
  6. 海岛并入最近乡镇（多部件 MultiPolygon）。
  7. 输出 SHP/GeoJSON，并绘制标注地图。
"""
import os
import re
import numpy as np
import cv2
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.ops import unary_union

# ===================== 配置 =====================
HERE = r"D:\Windows\Documents\海南省村界\海南省村界\Sanya"
SRC_PNG = os.path.join(HERE, "Sanya2002.png")
OUT_BASE = os.path.join(HERE, "三亚乡镇2002_16")
DRAW_PNG = os.path.join(HERE, "三亚乡镇2002_16地图.png")

# 图纸地理范围（与 2007 图一致），用于绘制
MINX, MINY, MAXX, MAXY = 283460.445, 2008356.577, 374161.780, 2060084.642
TARGET_CRS = "EPSG:32649"

MIN_REGION_PX = 200      # 小于此面积的区域忽略
MAXGAP_PX = 10           # 线网/细缝像素分配给最近乡镇的最大距离(px)

# 按面积降序排列后的区域编号 k -> 乡镇名（k 由脚本内自动匹配质心确定）
NAME_BY_CENTROID = [
    ((1155, 366), "雅亮乡"),
    ((1727, 593), "高峰乡"),
    ((1288, 638), "育才乡"),
    ((2786, 709), "藤桥镇"),
    ((859, 890), "崖城镇"),
    ((392, 786), "梅山镇"),
    ((612, 908), "保港镇"),
    ((2370, 1301), "田独镇"),
    ((2113, 1057), "荔枝沟镇"),
    ((1248, 1056), "天涯镇"),
    ((1692, 1086), "羊栏镇"),
    ((2617, 1158), "林旺镇"),
    ((2129, 1318), "红沙镇"),
    ((1904, 1301), "河西区"),
]
# 河东区/南海街道 由同一个大区域切分（该区域质心约 (1951,1456)）
SPLIT_CENTROID = (1951, 1456)
SPLIT_SEED_HE = (1990, 1400)     # 河东区种子
SPLIT_SEED_NH = (1870, 1540)     # 南海街道种子

ISLAND_CODES = "460200103"       # 2007 中代表离岛群的乡镇码

# =============================================


def imread_unicode(path, flags=cv2.IMREAD_GRAYSCALE):
    return cv2.imdecode(np.fromfile(path, np.uint8), flags)


def imwrite_unicode(path, img):
    ext = os.path.splitext(path)[1]
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(path)
    return ok


# ---------- 1. 读取与线网 ----------
img = imread_unicode(SRC_PNG)
H, W = img.shape
lines = (img < 128).astype(np.uint8)
lines_d = cv2.dilate(lines, np.ones((3, 3), np.uint8), 1)   # 封闭对角缝隙

# ---------- 2. 洪泛分区域 ----------
free = (lines_d == 0).astype(np.uint8)
nlab, lab, stats, cent = cv2.connectedComponentsWithStats(free, 4)
sea_ids = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])

regions = []   # (k, label_id, area, cx, cy)
k = 0
for i in range(1, nlab):
    if i in sea_ids:
        continue
    a = int(stats[i, cv2.CC_STAT_AREA])
    if a < MIN_REGION_PX:
        continue
    k += 1
    regions.append((k, i, a, float(cent[i][0]), float(cent[i][1])))

print(f"regions >= {MIN_REGION_PX}px: {k}")

# ---------- 3. 2007 乡镇码，用于判定离岛 ----------
gdf = gpd.read_file(os.path.join(HERE, "..", "海南村界.shp"), encoding="gbk").to_crs(TARGET_CRS)
gdf["乡镇码"] = gdf["XZQDM"].astype(str).str[:9]
sa = gdf[gdf["乡镇码"].str.startswith("4602")].copy().reset_index(drop=True)
towns2007 = sa.dissolve(by="乡镇码").reset_index()


def px2geo(px, py):
    sx = (MAXX - MINX) / W
    sy = (MAXY - MINY) / H
    return (MINX + px * sx, MAXY - py * sy)


def mask_poly(mask):
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
    c = max(cs, key=cv2.contourArea)[:, 0, :]
    if len(c) < 3:
        return None
    p = Polygon([px2geo(float(x), float(y)) for x, y in c]).buffer(0)
    return p if (not p.is_empty and p.area > 0) else None


# 判定每个区域主导的 2007 乡镇码
def dominant_code(poly):
    best, bov = None, 0.0
    for _, r in towns2007.iterrows():
        a = poly.intersection(r.geometry).area
        if a > bov:
            bov, best = a, r["乡镇码"]
    return best


# ---------- 4. 分类：乡镇 / 离岛 / 细缝 ----------
township_regions = {}      # label_id -> name
split_label = None         # 河东+南海 合并区域的 label_id
island_masks = []          # 离岛 mask
sliver_masks = []          # 细缝 mask（忽略）

for kk, i, a, cx, cy in regions:
    m = (lab == i)
    poly = mask_poly(m)
    if poly is None:
        continue
    code = dominant_code(poly)
    if code == ISLAND_CODES:
        island_masks.append(m)
        continue
    # 细缝：面积很小且最大内切半径很小
    dist = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5)
    maxr = float(dist[m].max())
    if a < 5000 and maxr < 12:
        sliver_masks.append(m)
        continue
    # 大区域：匹配名称
    matched = None
    for (mx, my), nm in NAME_BY_CENTROID:
        if abs(cx - mx) < 60 and abs(cy - my) < 60:
            matched = nm
            break
    if matched:
        township_regions[i] = matched
    else:
        # 剩余大区域 = 河东区+南海街道
        split_label = i

print(f"townships: {len(township_regions)}  split_region: {split_label}  "
      f"islands: {len(island_masks)}  slivers: {len(sliver_masks)}")

# ---------- 5. 像素级分区：线网/细缝像素归属最近区域 ----------
# 关键：相邻乡镇之间原来各自把轮廓吸附到最近的线像素，导致两条轮廓之间
# 残留整条线宽的空隙。这里改为把线网像素按最近距离分配给某个乡镇，
# 使所有陆地像素恰好归属唯一乡镇（无缝无重叠）。
STEP = ((MAXX - MINX) / W + (MAXY - MINY) / H) / 2.0   # 像素边长(米)


def mask_to_geo(mask):
    """mask -> 地理多边形；轮廓穿过像素中心，向外扩半像素到真实像素边界。"""
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
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


# ---------- 6. 构建 16 个乡镇几何 ----------
# 种子：各乡镇区域 + 河东/南海切分 + 离岛
seed_masks = []            # (name, mask)
for i, nm in township_regions.items():
    seed_masks.append((nm, lab == i))

if split_label is not None:
    m = (lab == split_label)
    yy, xx = np.mgrid[0:H, 0:W]
    d_he = (xx - SPLIT_SEED_HE[0]) ** 2 + (yy - SPLIT_SEED_HE[1]) ** 2
    d_nh = (xx - SPLIT_SEED_NH[0]) ** 2 + (yy - SPLIT_SEED_NH[1]) ** 2
    seed_masks.append(("河东区", m & (d_he <= d_nh)))
    seed_masks.append(("南海街道", m & (d_he > d_nh)))

island_seeds = [("__island_%d" % j, im) for j, im in enumerate(island_masks)]
all_seeds = seed_masks + island_seeds

seed_lab = np.zeros((H, W), np.int32)
for idx, (nm, m) in enumerate(all_seeds, 1):
    seed_lab[m] = idx

# 待分配像素 = 线网像素 + 非种子的白色区域（细缝/孔洞），排除海域
sea_ids.discard(0)                       # 0 是线网像素，不属于海域
sea_mask = np.isin(lab, list(sea_ids))
non_seed_free = (lines_d == 0) & (seed_lab == 0) & (~sea_mask)
assign_mask = ((lines_d == 1) | non_seed_free) & (~sea_mask)

best_d = np.full((H, W), np.inf, np.float32)
best_id = np.zeros((H, W), np.int32)
for idx, (nm, m) in enumerate(all_seeds, 1):
    dt = cv2.distanceTransform((m == 0).astype(np.uint8), cv2.DIST_L2, 5)
    upd = dt < best_d
    best_d[upd] = dt[upd]
    best_id[upd] = idx

final_lab = seed_lab.copy()
grow = assign_mask & (best_d <= MAXGAP_PX)
final_lab[grow] = best_id[grow]

# 被陆地包围的剩余未分配像素（线网加粗处的孔洞）也归入最近乡镇，避免内部空隙
zero = (final_lab == 0).astype(np.uint8)
nz, zc, _, _ = cv2.connectedComponentsWithStats(zero, 4)
border_z = set(zc[0, :]) | set(zc[-1, :]) | set(zc[:, 0]) | set(zc[:, -1])
interior = np.isin(zc, [z for z in range(1, nz) if z not in border_z]) & (final_lab == 0)
final_lab[interior] = best_id[interior]

result = {}   # name -> geometry
for idx, (nm, m) in enumerate(seed_masks, 1):
    g = mask_to_geo(final_lab == idx)
    if g is not None:
        result[nm] = g

# ---------- 7. 离岛并入最近乡镇 ----------
for j, im in enumerate(island_masks):
    idx = len(seed_masks) + 1 + j
    g = mask_to_geo(final_lab == idx)
    if g is None:
        continue
    c = g.representative_point()
    best, bd = None, 1e18
    for nm, tg in result.items():
        d = tg.distance(c)
        if d < bd:
            bd, best = d, nm
    result[best] = unary_union([result[best], g])

# ---------- 8. 输出 ----------
order = ["梅山镇", "保港镇", "崖城镇", "雅亮乡", "育才乡", "高峰乡", "天涯镇",
         "羊栏镇", "荔枝沟镇", "藤桥镇", "林旺镇", "田独镇", "红沙镇",
         "河东区", "河西区", "南海街道"]

rows = []
for idx, nm in enumerate(order, 1):
    g = result.get(nm)
    if g is None:
        print("  missing:", nm)
        continue
    rows.append((idx, nm, g.area / 1e6))

out = gpd.GeoDataFrame(
    {"OBJECTID": [r[0] for r in rows],
     "乡镇名": [r[1] for r in rows],
     "面积km2": [round(r[2], 3) for r in rows]},
    geometry=[result[r[1]] for r in rows], crs=TARGET_CRS)

out.to_file(OUT_BASE + ".shp", encoding="utf-8")
out.to_file(OUT_BASE + ".geojson", driver="GeoJSON", encoding="utf-8")
prj = open(os.path.join(HERE, "..", "海南村界.prj"), encoding="utf-8").read()
out.to_crs(prj).to_file(OUT_BASE + "_Albers.shp", encoding="utf-8")
print(f"WRITTEN: {OUT_BASE}.shp / .geojson / _Albers.shp")
for _, r in out.iterrows():
    print(f"  {r['OBJECTID']:2d}  {r['乡镇名']:<6s} {r['面积km2']:8.2f} km2")

# ---------- 9. 绘制标注地图 ----------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

try:
    from opencc import OpenCC
    _s2t = OpenCC("s2t")
except Exception:
    _s2t = None


TRAD = {
    "梅山镇": "梅山鎮", "保港镇": "保港鎮", "崖城镇": "崖城鎮", "雅亮乡": "雅亮鄉",
    "育才乡": "育才鄉", "高峰乡": "高峰鄉", "天涯镇": "天涯鎮", "羊栏镇": "羊欄鎮",
    "荔枝沟镇": "荔枝溝鎮", "藤桥镇": "藤橋鎮", "林旺镇": "林旺鎮", "田独镇": "田獨鎮",
    "红沙镇": "紅沙鎮", "河东区": "河東區", "河西区": "河西區", "南海街道": "南海街道",
}


def to_trad(s):
    if _s2t:
        return _s2t.convert(s)
    return TRAD.get(s, s)


names = {f.name for f in font_manager.fontManager.ttflist}
font_name = None
for cand in ["PMingLiU", "MingLiU", "新細明體", "Microsoft JhengHei", "SimHei", "SimSun"]:
    if cand in names:
        font_name = cand
        break
print("font:", font_name)

PX = 30
w_px = int(round((MAXX - MINX) / PX))
h_px = int(round((MAXY - MINY) / PX))
fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=100)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(MINX, MAXX)
ax.set_ylim(MINY, MAXY)
ax.set_aspect("equal")
ax.axis("off")
out.plot(ax=ax, edgecolor="black", facecolor="white", linewidth=72.0 / 100, antialiased=False)

for _, r in out.iterrows():
    g = r.geometry
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    p = g.representative_point()
    ax.text(p.x, p.y, to_trad(r["乡镇名"]), fontsize=28, ha="center", va="center",
            fontfamily=font_name, color="black", zorder=3)

plt.savefig(DRAW_PNG, dpi=100, pad_inches=0, facecolor="white")
plt.close(fig)
print("DRAWN:", DRAW_PNG)
print("DONE")
