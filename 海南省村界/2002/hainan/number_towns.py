import geopandas as gpd
import pandas as pd

SRC = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_town.shp"
OUT = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_town_编号排序.xlsx"

SEAT = {  # 6位县市码 -> 县城(市区)所在乡镇名
    "469001": "通什镇", "469002": "嘉积镇", "469003": "那大镇", "469005": "文城镇",
    "469006": "万城镇", "469007": "八所镇", "469021": "定城镇", "469022": "屯城镇",
    "469023": "金江镇", "469024": "临城镇", "469025": "牙叉镇", "469026": "石碌镇",
    "469027": "抱由镇", "469028": "椰林镇", "469029": "保城镇", "469030": "营根镇",
}
EXCLUDE = {"海口市", "三亚市"}

gdf = gpd.read_file(SRC, encoding="utf-8")
gdf["CX"] = gdf.geometry.centroid.x.round(3)
gdf["CY"] = gdf.geometry.centroid.y.round(3)

rows = []
for _, r in gdf.iterrows():
    code = r["CODE"]
    prefix6 = code[:6]
    is_excluded = r["CITY"] in EXCLUDE
    if is_excluded:
        rows.append({
            "原CODE": code, "CODE": code, "TOWN": r["TOWN"], "CITY": r["CITY"],
            "EN": r["EN"], "N_FEAT": r["N_FEAT"], "AREA_KM2": r["AREA_KM2"],
            "CX": r["CX"], "CY": r["CY"], "备注": "", "排序": 0,
        })
    else:
        rows.append({
            "原CODE": code, "CODE": None, "TOWN": r["TOWN"], "CITY": r["CITY"],
            "EN": r["EN"], "N_FEAT": r["N_FEAT"], "AREA_KM2": r["AREA_KM2"],
            "CX": r["CX"], "CY": r["CY"], "备注": "", "排序": 0,
        })

df = pd.DataFrame(rows)

counties = df[~df["CITY"].isin(EXCLUDE)]
for pref, grp in counties.groupby(df["CITY"]):
    pass

# 按县市分组重新编号
new_rows = []
excluded = df[df["CITY"].isin(EXCLUDE)].to_dict("records")
excluded.sort(key=lambda x: (x["原CODE"],))
new_rows.extend(excluded)

rest = df[~df["CITY"].isin(EXCLUDE)].to_dict("records")
pref_by_city = {}
for r_ in rest:
    pref_by_city.setdefault(r_["CITY"], r_["原CODE"][:6])

for city in sorted(pref_by_city, key=lambda c: pref_by_city[c]):
    pref = pref_by_city[city]
    members = [r_ for r_ in rest if r_["CITY"] == city]
    seat = SEAT[pref]
    if seat not in {r_["TOWN"] for r_ in members}:
        print(f"[警告] {city} 未找到县城乡镇 {seat}")
    seat_rec = [r_ for r_ in members if r_["TOWN"] == seat]
    others = [r_ for r_ in members if r_["TOWN"] != seat]
    others.sort(key=lambda x: (x["CX"], x["CY"]))
    ordered = seat_rec + others
    for i, r_ in enumerate(ordered, start=1):
        r_["CODE"] = f"{pref}{i:03d}"
        r_["排序"] = i
        r_["备注"] = "县城" if r_["TOWN"] == seat else ""
        new_rows.append(r_)

out_df = pd.DataFrame(new_rows)
out_df = out_df.sort_values(["CODE"]).reset_index(drop=True)
out_df.insert(0, "序号", range(1, len(out_df) + 1))
cols = ["序号", "CODE", "原CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "CX", "CY", "备注"]
out_df = out_df[cols]

with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
    out_df.to_excel(writer, index=False, sheet_name="乡镇编号")

print(f"共 {len(out_df)} 条，已导出 {OUT}")
import os
print(os.path.getsize(OUT), "bytes")