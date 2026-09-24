import geopandas as gpd
import pandas as pd

SRC = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Final\Hainan_town_codefix.shp"
OUT = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_Code.xlsx"

g = gpd.read_file(SRC, encoding="utf-8")
df = g.drop(columns=["geometry"]).copy()
df["CODE"] = df["CODE"].astype(int)
df["CX"] = g.geometry.representative_point().x.round(3)
df["CY"] = g.geometry.representative_point().y.round(3)

cols = ["CODE", "TOWN", "CITY", "EN", "N_FEAT", "AREA_KM2", "CX", "CY"]
df = df[cols].sort_values("CODE").reset_index(drop=True)

with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
    df.to_excel(writer, index=False, sheet_name="Sheet1")

print(f"导入 {len(df)} 条乡镇数据，已保存:", OUT)