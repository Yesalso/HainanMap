# -*- coding: utf-8 -*-
"""
ApplyHandDrawnMap.py —— 文昌市：把现行 SHP 边界改为手绘地图(Wenchang2.png)的线条
=================================================================================
按 手绘地图映射要点.txt 的「统一模板」流程执行：

    配准 → 二值化取线(手绘黑线) → 并入权威海岸线 → 形态学闭运算桥接断点
  → 白色区域 8 连通洪泛 + EDT 最近区域归属 → 区域外轮廓 → 与权威陆地求交
  → 缝隙按 40m 归属邻居 → snap-ref 配准式修复(抹 <90m 配准噪声、保留手绘改动)
  → 按乡镇码把新几何写回文昌 SHP(属性/行序不变) → 重栅格化重叠率校验 → 对比预览图

流水线直接复用已通用化的上游脚本（避免重复实现，保持单一来源）：
  ① ../../town/ReverseCounty2002.py   手绘 PNG → 乡镇级多边形(EPSG:32649)
     └ 文字掩膜：text = 现行渲染图(Wenchang4.png)黑像素 − 现行SHP边界栅格
  ② ../../Sanya/fix_slivers.py        snap-ref 配准式修复（挂权威 海南村界.shp）
  ③ 本脚本内完成：新几何写回文昌 SHP + 校验 + 预览

用法（在 town/Hainan2002/Wenchang/ 目录下）：
  python ApplyHandDrawnMap.py                  # 全流程，输出 Hainan_town_wenchang_handdrawn.shp
  python ApplyHandDrawnMap.py --apply          # 备份原主文件后，覆盖 Hainan_town_wenchang.shp
  python ApplyHandDrawnMap.py --only reverse   # 只跑某一步：reverse / fix / apply / verify / preview

Windows 控制台乱码时先执行： $env:PYTHONIOENCODING='utf-8'
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np
import geopandas as gpd
from shapely import make_valid

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))           # .../town/Hainan2002/Wenchang
TOWN = os.path.dirname(os.path.dirname(HERE))               # .../town
TREE = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # .../海南省村界

COUNTY = "469005"                                 # 文昌市
CRS = "EPSG:32649"                                # 正向制图坐标系

CUR_SHP = os.path.join(HERE, "Hainan_town_wenchang.shp")   # 现行文昌乡镇 SHP（任务1产物，Albers）
BAK_SHP = os.path.join(HERE, "Hainan_town_wenchang_before_handdrawn.shp")
OUT_SHP = os.path.join(HERE, "Hainan_town_wenchang_handdrawn.shp")

IMG_HAND = os.path.join(TOWN, "Empty_map", "Wenchang2.png")   # 手绘改线图（目标线条）
IMG_CUR  = os.path.join(TOWN, "Empty_map", "Wenchang4.png")   # 现行 SHP 渲染图（用于文字掩膜）
AUTH     = os.path.join(TREE, "海南村界.shp")                  # 权威村界
REV_SCRIPT = os.path.join(TOWN, "ReverseCounty2002.py")
FIX_SCRIPT = os.path.join(TREE, "Sanya", "fix_slivers.py")

REV_BASE = os.path.join(HERE, "wenchang_reverse")
FIX_BASE = os.path.join(HERE, "wenchang_fixed")
TMP_CUR  = os.path.join(HERE, "_cur_mask_src.shp")   # 现行 SHP 的掩膜副本（重命名字段）

PREVIEW = os.path.join(HERE, "handdrawn_preview.png")
ML = 72.0 / 100.0                                  # 预览线宽（磅→英寸比例）

SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".shp.xml")
PY = sys.executable


def log(m=""):
    print(m, flush=True)


def run(cmd):
    log("  $ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=HERE)
    if r.returncode != 0:
        raise SystemExit(f"✗ 步骤失败（退出码 {r.returncode}）")


def rm_sidecars(path):
    for ext in SIDECARS:
        p = path[:-4] + ext if path.lower().endswith((".shp",)) else path + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def atomic_write(gdf, path):
    rm_sidecars(path)
    tmp = path[:-4] + "__tmp.shp"
    rm_sidecars(tmp)
    gdf.to_file(tmp, encoding="utf-8")
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        src, dst = tmp[:-4] + ext, path[:-4] + ext
        if os.path.exists(src):
            os.replace(src, dst)


def backup_once(path, bak):
    if os.path.exists(bak):
        log(f"  备份已存在，跳过：{bak}")
        return
    for ext in SIDECARS:
        src, dst = path[:-4] + ext, bak[:-4] + ext
        if os.path.exists(src):
            shutil.copy2(src, dst)
    log(f"  已备份 → {bak}")


def load_gray(path):
    import cv2
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


# --------------------------------------------------------------------------- #
# ① 手绘 PNG → 乡镇级多边形
# --------------------------------------------------------------------------- #
def step_reverse():
    log("=" * 70)
    log("① 反向映射：手绘 Wenchang2.png → 乡镇级多边形（文字掩膜用 Wenchang4）")
    log("=" * 70)
    if not os.path.exists(AUTH):
        raise SystemExit(f"✗ 权威 SHP 不存在: {AUTH}")
    if not os.path.exists(IMG_HAND):
        raise SystemExit(f"✗ 手绘图不存在: {IMG_HAND}")

    # 现行 SHP 的掩膜副本：ReverseCounty2002 用 --code-field 同时读权威层与现行层，
    # 故将现行层字段名对齐为 XZQDM/XZQMC，并统一投影到 EPSG:32649
    cur = gpd.read_file(CUR_SHP, encoding="utf-8")
    cur = cur[cur.geometry.notna()].copy()
    cur = cur.to_crs(CRS)
    cur = cur.rename(columns={"CODE": "XZQDM", "TOWN": "XZQMC"})[["XZQDM", "XZQMC", "geometry"]]
    rm_sidecars(TMP_CUR)
    cur.to_file(TMP_CUR, encoding="utf-8")

    run([PY, REV_SCRIPT,
         "--shp", AUTH, "--shp-encoding", "gbk",
         "--code-field", "XZQDM", "--name-field", "XZQMC",
         "--county", COUNTY,
         "--img", IMG_HAND,
         "--img-current", IMG_CUR, "--shp-current", TMP_CUR,
         "--out", REV_BASE])
    rm_sidecars(TMP_CUR)


# --------------------------------------------------------------------------- #
# ② snap-ref 配准式修复（挂权威村界）
# --------------------------------------------------------------------------- #
def step_fix():
    log("=" * 70)
    log("② 几何修复：snap-ref（挂权威 海南村界.shp）")
    log("=" * 70)
    if not os.path.exists(REV_BASE + ".shp"):
        raise SystemExit(f"✗ 缺少反向结果: {REV_BASE}.shp，请先跑 --only reverse")
    run([PY, FIX_SCRIPT,
         "--input", REV_BASE + ".shp",
         "--output", FIX_BASE,
         "--key", "乡镇码",
         "--ref", AUTH, "--ref-encoding", "gbk",
         "--ref-key", "XZQDM", "--ref-key-trim", "9",
         "--ref-filter", f"XZQDM:{COUNTY}",
         "--mode", "snap-ref",
         "--conflate-tol", "90", "--snap-tol", "30",
         "--dissolve-by", "乡镇码",
         "--no-plot"])


# --------------------------------------------------------------------------- #
# ③ 按乡镇码写回文昌 SHP（属性/行序逐字节保留）
# --------------------------------------------------------------------------- #
def step_apply(apply_main=False):
    log("=" * 70)
    log("③ 写回：修复结果 → 文昌乡镇 SHP")
    log("=" * 70)
    if not os.path.exists(FIX_BASE + ".shp"):
        raise SystemExit(f"✗ 缺少修复结果: {FIX_BASE}.shp，请先跑 --only fix")
    if not os.path.exists(CUR_SHP):
        raise SystemExit(f"✗ 现行 SHP 不存在: {CUR_SHP}")

    cur = gpd.read_file(CUR_SHP, encoding="utf-8")
    out_crs = cur.crs
    cols = [c for c in cur.columns if c != "geometry"]
    attr = {}
    for _, r in cur.iterrows():
        attr.setdefault(str(r["CODE"]).replace(".0", "")[:9], dict(r.drop("geometry")))

    fix = gpd.read_file(FIX_BASE + ".shp", encoding="utf-8")
    if fix.crs is not None and out_crs is not None and fix.crs != out_crs:
        fix = fix.to_crs(out_crs)
    fix = fix.copy()
    fix["_code"] = fix["乡镇码"].astype(str).str[:9]
    fix = fix[~fix.geometry.is_empty & fix.geometry.notna()].copy()
    fix["geometry"] = fix.geometry.apply(lambda g: g if g.is_valid else make_valid(g))
    merged = fix.dissolve(by="_code", aggfunc="first").reset_index()

    missing = [c for c in merged["_code"].astype(str) if c not in attr]
    if missing:
        log(f"  ⚠ {len(missing)} 个码在现行图层无属性模板：{missing[:10]}")

    rows = []
    for _, r in merged.iterrows():
        c = str(r["_code"])
        a = attr.get(c, {"CODE": c, "TOWN": c, "CITY": "文昌市", "EN": "", "N_FEAT": 0})
        rec = {}
        for col in cols:
            if col == "CODE":
                rec[col] = a.get("CODE", c)
            elif col in a:
                rec[col] = a[col]
            elif col == "AREA_KM2":
                rec[col] = round(r.geometry.area / 1e6, 3)
            else:
                rec[col] = "" if col in ("TOWN", "CITY", "EN") else (0 if col == "N_FEAT" else None)
        if "AREA_KM2" in cols:
            rec["AREA_KM2"] = round(r.geometry.area / 1e6, 3)
        rec["geometry"] = r.geometry
        rows.append(rec)

    out = gpd.GeoDataFrame(rows, columns=cols + ["geometry"], geometry="geometry", crs=out_crs)
    ov = out.geometry.area.sum()
    log(f"  新结果：{len(out)} 单元，总面积 {ov/1e6:.3f} km²，非法 {int((~out.geometry.is_valid).sum())}")
    atomic_write(out, OUT_SHP)
    out.to_file(OUT_SHP[:-4] + ".geojson", driver="GeoJSON", encoding="utf-8")
    log(f"  已写出：{OUT_SHP} / .geojson")

    if apply_main:
        backup_once(CUR_SHP, BAK_SHP)
        atomic_write(out, CUR_SHP)
        log(f"  已覆盖 → {CUR_SHP}")


# --------------------------------------------------------------------------- #
# ④ 校验：新 SHP 重栅格化 vs 手绘图黑线
# --------------------------------------------------------------------------- #
def _county_transform():
    """权威村界按9位码融合后的四至（与反向映射一致的像素↔米制配准）。"""
    auth = gpd.read_file(AUTH, encoding="gbk").to_crs(CRS)
    a = auth[auth["XZQDM"].astype(str).str[:6] == COUNTY].copy()
    a["_c"] = a["XZQDM"].astype(str).str[:9]
    return a.dissolve(by="_c")


def _rasterize_rings(mask, geom, tf, cv2):
    ring = geom.boundary
    lines = list(ring.geoms) if ring.geom_type in ("MultiLineString", "GeometryCollection") else [ring]
    for ln in lines:
        if ln is None or ln.is_empty:
            continue
        c = np.array(ln.coords)
        if len(c) < 2:
            continue
        pxp, pyp = tf(c)
        cv2.polylines(mask, [np.stack([pxp, pyp], 1).astype(np.int32)], False, 255, 1, cv2.LINE_8)


def step_verify():
    log("=" * 70)
    log("④ 校验：新 SHP 重栅格化 vs 手绘 Wenchang2.png 黑线")
    log("=" * 70)
    if not os.path.exists(OUT_SHP):
        raise SystemExit(f"✗ 缺少结果文件: {OUT_SHP}，请先 --apply 或跑全流程")
    import cv2
    groups = _county_transform()
    minx, miny, maxx, maxy = groups.total_bounds
    img = load_gray(IMG_HAND)
    H, W = img.shape
    geo_w, geo_h = maxx - minx, maxy - miny

    def tf(c):
        return ((c[:, 0] - minx) / geo_w * W, (maxy - c[:, 1]) / geo_h * H)

    g = gpd.read_file(OUT_SHP, encoding="utf-8").to_crs(CRS)
    sub = g[g["CODE"].astype(str).str.startswith(COUNTY)].copy()
    sub = sub[sub.geometry.notna()].copy()
    sub["geometry"] = sub.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    sub = sub.dissolve(by="CODE")

    m = np.zeros((H, W), np.uint8)
    for geom in sub.geometry:
        _rasterize_rings(m, geom, tf, cv2)
    lines = (m == 255).astype(np.uint8)
    dt = cv2.distanceTransform(255 - lines * 255, cv2.DIST_L2, 3)
    dark = (img == 0).astype(np.uint8)
    d = dt[dark == 1]
    log(f"  新界线像素 {int(lines.sum())}，手绘黑像素 {int(dark.sum())}")
    for tol in (1.0, 1.5, 2.0, 3.0):
        log(f"    手绘黑像素距新界线 ≤{tol}px：{(d <= tol).mean():.4f}")
    log(f"    平均 {d.mean():.2f}px  中位 {np.median(d):.2f}px")


# --------------------------------------------------------------------------- #
# ⑤ 前后对比预览图
# --------------------------------------------------------------------------- #
def step_preview():
    if not os.path.exists(OUT_SHP):
        raise SystemExit(f"✗ 缺少结果文件: {OUT_SHP}")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun"]
    except Exception:
        pass
    import cv2

    groups = _county_transform()
    minx, miny, maxx, maxy = groups.total_bounds
    img = load_gray(IMG_HAND)
    H, W = img.shape

    before = gpd.read_file(CUR_SHP, encoding="utf-8").to_crs(CRS)
    after = gpd.read_file(OUT_SHP, encoding="utf-8").to_crs(CRS)
    before = before[before.geometry.notna()].copy()
    after = after[after.geometry.notna()].copy()
    before["geometry"] = before.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    after["geometry"] = after.geometry.apply(lambda x: x if x.is_valid else make_valid(x))

    fig, axes = plt.subplots(1, 3, figsize=(27, 12), dpi=110)
    for ax, df, ttl in ((axes[0], before, "现行 SHP（改前）"),
                        (axes[1], None, "手绘改线 Wenchang2"),
                        (axes[2], after, "手绘改线后的 SHP（结果）")):
        ax.imshow(img, cmap="gray", extent=[minx, maxx, miny, maxy],
                  aspect="equal", alpha=0.5)
        if df is not None:
            df.dissolve(by="CODE").plot(ax=ax, facecolor="#dbe9f6",
                                        edgecolor="#1f4e79", linewidth=ML)
        ax.set_title(ttl, fontsize=14)
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_aspect("equal")
        ax.axis("off")
    plt.tight_layout()
    fig.savefig(PREVIEW, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    log(f"  预览图：{PREVIEW}")


# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["reverse", "fix", "apply", "verify", "preview"],
                    default=None, help="只跑某一步（默认全流程）")
    ap.add_argument("--apply", action="store_true",
                    help="备份后覆盖主文件 Hainan_town_wenchang.shp")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--no-preview", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.only == "reverse":
        step_reverse()
    elif args.only == "fix":
        step_fix()
    elif args.only == "apply":
        step_apply(apply_main=args.apply)
    elif args.only == "verify":
        step_verify()
    elif args.only == "preview":
        step_preview()
    else:
        step_reverse()
        step_fix()
        step_apply(apply_main=args.apply)
        if not args.no_verify:
            step_verify()
        if not args.no_preview:
            step_preview()
        log("")
        log("全流程完成。新文件：" + OUT_SHP)


if __name__ == "__main__":
    main()