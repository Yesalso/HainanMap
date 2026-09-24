# 三亚城芯6乡镇整体重划 QA 报告

- 输入：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\三亚\sanya.shp`
- 手绘图：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\三亚\Sanya1.png`（4535×2586，1px=20m）
- 输出：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\三亚\sanya_fixed.shp`
- 坐标系：源 `PROJCS["CGCS_2000_Albers",GEOGCS["China Geodetic Coordinate System 2000",DATUM["China_2000",SPHEROID["CGCS2000",6378137,298.257222101],AUTHORITY["EPSG","1043"]],PRIMEM["Greenwich",0],UNIT["Degree",0.0174532925199433]],PROJECTION["Albers_Conic_Equal_Area"],PARAMETER["latitude_of_center",0],PARAMETER["longitude_of_center",105],PARAMETER["standard_parallel_1",25],PARAMETER["standard_parallel_2",47],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH]]`
- 整体域：荔枝沟镇、红沙镇、河东区、河西区、南海街道、田独镇（U=387.840 km²）
- 参数：color_tol=30，smooth_close=20m，smooth_open=20m，curve_tol=25m，curve_iters=3，min_hole=1 m²
- 生成时间：2026-09-24 18:45:12

## 1. 分区校验（9 区域对 U）

```
domain_U_km2: 387.839861
children_sum_km2: 387.839784
children_union_km2: 387.839784
overlap_m2: 0.0000
domain_missing_m2: 76.8509
invalid: 0
parts: 23
```

## 2. 面积守恒（整县）

```
input_km2: 1920.571153
output_km2: 1920.571076
diff_m2: -76.8512
```

## 3. 9 个新区域

| CODE | TOWN | 面积km² | 部件数 |
|---|---|---:|---:|
| SY18 | 牛岭乡 | 123.1771 | 1 |
| SY16 | 河西区 | 11.9613 | 2 |
| SY19 | 六道乡 | 9.4670 | 2 |
| SY15 | 南海区 | 1.2879 | 2 |
| SY17 | 鹿回头区 | 12.1423 | 6 |
| SY08 | 荔枝沟区 | 12.4286 | 1 |
| SY13 | 红沙区 | 24.4093 | 1 |
| SY14 | 河东区 | 15.5230 | 1 |
| SY03 | 田独镇 | 177.4431 | 7 |

## 4. 未受影响乡镇（逐字节不变）

- 数量：10/10

- 输出要素总数：19（沿用 10，手绘调整 9）
