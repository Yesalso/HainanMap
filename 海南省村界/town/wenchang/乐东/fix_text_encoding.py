# -*- coding: utf-8 -*-
"""
修复 乐东.shp 属性表文本（双重编码乱码），生成 乐东_corrected.shp。
与白沙/东方 同理（白沙_corrected.shp / 东方_clean.shp）。
规则：
  1. 字符串先试 latin-1 编码 -> utf-8 解码（CP1252 型乱码）；
  2. 失败再试 gbk 编码 -> utf-8 解码（GBK 型乱码，如 莺歌海镇）；
  3. 仍失败的保留原值。
输出保持原 CRS（CGCS2000 Albers），编码 UTF-8。
"""
import os
import geopandas as gpd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "乐东.shp")
OUT = os.path.join(HERE, "乐东_corrected.shp")
TEXT_FIELDS = ["TOWN", "CITY", "EN"]


def fix(v):
    if not isinstance(v, str):
        return v
    for enc in ("latin-1", "gbk"):
        try:
            dec = v.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if dec != v or any("\u4e00" <= c <= "\u9fff" for c in dec):
            return dec
    return v


def main():
    g = gpd.read_file(SRC)
    for c in TEXT_FIELDS:
        if c in g.columns:
            g[c] = g[c].apply(fix)
    if os.path.exists(OUT):
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            p = OUT[:-4] + ext
            if os.path.exists(p):
                os.remove(p)
    g.to_file(OUT, encoding="utf-8")
    print("已写出：", OUT)
    print(g[["CODE", "TOWN", "CITY", "EN"]].to_string())


if __name__ == "__main__":
    main()