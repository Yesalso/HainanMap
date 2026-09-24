import geopandas as gpd
import pandas as pd

SRC = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_town.shp"
XLSX = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_Code.xlsx"
OUT = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_town_codefix.shp"

g = gpd.read_file(SRC, encoding="utf-8")
x = pd.read_excel(XLSX)
x["CODE"] = x["CODE"].astype(str)

mapping = dict(zip(zip(x["CITY"], x["TOWN"]), x["CODE"]))

new_codes, missing = [], []
for _, r in g.iterrows():
    code = mapping.get((r["CITY"], r["TOWN"]))
    if code is None:
        missing.append((r["CITY"], r["TOWN"]))
        code = r["CODE"]
    new_codes.append(code)

g["CODE"] = new_codes
g.to_file(OUT, encoding="utf-8")

print(f"更新 {len(g) - len(missing)} 条，未匹配 {len(missing)} 条: {missing}")
print("已保存:", OUT)