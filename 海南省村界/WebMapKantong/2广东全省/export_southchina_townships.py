# -*- coding: utf-8 -*-
"""把「华南乡镇界线」矢量数据的属性表导出为一份 XLSX 清单。

数据源：
    华南乡镇界线.shp / .dbf   —— 已合并广东、广西、海南、香港、澳门的乡镇（区）界线
    行政区划名称对照表.csv     —— 可选，按 4/6/9 位代码补全市、县、乡镇名称

输出：
    华南地区乡镇一览表.xlsx
        ├─ 乡镇一览   全部乡镇（街道）逐条列出
        ├─ 分省统计   每个省级单位的乡镇数、面积
        ├─ 分市统计   每个地级市的乡镇数、面积
        └─ 说明       数据来源、字段定义、名称补全情况

依赖：openpyxl（只用于写出表格，读取 dbf 为纯标准库实现，不需要 geopandas/GDAL）

用法：
    python export_southchina_townships.py
    python export_southchina_townships.py -o 我的清单.xlsx
    python export_southchina_townships.py --no-hkmo        # 只保留粤桂琼
    python export_southchina_townships.py --keep-nonadmin  # 保留着色用合并多边形
    python export_southchina_townships.py --csv            # 同时导出一份 UTF-8-BOM 的 csv
"""

from __future__ import annotations

import argparse
import csv
import re
import struct
import sys
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = Path(__file__).resolve().parent
DEFAULT_DBF = HERE / "华南乡镇界线.dbf"
DEFAULT_NAME_REF = HERE / "行政区划名称对照表.csv"
DEFAULT_OUT = HERE / "华南地区乡镇一览表.xlsx"

# 省级单位排序（华南：粤、桂、琼 + 港澳）
PROV_ORDER = {
    "广东省": 1,
    "广西壮族自治区": 2,
    "海南省": 3,
    "香港特别行政区": 4,
    "澳门特别行政区": 5,
}
PROV_FILL = {
    "广东省": "EAF3FB",
    "广西壮族自治区": "FDF1E3",
    "海南省": "EAF6EC",
    "香港特别行政区": "FBEEF4",
    "澳门特别行政区": "FBF0E6",
}

HEADERS = ["序号", "省级", "地级市", "县（区）", "乡镇（街道）", "乡镇码", "类型", "面积(km²)", "要素数", "名称来源"]
WIDTHS = [6, 16, 16, 20, 22, 13, 17, 12, 9, 14]

NULLISH = {"nan", "none", "null", "<na>", "-"}

# 县级代码变更（底图用旧码、对照表用新码）：旧码 -> (新码, 显示名)
# 乡镇码后 3 位在撤县设市时通常保持不变，可据此迁移匹配
COUNTY_ALIAS = {
    "450127": ("450181", "横县（今横州市）"),
}


def resolve_county(code: str, ref: dict[str, str]) -> str | None:
    if code in ref:
        return ref[code]
    alias = COUNTY_ALIAS.get(code)
    if alias and alias[0] in ref:
        return alias[1]
    return None


def resolve_township(code: str, ref: dict[str, str]) -> str | None:
    if code in ref:
        return ref[code]
    alias = COUNTY_ALIAS.get(code[:6])
    if alias:
        return ref.get(alias[0] + code[6:])
    return None


def clean(value: str) -> str:
    """去掉空白，并把源数据里以文本形式出现的 nan/none/null 视为空。"""
    s = (value or "").strip()
    return "" if s.lower() in NULLISH else s


# --------------------------------------------------------------------------- #
# 1. DBF 读取（Shapefile 属性表，纯标准库）
# --------------------------------------------------------------------------- #
def _text(raw: bytes) -> str:
    """DBF 字段值解码：先按 UTF-8，失败再退 GBK。"""
    s = raw.rstrip(b"\x00").strip()
    if not s:
        return ""
    try:
        return s.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return s.decode("gbk")
        except UnicodeDecodeError:
            return s.decode("latin-1")


