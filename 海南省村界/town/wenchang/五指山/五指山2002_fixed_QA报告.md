# 分区修复 QA 报告

- 输入：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\五指山\五指山2002.shp`
- 输出：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\五指山\五指山2002_fixed.shp`
- 权威参考：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\五指山\五指山_dec.shp`
- 坐标系：`EPSG:32649`
- 参数：snap_m=40 m，max_gap_width=60 m，min_hole=1 m²，min_part=0 m²，grid=0 m
- 生成时间：2026-09-23 12:48:46

## 1. 修复前诊断

```
n: 9
invalid: 0
interior_rings: 2
sum_area: 1130607502.738196
union_area: 1130607502.7381968
overlap: 0.0
domain_missing: 0.0
gap_cnt: 0
gap_area: 0
thin: 0
```

## 2. 修复后校验

```
n: 9
invalid: 0
interior_rings: 0
sum_area: 1130607502.7381954
union_area: 1130607502.738198
overlap: 2.0995101393372483e-08
domain_missing: 0.0
gap_cnt: 0
gap_area: 0
thin: 0
```

## 3. 旧乡镇生长（贴到黑线中心）

| 旧乡镇 | 母镇 | 生长前km² | 最终km² | 增量km² |
|---|---|---:|---:|---:|
| 红山乡 | 通什镇 | 84.1043 | 85.7476 | 1.6433 |
| 保国乡 | 畅好乡 | 61.6890 | 63.0206 | 1.3316 |

## 4. 未受影响乡镇

- 逐字节不变：5 个
- 被拆分：通什镇（85.75 km²）, 畅好乡（63.02 km²）

- 要素总数：9（沿用 7，2002新增 2）
- 面积：输入 1130.607503 km² → 输出 1130.607503 km²
