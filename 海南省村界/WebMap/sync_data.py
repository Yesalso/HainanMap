# -*- coding: utf-8 -*-
"""
sync_data.py —— 将 Hainan_Code.xlsx 中的乡镇地名与人口普查数据同步到 data.js

用法：
    python sync_data.py

功能：
    1. 读取 Hainan_Code.xlsx Sheet1（按表头名自动定位列，列序变化不影响）：
         - CODE           乡镇区划代码（9 位，不足补 0）
         - New_Name       最新地名（无该列时依次回退 TOWN / 行政区名称）
         - 七人普/六普/五普常住人口（列名模糊匹配，含"七/六/五"和"常住人口"即可）
    2. 按区划代码合并进 data.js 的 window.HN_DATA：
         - towns[].town / pop7 / pop6 / pop5
         - cities[].pop7 / pop6 / pop5（按下属乡镇汇总重算）
    3. 控制台输出全部变更明细；无变化时提示"数据已是最新"。

注意：只读 xlsx，不修改它；data.js 会整体重写（格式保持单行 JSON）。
"""
import json
import os
import re
import sys

import openpyxl

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
XLSX_PATH = os.path.join(BASE, "Hainan_Code.xlsx")
DATAJS_PATH = os.path.join(BASE, "data.js")

POP_KEYS = ("pop7", "pop6", "pop5")


# ---------------------------------------------------------------- xlsx 读取
def find_header(rows):
    """定位表头行：含 CODE 列且含名称/人口列的行。"""
    for i, r in enumerate(rows):
        cells = [str(c).strip().upper() if c is not None else "" for c in r]
        if "CODE" in cells and any("常住人口" in c for c in cells):
            return i, r
    raise SystemExit("错误：找不到表头行（需包含 CODE 与 ××人普常住人口 列）")


def col_index(header, names, required=True):
    """按多个候选名（不区分大小写）查找列号。"""
    up = [str(c).strip().upper() if c is not None else "" for c in header]
    for n in names:
        if n.upper() in up:
            return up.index(n.upper())
    return None if not required else (_ for _ in ()).throw(
        SystemExit("错误：表头中找不到列 %s" % names))


def col_index_fuzzy(header, census_num):
    """模糊匹配人口列：列名同时含"七/六/五"与"常住人口"。"""
    for i, c in enumerate(header):
        if c and census_num in str(c) and "常住人口" in str(c):
            return i
    raise SystemExit("错误：表头中找不到 %s人普常住人口 列" % census_num)


def read_xlsx():
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]  # 取第一个 sheet
    rows = list(ws.iter_rows(values_only=True))
    h, header = find_header(rows)

    c_code = col_index(header, ["CODE"])
    c_town = col_index(header, ["TOWN"])
    c_name = col_index(header, ["New_Name"], required=False) \
        or col_index(header, ["行政区名称"], required=False) or c_town
    c_city = col_index(header, ["CITY"], required=False)
    pop_cols = {k: col_index_fuzzy(header, n) for k, n in
                zip(POP_KEYS, ("七", "六", "五"))}

    data = {}
    for r in rows[h + 1:]:
        if r[c_code] is None:
            continue
        code = str(r[c_code]).split(".")[0].split(",")[0].strip().zfill(9)
        data[code] = {
            "name": r[c_name] or r[c_town],
            "city": r[c_city] if c_city is not None else None,
            "pop": {k: (r[c] if isinstance(r[c], (int, float)) else None)
                    for k, c in pop_cols.items()},
        }
    return data


# ---------------------------------------------------------------- data.js 读写
def load_datajs():
    with open(DATAJS_PATH, encoding="utf-8") as f:
        txt = f.read().strip()
    if not txt.startswith("window.HN_DATA="):
        raise SystemExit("错误：data.js 不是预期的 window.HN_DATA=... 格式")
    return json.loads(txt[len("window.HN_DATA="):].rstrip(";"))


def save_datajs(D):
    out = "window.HN_DATA=" + json.dumps(D, ensure_ascii=False,
                                         separators=(",", ":")) + ";"
    with open(DATAJS_PATH, "w", encoding="utf-8") as f:
        f.write(out)


# ---------------------------------------------------------------- 同步
def main():
    if not os.path.exists(XLSX_PATH):
        raise SystemExit("错误：找不到 %s" % XLSX_PATH)
    xlsx = read_xlsx()
    print("xlsx 乡镇数:", len(xlsx))

    D = load_datajs()
    renamed, popchg, missing = [], [], []

    for t in D["towns"]:
        code = t.get("code")
        if code not in xlsx:
            missing.append((code, t["town"]))
            continue
        rec = xlsx[code]
        # 地名
        if rec["name"] and rec["name"] != t["town"]:
            renamed.append((code, t["town"], "->", rec["name"]))
            t["town"] = rec["name"]
        # 人口
        for k in POP_KEYS:
            v = rec["pop"][k]
            if v is not None and t.get(k) != v:
                popchg.append((code, t["town"], k, t.get(k), "->", v))
                t[k] = v

    # 市级汇总
    city_sum = {}
    for t in D["towns"]:
        s = city_sum.setdefault(t["city"], dict.fromkeys(POP_KEYS, 0))
        for k in POP_KEYS:
            s[k] += t.get(k) or 0
    for city in D["cities"]:
        city["pop7"], city["pop6"], city["pop5"] = \
            city_sum.get(city["name"], dict.fromkeys(POP_KEYS, 0)).values()

    # 报告
    print("地名变更:", len(renamed))
    for x in renamed:
        print("  ", *x)
    print("人口变更:", len(popchg))
    for x in popchg:
        print("  ", *x)
    if missing:
        print("警告：data.js 中 %d 个乡镇在 xlsx 找不到:" % len(missing))
        for x in missing:
            print("  ", x)
    unused = set(xlsx) - {t.get("code") for t in D["towns"]}
    if unused:
        print("提示：xlsx 中 %d 条记录无对应地图面（已忽略）: %s" % (len(unused), sorted(unused)))

    if not renamed and not popchg and not missing:
        print("✅ 数据已是最新，data.js 未改动")
        return

    save_datajs(D)
    print("💾 data.js 已更新（%d towns / %d cities）" % (len(D["towns"]), len(D["cities"])))


if __name__ == "__main__":
    main()
