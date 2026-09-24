# 分区修复 QA 报告

- 输入：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\屯昌\屯昌2002.shp`
- 输出：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\屯昌\屯昌2002_fixed.shp`
- 权威参考：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\屯昌\屯昌_utf8.shp`
- 坐标系：`EPSG:32649`
- 参数：snap_m=30 m，max_gap_width=60 m，min_hole=1 m²，min_part=0 m²，grid=0 m
- 生成时间：2026-09-24 15:04:25

## 1. 修复前诊断

```
n: 13
invalid: 0
interior_rings: 5
sum_area: 1223310262.253431
union_area: 1223310262.2534304
overlap: 0.0
domain_missing: 0.0
gap_cnt: 0
gap_area: 0
thin: 1
```

## 2. 修复后校验

```
n: 13
invalid: 0
interior_rings: 0
sum_area: 1223310262.2534318
union_area: 1223310262.2534306
overlap: 3.832983860355382e-07
domain_missing: 2.3398145206077192e-07
gap_cnt: 0
gap_area: 0
thin: 0
```

## 3. 旧乡镇生长（贴到黑线中心）

| 旧乡镇 | 母镇 | 生长前km² | 最终km² | 增量km² |
|---|---|---:|---:|---:|
| 大同乡 | 屯城镇 | 90.2662 | 91.8954 | 1.6292 |
| 枫木乡 | 枫木镇 | 92.9686 | 94.5264 | 1.5578 |
| 藤寨乡 | 南坤镇 | 60.3062 | 61.6418 | 1.3356 |
| 南岭乡 | 南坤镇 | 58.1048 | 59.8882 | 1.7834 |
| 中建乡 | 坡心镇 | 104.9261 | 106.7289 | 1.8028 |

## 4. 未受影响乡镇

- 逐字节不变：4 个
- 被拆分：屯城镇（91.90 km²）, 枫木镇（94.53 km²）, 南坤镇（121.53 km²）, 坡心镇（106.73 km²）

- 要素总数：13（沿用 8，2002新增 5）
- 面积：输入 1223.310262 km² → 输出 1223.310262 km²