def read_dbf(path: Path) -> list[dict[str, str]]:
    """读取 dBASE III 表（.dbf），返回 [{字段名: 值}]。"""
    data = path.read_bytes()
    n_rec = struct.unpack_from("<I", data, 4)[0]
    header_len = struct.unpack_from("<H", data, 8)[0]
    rec_len = struct.unpack_from("<H", data, 10)[0]

    fields: list[tuple[str, str, int]] = []
    pos = 32
    while pos < header_len and data[pos] != 0x0D:
        name = _text(data[pos : pos + 11])
        ftype = chr(data[pos + 11])
        flen = data[pos + 16]
        fields.append((name, ftype, flen))
        pos += 32

    rows: list[dict[str, str]] = []
    offset = header_len
    for _ in range(n_rec):
        rec = data[offset : offset + rec_len]
        offset += rec_len
        if len(rec) < rec_len or rec[:1] == b"*":  # 删除标记
            continue
        p = 1
        row: dict[str, str] = {}
        for name, _ftype, flen in fields:
            row[name] = _text(rec[p : p + flen])
            p += flen
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# 2. 名称对照表
# --------------------------------------------------------------------------- #
def load_name_ref(path: Path | None) -> dict[str, str]:
    """载入「乡镇码 → 乡镇名称」对照表（csv 需含 code/name 或 乡镇码/乡镇名称 列）。"""
    if not path or not path.exists():
        return {}
    table: dict[str, str] = {}
    for enc in ("utf-8-sig", "gbk"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                cols = reader.fieldnames or []
                code_col = next((c for c in cols if c.lower() in ("code", "乡镇码")), None)
                name_col = next((c for c in cols if c.lower() in ("name", "乡镇名称")), None)
                if not code_col or not name_col:
                    return {}
                for r in reader:
                    code = (r.get(code_col) or "").strip()
                    nm = (r.get(name_col) or "").strip()
                    if code and nm:
                        table[code] = nm
            return table
        except UnicodeDecodeError:
            continue
    return {}


# --------------------------------------------------------------------------- #
# 3. 规范化
# --------------------------------------------------------------------------- #
def guess_type(name: str, prov: str) -> str:
    """按名称后缀判定乡级单位类型（顺序敏感：先判街道/镇/乡，再判区）。"""
    n = name.strip()
    if not n or n == "（名称缺失）":
        return ""
    if re.search(r"(街道|街道办|办事处)$", n):
        return "街道"
    if n.endswith("镇"):
        return "镇"
    if re.search(r"(乡|苏木)$", n):
        return "乡"
    if any(k in n for k in ("林场", "农场", "牧场", "渔场", "茶场", "良种场", "林业局")):
        return "农林场"
    if prov.startswith(("香港", "澳门")) and n.endswith(("区", "區")):
        return "区/堂区"
    if re.search(r"(管理委员会|管委会|开发区|保税区|工业区|新区|港区|水库|监狱|劳教所|海岛)$", n):
        return "管理区/特殊单元"
    if n.endswith("区"):
        return "区/片区"
    return "其他"


def is_admin_code(code: str) -> bool:
    """9 位数字码（内地乡镇/街道）；港澳用 HK/MO 前缀的字母码。"""
    return bool(re.fullmatch(r"\d{9}", code)) or bool(re.fullmatch(r"(HK|MO)[A-Z0-9]+", code))


def normalize(rows: list[dict[str, str]], name_ref: dict[str, str], keep_nonadmin: bool) -> tuple[list[dict], Counter]:
    stats: Counter = Counter()
    out: list[dict] = []
    dropped: list[dict] = []

    for r in rows:
        code = clean(r.get("乡镇码"))
        prov = clean(r.get("省"))
        city = clean(r.get("市"))
        county = clean(r.get("县"))
        name = clean(r.get("乡镇"))
        area_raw = clean(r.get("面积km2"))
        feat_raw = clean(r.get("要素数"))

        # 源数据（广西）的市、县字段存的是行政代码，换成名称
        if re.fullmatch(r"\d{4}", city):
            city = name_ref.get(city, city)
        if re.fullmatch(r"\d{6}", county):
            county = resolve_county(county, name_ref) or county

        if not is_admin_code(code):
            # 例如 RED_ORANGE / BLUE 这类地图着色用的跨区合并多边形，不是行政区
            if not keep_nonadmin:
                dropped.append({"乡镇码": code, "乡镇": name, "市": city})
                continue
            source = "源数据（非行政区）"
        elif name:
            source = "源数据"
        elif resolve_township(code, name_ref):
            name, source = resolve_township(code, name_ref) or "", "对照表补全"
        else:
            name, source = "", "缺失"

        try:
            area = round(float(area_raw), 3) if area_raw else None
        except ValueError:
            area = None
        try:
            feat = int(float(feat_raw)) if feat_raw else None
        except ValueError:
            feat = None

        if not name:
            stats["name_missing"] += 1

        out.append(
            {
                "省": prov,
                "市": city,
                "县": county,
                "乡镇": name or "（名称缺失）",
                "乡镇码": code,
                "类型": guess_type(name, prov),
                "面积": area,
                "要素数": feat,
                "名称来源": source,
            }
        )

    # 按 省 → 乡镇码 排序，等于按行政区划顺序（市、县、乡镇依次展开）
    out.sort(key=lambda r: (PROV_ORDER.get(r["省"], 9), r["乡镇码"]))
    if dropped:
        stats["dropped"] = len(dropped)
        stats["dropped_sample"] = "；".join(f"{d['乡镇码']}={d['乡镇']}" for d in dropped[:6])
    return out, stats


# --------------------------------------------------------------------------- #
# 4. 汇总
# --------------------------------------------------------------------------- #
def summarize(rows: list[dict], key: str) -> "OrderedDict[tuple, dict]":
    agg: "OrderedDict[tuple, dict]" = OrderedDict()
    for r in rows:
        k = (r["省"], r[key]) if key != "省" else (r["省"],)
        d = agg.setdefault(k, {"n": 0, "area": 0.0, "counties": set(), "cities": set()})
        d["n"] += 1
        d["area"] += r["面积"] or 0.0
        if r["县"]:
            d["counties"].add(r["县"])
        if r["市"]:
            d["cities"].add(r["市"])
    return agg


# --------------------------------------------------------------------------- #
# 5. 写 XLSX
# --------------------------------------------------------------------------- #
def write_xlsx(rows: list[dict], stats: Counter, out_path: Path, src_name: str, name_ref_used: bool) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
    body_font = Font(name="微软雅黑", size=10)
    zebra = PatternFill("solid", fgColor="F7F9FB")

    wb = Workbook()

    # ---------------- 乡镇一览 ----------------
    ws = wb.active
    ws.title = "乡镇一览"
    ws.append(HEADERS)
    for c in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    ws.row_dimensions[1].height = 24

    for i, r in enumerate(rows, 1):
        ws.append(
            [
                i,
                r["省"],
                r["市"],
                r["县"],
                r["乡镇"],
                r["乡镇码"],
                r["类型"],
                r["面积"],
                r["要素数"],
                r["名称来源"],
            ]
        )
        row_idx = ws.max_row
        for c in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=row_idx, column=c)
            cell.font = body_font
            cell.border = border
            if c in (1, 6, 7, 9):
                cell.alignment = Alignment(horizontal="center")
            if c == 6:
                cell.number_format = "@"  # 乡镇码按文本，避免前导 0 丢失
            if c == 8:
                cell.number_format = "#,##0.00"
        if i % 2 == 0:
            for c in range(1, len(HEADERS) + 1):
                ws.cell(row=row_idx, column=c).fill = zebra
        prov_cell = ws.cell(row=row_idx, column=2)
        prov_cell.fill = PatternFill("solid", fgColor=PROV_FILL.get(r["省"], "FFFFFF"))

    for idx, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{ws.max_row}"

    # ---------------- 分省统计 ----------------
    ws2 = wb.create_sheet("分省统计")
    h2 = ["省级", "市级区划数", "县级区划数", "乡镇（街道）数", "面积合计(km²)", "平均面积(km²)"]
    ws2.append(h2)
    for c in range(1, len(h2) + 1):
        cell = ws2.cell(row=1, column=c)
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    prov_rows = [r for r in rows if r["省"]]
    for prov, d in summarize(prov_rows, "省").items():
        ws2.append(
            [
                prov[0],
                len({r["市"] for r in prov_rows if r["省"] == prov[0] and r["市"]}),
                len({(r["市"], r["县"]) for r in prov_rows if r["省"] == prov[0] and (r["市"] or r["县"])}),
                d["n"],
                round(d["area"], 2),
                round(d["area"] / d["n"], 2) if d["n"] else 0,
            ]
        )
        for c in range(1, len(h2) + 1):
            cell = ws2.cell(row=ws2.max_row, column=c)
            cell.font = body_font
            cell.border = border
            cell.alignment = Alignment(horizontal="center" if c != 1 else "left")
            if c in (5, 6):
                cell.number_format = "#,##0.00"

    total_n = len(prov_rows)
    total_a = sum(r["面积"] or 0 for r in prov_rows)
    ws2.append(["合计", "", "", total_n, round(total_a, 2), round(total_a / total_n, 2) if total_n else 0])
    for c in range(1, len(h2) + 1):
        cell = ws2.cell(row=ws2.max_row, column=c)
        cell.font = Font(name="微软雅黑", size=10, bold=True)
        cell.border = border
        cell.fill = PatternFill("solid", fgColor="EDEDED")
        cell.alignment = Alignment(horizontal="center" if c != 1 else "left")
        if c in (5, 6):
            cell.number_format = "#,##0.00"

    for idx, w in enumerate([20, 12, 14, 16, 16, 16], 1):
        ws2.column_dimensions[get_column_letter(idx)].width = w
    ws2.freeze_panes = "A2"

    # ---------------- 分市统计 ----------------
    ws3 = wb.create_sheet("分市统计")
    h3 = ["省级", "市级区划", "乡镇（街道）数", "县级区划数", "面积合计(km²)"]
    ws3.append(h3)
    for c in range(1, len(h3) + 1):
        cell = ws3.cell(row=1, column=c)
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    for key, d in summarize([r for r in rows if r["市"]], "市").items():
        ws3.append([key[0], key[1], d["n"], len(d["counties"]), round(d["area"], 2)])
        for c in range(1, len(h3) + 1):
            cell = ws3.cell(row=ws3.max_row, column=c)
            cell.font = body_font
            cell.border = border
            if c != 2:
                cell.alignment = Alignment(horizontal="center")
            if c == 5:
                cell.number_format = "#,##0.00"

    for idx, w in enumerate([20, 20, 16, 14, 16], 1):
        ws3.column_dimensions[get_column_letter(idx)].width = w
    ws3.freeze_panes = "A2"
    ws3.auto_filter.ref = f"A1:E{ws3.max_row}"

    # ---------------- 说明 ----------------
    ws4 = wb.create_sheet("说明")
    ws4.column_dimensions["A"].width = 18
    ws4.column_dimensions["B"].width = 96
    missing = sum(1 for r in rows if r["名称来源"] == "缺失")
    filled = sum(1 for r in rows if r["名称来源"] == "对照表补全")
    info = [
        ("数据来源", f"{src_name}（属性表 .dbf，未使用几何，坐标系 CGCS2000 Albers）"),
        ("覆盖范围", "广东省、广西壮族自治区、海南省、香港特别行政区、澳门特别行政区"),
        ("记录总数", f"{len(rows)} 条乡镇级单元"),
        ("分省数量", "；".join(f"{p} {n} 条" for p, n in Counter(r["省"] for r in rows).most_common())),
        ("名称补全", f"对照表补全 {filled} 条；仍缺失 {missing} 条（已标注「名称缺失」）"),
        ("剔除记录", f"{stats.get('dropped', 0)} 条非行政区多边形"
                     + (f"（{stats.get('dropped_sample')}）" if stats.get("dropped_sample") else "")),
        ("名称对照表", "已启用（行政区划名称对照表.csv）" if name_ref_used else "未找到，市/县/乡镇名称仅取自源数据"),
        ("字段说明", "省级/地级市/县（区）/乡镇（街道）：行政区划层级；乡镇码：9 位统计用区划代码，港澳为 HK/MO 前缀"),
        ("", "类型：按名称后缀判定（街道、镇、乡、农林场、区/堂区、管理区/特殊单元、区/片区、其他）"),
        ("", "面积(km²)：多边形面积；要素数：该乡镇由多少个原始图斑融合而成"),
        ("", "名称来源：源数据 / 对照表补全 / 缺失"),
        ("港澳说明", "香港 18 区、澳门 11 个堂区在行政层级上相当于县级，此处按其底图单元一并列出，类型标为「区/堂区」"),
        ("层级说明", "海南实行省直管县，源数据「市」字段填的是县级市/县名，故其市级区划数偏多；广东含 1950 年代自广西划入的钦廉地区（乡镇码 45 开头）"),
        ("历史地名", "底图沿用部分 1950 年代建制名（如海南「府城市/嘉积市/那大市」、广西「梧州专区」、广东「北海市」），与现行区划不完全一致"),
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    for i, (k, v) in enumerate(info, 1):
        a, b = ws4.cell(row=i, column=1, value=k), ws4.cell(row=i, column=2, value=v)
        a.font = Font(name="微软雅黑", size=10, bold=True)
        b.font = Font(name="微软雅黑", size=10)
        a.alignment = Alignment(vertical="top", wrap_text=True)
        b.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(out_path)


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["省"], r["市"], r["县"], r["乡镇"], r["乡镇码"], r["类型"], r["面积"], r["要素数"], r["名称来源"]])


