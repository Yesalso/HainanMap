# -*- coding: utf-8 -*-
"""
reverse_county_pipeline.py —— 手绘地图 → SHP 全流程一键驱动
============================================================
把「手绘地图 PNG 反向渲染回乡镇 SHP，并处理缝隙/重叠/配准」的完整流程
整理成一份可直接执行的脚本。文件准备好后，改一下下方 CONFIG 即可运行。

步骤：
  ① ReverseCounty2002.py   手绘 PNG → 乡镇级多边形（洪泛+轮廓吸附）
  ② Sanya/fix_slivers.py   snap-ref 配准式修复（挂权威 海南村界.shp，
                           抹平 <conflate_tol 的配准噪声，保留真实手绘改动）
  ③ ApplyCounty2002.py     把修复结果合并回 Hainan_town.shp（先备份）
  ④ verify                 用新 SHP 重新栅格化，与手绘图比对重合率

运行：
  python reverse_county_pipeline.py
  python reverse_county_pipeline.py --county 469005 --img ../Empty_map/Wenchang2.png --name wenchang
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import numpy as np
import cv2
import geopandas as gpd
from shapely.ops import unary_union
from shapely import make_valid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PY = sys.executable


def log(m=""):
    print(m, flush=True)


def run(cmd):
    log("  $ " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        raise SystemExit(f"✗ 步骤失败（退出码 {r.returncode}）")


def load_gray(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def verify(merged_shp, prefix, img_path, crs="EPSG:32649"):
    """用新 SHP 的目标县重新栅格化，统计与手绘图黑线的重合率。"""
    g = gpd.read_file(merged_shp, encoding="utf-8").to_crs(crs)
    g = g[g["CODE"].astype(str).str.startswith(str(prefix))].copy()
    g["geometry"] = g.geometry.apply(lambda x: x if x.is_valid else make_valid(x))
    groups = g.dissolve(by="CODE")
    minx, miny, maxx, maxy = groups.total_bounds
    geo_w, geo_h = maxx - minx, maxy - miny
    img = load_gray(img_path)
    H, W = img.shape
    dark = (img == 0).astype(np.uint8)

    lines = np.zeros((H, W), np.uint8)
    for geom in groups.geometry:
        ring = geom.boundary
        segs = list(ring.geoms) if ring.geom_type in ("MultiLineString", "GeometryCollection") else [ring]
        for ln in segs:
            if ln is None or ln.is_empty:
                continue
            c = np.array(ln.coords)
            if len(c) < 2:
                continue
            pxp = (c[:, 0] - minx) / geo_w * W
            pyp = (maxy - c[:, 1]) / geo_h * H
            cv2.polylines(lines, [np.stack([pxp, pyp], 1).astype(np.int32)],
                          False, 255, 1, cv2.LINE_8)
    lines = (lines == 255).astype(np.uint8)
    dt = cv2.distanceTransform((1 - lines).astype(np.uint8) * 255, cv2.DIST_L2, 3)
    d = dt[dark == 1]
    log(f"  新 SHP 栅格化 vs 手绘图：")
    for tol in (1.0, 1.5, 2.0, 3.0):
        log(f"    黑像素距新界线 ≤{tol}px：{(d <= tol).mean():.4f}")
    log(f"    平均 {d.mean():.2f}px  中位 {np.median(d):.2f}px")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", default="469005", help="县码前缀")
    ap.add_argument("--img", default=os.path.join(HERE, "Empty_map", "Wenchang2.png"),
                    help="手绘地图 PNG")
    ap.add_argument("--name", default="wenchang", help="输出/备份后缀名")
    ap.add_argument("--ref", default=os.path.join(ROOT, "海南村界.shp"),
                    help="权威村界 SHP")
    ap.add_argument("--hainan", default=os.path.join(HERE, "Hainan2002", "Hainan_town.shp"),
                    help="全岛乡镇 SHP")
    ap.add_argument("--out-dir", default=HERE, help="中间结果目录")
    ap.add_argument("--conflate-tol", type=float, default=90.0)
    ap.add_argument("--snap-tol", type=float, default=30.0)
    ap.add_argument("--min-region-px", type=int, default=50)
    ap.add_argument("--min-keep-m2", type=float, default=5e4)
    ap.add_argument("--only", choices=["reverse", "fix", "apply", "verify"], default=None,
                    help="只跑某一步（默认全跑）")
    args = ap.parse_args(argv)

    rev_script = os.path.join(HERE, "ReverseCounty2002.py")
    fix_script = os.path.join(ROOT, "Sanya", "fix_slivers.py")
    apply_script = os.path.join(HERE, "ApplyCounty2002.py")

    rev_base = os.path.join(args.out_dir, f"{args.name}_reverse")
    fix_base = os.path.join(args.out_dir, f"{args.name}_fixed")

    if args.only in (None, "reverse"):
        log("=" * 70)
        log("① 手绘 PNG → 乡镇多边形（反向映射）")
        log("=" * 70)
        run([PY, rev_script,
             "--shp", args.ref, "--shp-encoding", "gbk",
             "--code-field", "XZQDM", "--name-field", "XZQMC",
             "--county", args.county,
             "--img", args.img,
             "--min-region-px", str(args.min_region_px),
             "--min-keep-m2", str(args.min_keep_m2),
             "--out", rev_base])

    if args.only in (None, "fix"):
        log("=" * 70)
        log("② 配准式修复（snap-ref，挂权威村界）")
        log("=" * 70)
        run([PY, fix_script,
             "--input", rev_base + ".shp",
             "--output", fix_base,
             "--key", "乡镇码",
             "--ref", args.ref,
             "--ref-encoding", "gbk",
             "--ref-key", "XZQDM",
             "--ref-key-trim", "9",
             "--ref-filter", f"XZQDM:{args.county}",
             "--mode", "snap-ref",
             "--conflate-tol", str(args.conflate_tol),
             "--snap-tol", str(args.snap_tol),
             "--dissolve-by", "乡镇码",
             "--no-plot"])

    if args.only in (None, "apply"):
        log("=" * 70)
        log("③ 合并回 Hainan_town.shp")
        log("=" * 70)
        run([PY, apply_script,
             "--hainan", args.hainan,
             "--county", fix_base + ".shp",
             "--prefix", args.county,
             "--name", args.name,
             "--ref", args.ref])

    if args.only in (None, "verify"):
        log("=" * 70)
        log("④ 校验：新 SHP 重新栅格化 vs 手绘图")
        log("=" * 70)
        verify(args.hainan, args.county, args.img)

    log("")
    log("全流程完成。")


if __name__ == "__main__":
    main()
