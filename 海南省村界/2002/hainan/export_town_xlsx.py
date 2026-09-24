import geopandas as gpd
import pandas as pd

src = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_town.shp"
out = r"D:\Windows\Documents\海南省村界\海南省村界\2002\hainan\Hainan_town.xlsx"

gdf = gpd.read_file(src, encoding="utf-8")
df = gdf.drop(columns=["geometry"]).copy()
df["CENTROID_X"] = gdf.geometry.representative_point().x.round(3)
df["CENTROID_Y"] = gdf.geometry.representative_point().y.round(3)

with pd.ExcelWriter(out, engine="openpyxl") as writer:
    df.to_excel(writer, index=False, sheet_name="乡镇地名")

print(f"共 {len(df)} 条记录，已导出到 {out}")