# --------------------------------------------------------------------------- #
# 6. 入口
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="导出华南地区全部乡镇（街道）清单到 XLSX")
    ap.add_argument("-d", "--dbf", type=Path, default=DEFAULT_DBF, help="乡镇界线属性表 .dbf")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help="输出 xlsx 路径")
    ap.add_argument("--name-ref", type=Path, default=DEFAULT_NAME_REF, help="乡镇名称对照表 csv")
    ap.add_argument("--no-hkmo", action="store_true", help="不包含香港、澳门")
    ap.add_argument("--province", nargs="*", help="只导出指定省级单位，如 --province 广东省 海南省")
    ap.add_argument("--keep-nonadmin", action="store_true", help="保留着色用的合并多边形")
    ap.add_argument("--csv", action="store_true", help="同时导出 csv")
    args = ap.parse_args()

    if not args.dbf.exists():
        sys.exit(f"找不到数据文件: {args.dbf}")

    print(f"读取属性表: {args.dbf.name}")
    raw = read_dbf(args.dbf)
    print(f"  原始记录: {len(raw)} 条")

    name_ref = load_name_ref(args.name_ref)
    print(f"名称对照表: {len(name_ref)} 条" if name_ref else "名称对照表: 未找到（跳过补全）")

    rows, stats = normalize(raw, name_ref, args.keep_nonadmin)

    if args.no_hkmo:
        rows = [r for r in rows if r["省"] not in ("香港特别行政区", "澳门特别行政区")]
    if args.province:
        want = set(args.province)
        rows = [r for r in rows if r["省"] in want]
    if not rows:
        sys.exit("筛选后没有记录，请检查 --province 参数")

    print(f"  导出记录: {len(rows)} 条")
    print("  分省统计:")
    for prov, n in Counter(r["省"] for r in rows).most_common():
        print(f"    {prov:<10} {n:>5} 条")
    if stats.get("dropped"):
        print(f"  已剔除非行政区多边形 {stats['dropped']} 条: {stats.get('dropped_sample', '')}")
    filled = sum(1 for r in rows if r["名称来源"] == "对照表补全")
    missing = sum(1 for r in rows if r["名称来源"] == "缺失")
    print(f"  名称补全 {filled} 条，仍缺失 {missing} 条")

    try:
        write_xlsx(rows, stats, args.out, args.dbf.name, bool(name_ref))
    except ImportError:
        sys.exit("缺少 openpyxl，请先执行:  pip install openpyxl")
    print(f"已写出: {args.out}")

    if args.csv:
        csv_path = args.out.with_suffix(".csv")
        write_csv(rows, csv_path)
        print(f"已写出: {csv_path}")


if __name__ == "__main__":
    main()
