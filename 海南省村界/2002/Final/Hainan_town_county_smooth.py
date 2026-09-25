# -*- coding: utf-8 -*-
"""
海南本岛 · 乡镇边界 + 县市边界地图（无地名）——平滑发丝线版
目标效果（参考图）：全线统一约 1px 发丝级细线、抗锯齿柔和灰边、
曲线平滑无锯齿、小岛同样描边、无粗县市界。

与 Hainan_town_county.py / _fixed.py 的差异：
  1. 去掉 simplify(5m) + set_precision(10m)：topo_v3 共享边本已逐点一致，
     这两步反而会重新引入 <=5m 微错位，让线条发毛。
  2. 县市界与乡镇界统一 1px（参考图为单一权重）。
  3. 不再过滤 <2km2 小岛，所有县市面片都描边，海岸线权重连续。
  4. 4x 超采样 + LANCZOS 降采样 + 二值化：输出纯黑/纯白两色，
     无任何灰色过渡像素（用户要求界线纯黑）。
     注意：老版本断线问题出在"降采样+>127 阈值"组合上；
     本版阈值放宽到 160 且线宽 1px 设计，实测连通不断线。
输出：Hainan_town_county_smooth.png（不覆盖其他版本）
"""
import os
import warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from io import BytesIO
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

# ===================== 配置 =====================
shp_path = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/Hainan_town_topo_v3.shp"
out_dir = "D:/Windows/Documents/海南省村界/海南省村界/2002"

TARGET_DPI = 100
PIXEL_TO_METER = 35
SUPERSAMPLE = 4          # 4x 超采样 + LANCZOS 降采样：0.72pt 在 400dpi 画布为 4px，
                         # 降回 1px 时灰度过渡最细腻（2x 仍有可见台阶/锯齿）
TOWN_LINE_PX = 1         # 乡镇线：1px
COUNTY_LINE_PX = 1       # 县市线：同样 1px（参考图单一权重）
LINE_COLOR = "black"
RENDER_DPI = TARGET_DPI * SUPERSAMPLE
# 线宽换算（正确公式）：pt = px @ TARGET_DPI × 72 / TARGET_DPI
TOWN_LINE_PT = TOWN_LINE_PX * 72.0 / TARGET_DPI
COUNTY_LINE_PT = COUNTY_LINE_PX * 72.0 / TARGET_DPI
MAX_PX = 20000
TARGET_CRS = "EPSG:32649"
# ==================================================

warnings.filterwarnings("ignore")
os.makedirs(out_dir, exist_ok=True)

# ---------- 海口市保留名单 ----------
HAIKOU_KEEP = {
    "中山街道", "滨海街道", "金贸街道", "大同街道", "海垦街道", "国兴街道",
    "海府街道", "博爱街道", "白龙街道", "蓝天街道", "和平南街道", "白沙街道",
    "人民路街道", "海甸街道", "新埠街道", "海秀街道", "秀英街道", "金宇街道",
    "西秀镇", "长流镇", "海秀镇", "城西镇", "新海乡",
}

# ---------- 读取 SHP（不做 simplify / set_precision，保持 topo_v3 原样）----------
gdf = gpd.read_file(shp_path, encoding="utf-8")
print(f"原始要素数：{len(gdf)}")
gdf = gdf.to_crs(TARGET_CRS)
gdf["geometry"] = gdf.geometry.buffer(0)
print(f"几何清理完成，有效 {int(gdf.geometry.is_valid.sum())}/{len(gdf)}")

# ---------- 海口市重划 · 县市归属 ----------
haikou = gdf[gdf["CITY"] == "海口市"].copy()
haikou["县市"] = haikou["TOWN"].apply(
    lambda t: "海口市" if t in HAIKOU_KEEP else "琼山市")
others = gdf[gdf["CITY"] != "海口市"].copy()
others["县市"] = others["CITY"]

county_df = gpd.GeoDataFrame(
    pd.concat([haikou[["县市", "geometry"]], others[["县市", "geometry"]]], ignore_index=True),
    crs=TARGET_CRS)
kept = (haikou["县市"] == "海口市").sum()
moved = (haikou["县市"] == "琼山市").sum()
print(f"海口市保留 {kept} 个乡镇，划入琼山市 {moved} 个乡镇")

township = county_df.copy()
counties = county_df.dissolve(by="县市").reset_index()
print(f"县市单元数：{len(counties)}")

# ---------- 绘图 ----------
minx, miny, maxx, maxy = township.total_bounds
geo_w, geo_h = maxx - minx, maxy - miny

w_px = int(round(geo_w / PIXEL_TO_METER))
h_px = int(round(geo_h / PIXEL_TO_METER))
if w_px > MAX_PX or h_px > MAX_PX:
    scale = min(MAX_PX / w_px, MAX_PX / h_px)
    w_px, h_px = int(round(w_px * scale)), int(round(h_px * scale))
print(f"范围 {geo_w:.0f}×{geo_h:.0f} m，像素 {w_px}×{h_px}")

fig = plt.figure(figsize=(w_px / TARGET_DPI, h_px / TARGET_DPI), dpi=RENDER_DPI)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(minx, maxx)
ax.set_ylim(miny, maxy)
ax.set_facecolor("white")
ax.set_aspect("equal")
ax.axis("off")

# 乡镇边界（1px）：合并共享边只描一次，开启抗锯齿
boundary_line = unary_union(township.geometry.boundary.tolist())
gpd.GeoSeries([boundary_line], crs=TARGET_CRS).plot(
    ax=ax, color=LINE_COLOR, linewidth=TOWN_LINE_PT, antialiased=True)

# 县市边界（同 1px）：叠加其上，不过滤小岛，海岸线权重连续
gpd.GeoSeries(counties.geometry, crs=counties.crs).boundary.plot(
    ax=ax, color=LINE_COLOR, linewidth=COUNTY_LINE_PT,
    antialiased=True, zorder=2)

# ---------- 4x 超采样渲染 → LANCZOS 降采样 → 二值化（纯黑/纯白，无灰色）----------
buf = BytesIO()
plt.savefig(buf, format="png", dpi=RENDER_DPI, pad_inches=0,
            bbox_inches=None, facecolor="white")
plt.close(fig)
buf.seek(0)

hi = Image.open(buf)
out_size = (w_px, h_px)
lo = hi.resize(out_size, Image.LANCZOS).convert("L")
# 二值化：灰度 < BIN_THRESHOLD 判为线条（纯黑 0），其余纯白 255。
# 阈值放宽到 160（而非 127）：线条像素只要累计约 37% 覆盖就保留，
# 保证 1px 发丝线在斜线/急弯处连通不断线，同时不含任何灰色像素。
BIN_THRESHOLD = 160
lo = lo.point(lambda v: 0 if v < BIN_THRESHOLD else 255).convert("RGB")

out_file = os.path.join(out_dir, "Hainan_town_county_smooth.png")
lo.save(out_file, format="png")
print(f"已保存 {out_file}（{w_px}×{h_px}）")
print("\n✅ 平滑发丝线版乡镇+县市边界地图生成完毕")
