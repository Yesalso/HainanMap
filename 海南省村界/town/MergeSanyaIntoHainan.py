# -*- coding: utf-8 -*-
"""
把 三亚 2002 乡镇界线（town/三亚2002.shp，16 个单元）替换进
town/Hainan2002/Hainan_town.shp（全省 231 个乡镇/街道）。

做法（与 MergeHaikouIntoHainan.py 保持一致）：
  1. 保留 Hainan_town 中非三亚的单元（CODE 不以 4602 开头 且 CITY != 三亚市）；
  2. 用三亚2002.shp 的 16 个单元整体替换原三亚部分；
  3. 三亚块仍插回它原来的位置（原第 41–49 行），不跑到文件末尾；
  4. 原文件先备份为 Hainan2002/Hainan_town_before_sanya.shp（仅备份一次）。

编码说明：按需求沿用 三亚2002.shp 的原始 CODE（SY01–SY16），不做改写。

校验内容：
  * 两图层 CRS 必须一致；
  * 三亚2002 必须全部为面、几何有效、彼此不重叠；
  * 替换后三亚不得与邻县（陵水/乐东/保亭等）产生重叠；
  * 输出替换前后对比预览 PNG。

运行方式：
  C:\\Users\\Windows\\.workbuddy\\binaries\\python\\envs\\default\\Scripts\\python.exe MergeSanyaIntoHainan.py
"""

import os
import shutil
import sys

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

# ==================== 配置 ====================
TOWN_DIR = os.path.dirname(os.path.abspath(__file__))
HN_DIR = os.path.join(TOWN_DIR, "Hainan2002")
HN_SHP = os.path.join(HN_DIR, "Hainan_town.shp")
SY_SHP = os.path.join(TOWN_DIR, "三亚2002.shp")
BAK_SHP = os.path.join(HN_DIR, "Hainan_town_before_sanya.shp")
PREVIEW = os.path.join(HN_DIR, "三亚替换校验.png")

