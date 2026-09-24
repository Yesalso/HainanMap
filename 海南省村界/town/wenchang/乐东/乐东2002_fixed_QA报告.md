# 分区修复 QA 报告

- 输入：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\乐东\乐东2002.shp`
- 输出：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\乐东\乐东2002_fixed.shp`
- 权威参考：`D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\乐东\乐东_corrected.shp`
- 坐标系：`EPSG:32649`
- 参数：snap_m=30 m，max_gap_width=60 m，min_hole=1 m²，min_part=0 m²，grid=0 m
- 生成时间：2026-09-24 15:38:04

## 1. 修复前诊断

```
n: 16
invalid: 0
interior_rings: 6
sum_area: 2766252676.590201
union_area: 2766252676.590193
overlap: 0.0
domain_missing: 0.0
gap_cnt: 0
gap_area: 0
thin: 1
```

## 2. 修复后校验

```
n: 16
invalid: 0
interior_rings: 0
sum_area: 2766252676.5901985
union_area: 2766252676.590186
overlap: 1.811863264280047e-07
domain_missing: 0.0
gap_cnt: 0
gap_area: 0
thin: 1
```

## 3. 旧乡镇生长（贴到黑线中心）

| 旧乡镇 | 母镇 | 生长前km² | 最终km² | 增量km² |
|---|---|---:|---:|---:|
| 山荣乡 | 抱由镇 | 163.8628 | 166.3293 | 2.4665 |
| 永明乡 | 抱由镇 | 138.2593 | 140.0741 | 1.8148 |
| 三平乡 | 万冲镇 | 123.4844 | 126.0635 | 2.5791 |
| 福报乡 | 千家镇 | 124.4684 | 126.4183 | 1.9499 |
| 峰岭乡 | 尖峰镇 | 314.9256 | 318.3369 | 3.4112 |

## 4. 未受影响乡镇

- 逐字节不变：7 个
- 被拆分：抱由镇（306.40 km²）, 万冲镇（126.06 km²）, 千家镇（126.42 km²）, 尖峰镇（318.34 km²）

- 要素总数：16（沿用 11，2002新增 5）
- 面积：输入 2766.252677 km² → 输出 2766.252677 km²
