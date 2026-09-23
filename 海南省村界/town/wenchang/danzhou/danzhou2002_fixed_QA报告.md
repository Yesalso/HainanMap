# 分区修复 QA 报告

- 输入：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\danzhou\danzhou2002.shp`
- 输出：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\danzhou\danzhou2002_fixed.shp`
- 权威参考：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\danzhou\Hainan_town_danzhou.shp`
- 坐标系：`EPSG:32649`
- 参数：snap_m=30 m，max_gap_width=60 m，min_hole=1 m²，grid=0 m
- 生成时间：2026-09-21 18:30:38

## 1. 修复前诊断

```
n: 23
invalid: 0
interior_rings: 10
sum_area: 3396513489.3073463
union_area: 3396513489.3073254
overlap: 0.0
domain_missing: 0.0
gap_cnt: 0
gap_area: 0
thin: 19
```

## 2. 修复后校验

```
n: 23
invalid: 0
interior_rings: 1
sum_area: 3396513489.307347
union_area: 3396513489.307352
overlap: 8.20389553672315e-07
domain_missing: 0.0
gap_cnt: 0
gap_area: 0
thin: 19
```

## 3. 旧乡镇生长（贴到黑线中心）

| 旧乡镇 | 母镇 | 生长前km² | 最终km² | 增量km² |
|---|---|---:|---:|---:|
| 洛基乡 | 那大镇 | 59.4179 | 61.3014 | 1.8835 |
| 富克乡 | 雅星镇 | 183.7853 | 186.5657 | 2.7805 |
| 松林乡 | 光村镇 | 65.5639 | 67.0446 | 1.4808 |
| 兰训乡 | 木棠镇 | 78.2144 | 79.7757 | 1.5614 |
| 长坡乡 | 东成镇 | 110.4452 | 112.8489 | 2.4037 |

## 4. 未受影响乡镇

- 逐字节不变：13 个
- 被拆分：那大镇（61.30 km²）, 雅星镇（186.57 km²）, 光村镇（67.04 km²）, 木棠镇（79.78 km²）, 东成镇（112.85 km²）

- 要素总数：23（沿用 18，2002新增 5）
- 面积：输入 3396.513489 km² → 输出 3396.513489 km²