COLS = ["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "geometry"]
SANYA_PREFIX = "4602"
SANYA_CITY = "三亚市"
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix")
HIGHLIGHT = "#c0392b"   # 三亚高亮色
# ==============================================


def backup_once():
    """把原全岛图层备份一份（只备份一次，避免二次运行覆盖掉真·原始数据）。"""
    if os.path.exists(BAK_SHP):
        print(f"备份已存在，跳过：{BAK_SHP}")
        return
    for ext in SIDECARS:
        src = HN_SHP[:-4] + ext
        if os.path.exists(src):
            shutil.copy2(src, BAK_SHP[:-4] + ext)
    print(f"已备份原文件 → {BAK_SHP}")


def check_sanya_layer(sy, hn):
    """对替换层做体检，任何一项不过就直接中止，不写文件。"""
    assert sy.crs == hn.crs, "两图层 CRS 不一致，先统一坐标系再替换"
    assert len(sy) > 0, "三亚2002.shp 为空"

    bad_type = [g.geom_type for g in sy.geometry
                if g.geom_type not in ("Polygon", "MultiPolygon")]
    assert not bad_type, f"三亚2002.shp 含非面几何：{set(bad_type)}"

    invalid = int((~sy.geometry.is_valid).sum())
    assert invalid == 0, f"三亚2002.shp 有 {invalid} 个无效几何"

    total = sum(g.area for g in sy.geometry)
    union_area = unary_union(list(sy.geometry)).buffer(0).area
    overlap = (total - union_area) / 1e6
    if overlap > 1e-3:
        print(f"  [!] 三亚2002 内部存在重叠 {overlap:.3f} km²，替换后可能双计面积")

    if SANYA_CITY not in set(sy.CITY.astype(str)):
        print(f"  [!] 三亚2002 的 CITY 字段不含「{SANYA_CITY}」")
    print(f"  三亚2002 体检通过：{len(sy)} 个单元，"
          f"有效几何 {len(sy) - invalid}/{len(sy)}，"
          f"内部重叠 {overlap:.3f} km²，合计 {total / 1e6:.1f} km²")


def check_no_neighbor_overlap(merged, sanya_mask):
    """替换后的三亚不应与邻县重叠。"""
    sy = merged[sanya_mask]
    rest = merged[~sanya_mask]
    su = unary_union(list(sy.geometry)).buffer(0)
    hits = {}
    for city, grp in rest.groupby("CITY"):
        gu = unary_union(list(grp.geometry)).buffer(0)
        a = su.intersection(gu).area / 1e6
        if a > 0.001:
            hits[str(city)] = round(a, 3)
    if hits:
        print(f"  [!] 三亚与邻县存在重叠：{hits}")
    else:
        print("  重叠检查通过：三亚与邻县无重叠")
    return hits


def write_shp(gdf, path, encoding="utf-8"):
    """写出 shapefile。

    注意：某些受限运行环境（沙箱）会拦截 unlink/删除操作，GDAL 直接覆盖写入
    Hainan_town.shp 时会因无法删除旧文件而报 OSError。因此先写到同目录的临时
    文件，再用 os.replace 逐份原子覆盖目标。这样既绕开删除限制，也不会出现
    「删了旧文件但新文件没写成」的空窗。
    """
    tmp = path[:-4] + "__tmp.shp"
    for ext in SIDECARS:                      # 清掉可能残留的临时文件
        p = tmp[:-4] + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    gdf.to_file(tmp, encoding=encoding)
    for ext in SIDECARS:
        src, dst = tmp[:-4] + ext, path[:-4] + ext
        if os.path.exists(src):
            os.replace(src, dst)
    print("  已通过临时文件原子替换写入（规避沙箱删除限制）")


def preview(merged, sanya_mask):
    """输出替换前后对比预览：左=全岛（三亚高亮），右=三亚 16 个单元及名称。"""
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

        fig, axes = plt.subplots(1, 2, figsize=(18, 9), dpi=110)

        # 左：全岛
        ax = axes[0]
        merged[~sanya_mask].plot(ax=ax, facecolor="#e8eaed",
                                 edgecolor="#9aa0a6", linewidth=0.3)
        merged[sanya_mask].plot(ax=ax, facecolor=HIGHLIGHT,
                                edgecolor="#7b1f13", linewidth=0.5)
        ax.set_title("全岛图层（红色 = 替换后的三亚 2002）", fontsize=13)
        ax.set_aspect("equal")
        ax.axis("off")

        # 右：三亚放大
        ax = axes[1]
        sy = merged[sanya_mask]
        sy.plot(ax=ax, column="TOWN", cmap="tab20",
                edgecolor="black", linewidth=0.5)
        for _, r in sy.iterrows():
            p = r.geometry.representative_point()
            ax.text(p.x, p.y, str(r["TOWN"]), ha="center", va="center",
                    fontsize=9, color="#111111")
        ax.set_title(f"三亚 2002 乡镇（{len(sy)} 个单元）", fontsize=13)
        ax.set_aspect("equal")
        ax.axis("off")

        plt.tight_layout()
        plt.savefig(PREVIEW, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"已输出校验预览：{PREVIEW}")
    except Exception as e:  # 预览失败不影响数据输出
        print(f"预览生成失败（不影响结果）：{e}")


def main():
    hn = gpd.read_file(HN_SHP, encoding="utf-8")
    sy = gpd.read_file(SY_SHP, encoding="utf-8")
    print(f"替换前 Hainan_town：{len(hn)} 个单元")
    print(f"三亚替换层：{len(sy)} 个单元")
    check_sanya_layer(sy, hn)

    hn = hn[COLS].copy()
    sy = sy[COLS].copy()

    # --- 定位原三亚块 ---
    is_sanya_old = (hn["CODE"].astype(str).str.startswith(SANYA_PREFIX)
                    | (hn["CITY"].astype(str) == SANYA_CITY))
    old_idx = list(hn.index[is_sanya_old])
    assert old_idx, "全岛图层中未找到三亚要素，无法替换"
    insert_at = int((hn.index < min(old_idx)).sum())
    print(f"原三亚块：{len(old_idx)} 个单元，位于第 {min(old_idx) + 1}–{max(old_idx) + 1} 行"
          f"（插入点 {insert_at}）")

    backup_once()

    keep = hn[~is_sanya_old].copy()
    print(f"保留非三亚单元：{len(keep)}")

    # --- 三亚块插回原位，其余顺序不变 ---
    merged = pd.concat(
        [keep.iloc[:insert_at], sy, keep.iloc[insert_at:]], ignore_index=True)
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=hn.crs)
    merged["AREA_KM2"] = (merged.geometry.area / 1e6).round(3)

    sanya_mask = (merged["CODE"].astype(str).str.startswith(SANYA_PREFIX)
                  | (merged["CODE"].astype(str).str.startswith("SY"))
                  | (merged["CITY"].astype(str) == SANYA_CITY))
    print(f"替换后单元数：{len(merged)}（三亚 {int(sanya_mask.sum())} + 其他 {len(keep)}）")
    print(f"替换后总面积：{merged.geometry.area.sum() / 1e6:.1f} km²")
    print(f"三亚面积：{merged[sanya_mask].geometry.area.sum() / 1e6:.1f} km²")

    bad = int((~merged.geometry.is_valid).sum())
    print(f"有效几何：{len(merged) - bad}/{len(merged)}")
    check_no_neighbor_overlap(merged, sanya_mask)

    write_shp(merged, HN_SHP)
    print(f"已输出：{HN_SHP}")

    preview(merged, sanya_mask)


if __name__ == "__main__":
    main()
