# 手绘地图 → SHP 反向映射教程（以文昌 2002 为例）

> 目标：把手绘/上色的县级地图（PNG）反向映射回地理坐标的乡镇级 SHP。
> 本文档记录文昌 2002 的完整做法，可复用于海南任意县市。
> 配套脚本与本文件同目录：`town/wenchang/`。

---

## 0. 适用场景与前提

**适用**：手绘图是"在现行 SHP 制图底图上改"的——黑线是目标年边界，**彩色块标出目标年存在、
现已并入他镇的旧乡镇**。此时可用"分区切分"法，**从机制上零缝隙**。

**前提（关键）**：手绘图必须与现行 SHP 用**同一套正向制图参数**（`Empty_map/Hainan.py`）：
- 投影 `EPSG:32649`（UTM 49N）
- 比例尺 `1 px = 20 m`
- 图幅四至 = 本县 SHP 的 `total_bounds`，无页边距

只要满足，像素↔米制就一一对应，配准偏移为 `(0,0)`，无需模板匹配。

---

## 1. 原理

1. **配准**：`x = minx + (col+0.5)·(geo_w/W)`，`y = maxy - (row+0.5)·(geo_h/H)`。
   其中 `geo_w/geo_h` 是本县 SHP 的四至宽高，`W/H` 是 PNG 像素宽高（脚本自动核对是否 = 四至/20）。
2. **切分（方案A）**：对含彩色块的现行乡镇，
   - `旧乡镇 = 彩色块 ∩ 所属母镇`
   - `其余 = 母镇 − union(旧乡镇)`
   数学上恒有 `union(其余, 旧乡镇) == 原母镇` → **无缝隙、无重叠、面积守恒**。
3. **修复**：手绘填充色块比黑线中心内缩约 1~1.5px（半像素偏移），导致旧乡镇没贴到边界、
   母镇出现等面积孔洞。修复 = 让旧乡镇向外生长到黑线中心 + 消残条。

---

## 2. 环境依赖

```powershell
python -c "import geopandas, shapely, cv2, numpy, matplotlib; print('ok')"
```
- `geopandas`、`shapely(>=2.0)`、`opencv-python(cv2)`、`numpy`、`matplotlib`
- Windows 控制台中文：运行前 `$env:PYTHONIOENCODING='utf-8'`

---

## 3. 数据准备

### 3.1 现行本县 SHP
用 `ExtractCountyShp.py` 从全岛乡镇图层裁出本县（也可用已有的县级 SHP）：

```powershell
python ExtractCountyShp.py `
  --input "..\Hainan2002\Current\Hainan_town_before_wenchang.shp" `
  --outdir . --name wenchang --code 469005
# → wenchang.shp（19 个乡镇，EPSG:32649 或 CGCS2000 Albers 均可，脚本会自动投影）
```

### 3.2 手绘上色 PNG
- 用 `Hainan.py` 生成空白底图（黑线、无标注），在其上手工描改并**用标准调色板填充色块**；
- **黑线** = 目标年乡镇边界；**彩色块** = 目标年旧乡镇（现已并入他镇者）；
- 一个颜色若有多个互不相连的色块（如文昌的 `#22B14C` 同时用于清澜镇、宝芳乡），
  在颜色表里写成**列表**，脚本按"离海岸由近到远"（或按面积）依次命名。
- 建议同时保留 `Wenchang4.png`（现行 SHP 空白渲染）便于核对配准。

### 3.3 颜色表 JSON（换县只改这个）
`color_map_wenchang.json`：
```json
{
  "city": "文昌市",
  "new_code_start": 469005117,
  "new_order": ["湖山乡","宝芳乡","清澜镇","迈号镇","南阳乡","头苑镇","龙马乡","新桥乡"],
  "multi_order": "coast",
  "colors": {
    "#00A2E8": "湖山乡",
    "#22B14C": ["清澜镇","宝芳乡"],
    "#FF7F27": "迈号镇",
    "#FFF200": "南阳乡",
    "#880015": "头苑镇",
    "#99D9EA": "龙马乡",
    "#7F7F7F": "新桥乡"
  }
}
```
- `colors`：颜色 → 名称（或名称列表）
- `multi_order`：一个颜色多连通域时的排序规则，`coast`=离海岸近者优先，`area`=面积大者优先
- `new_code_start` / `new_order`：给旧乡镇编 9 位乡镇码（从起点顺序编）

---

## 4. 操作步骤（四步）

### 步骤 1 · 反向映射
```powershell
$env:PYTHONIOENCODING='utf-8'
python reverse_by_color.py --color-map color_map_wenchang.json
# 或显式：
python reverse_by_color.py --shp wenchang.shp --img Wenchang_draw.png `
    --out wenchang2002 --color-map color_map_wenchang.json
```
产出 `wenchang2002.shp`（EPSG:32649）+ `_Albers.shp` + `_preview.png`。
此时已是严格分区（0 缝隙/0 重叠/面积守恒），但旧乡镇比黑线中心内缩 ~1px、母镇有孔洞。

### 步骤 2 · 分区修复（收紧贴合）
```powershell
python fix_partition.py --snap-m 30
```
- 把旧乡镇向外生长 `snap-m`（经验最优 **30 m ≈ 1.5px**），裁回母镇 → 贴到手绘黑线中心；
- 同一母镇内旧乡镇去重叠（大者优先）；
- **Eliminate**：把母镇残留的细条（宽度 < `max-gap-width`）按最长公共边界并入相邻旧乡镇；
- 重建 `remain = 母镇 − union(旧乡镇)`；未受影响乡镇**逐字节保持不变**。
产出 `wenchang2002_fixed.shp` + `_Albers.shp` + `_QA报告.md` + `_overlay.png` + `_sidebyside.png`。

### 步骤 3 · 校验出图
```powershell
python check_reverse.py --new wenchang2002_fixed.shp --out wenchang2002_fixed
```
输出：面积守恒、两两重叠、逐乡镇是否被拆分、**手绘黑线↔新界线贴合度**、
`_overlay.png`（红=新增 绿=沿用）、`_sidebyside.png`（左=手绘 右=新 SHP）。

### 一键运行
```powershell
.\run.bat
```
依次执行 反向映射 → 分区修复（--snap-m 30）→ 校验出图。

---

## 5. 参数速查表

| 脚本 | 参数 | 默认 | 说明 |
|---|---|---|---|
| ExtractCountyShp | `--code` / `--city` | — | 县码前缀（如 469005）或县名（如 文昌市） |
| reverse_by_color | `--px-m` | 20 | 正向制图比例尺，用于核对图幅像素数 |
| reverse_by_color | `--color-tol` | 30 | 颜色匹配容差（曼哈顿），抗锯齿留边 |
| reverse_by_color | `--min-region-px` | 1000 | 最小色块像素，滤掉噪点 |
| reverse_by_color | `--smooth-m` | 20 | 轮廓拓扑保持简化容差(m)，抹像素台阶 |
| fix_partition | `--snap-m` | **30** | 旧乡镇向外生长量(m)，贴黑线中心；经验最优 |
| fix_partition | `--max-gap-width` | 60 | 细条/缝隙最大宽度(m)，超过不视为细屑 |
| fix_partition | `--min-hole-m2` | 1 | 微孔洞阈值(m²) |
| fix_partition | `--grid` | 0 | 精度归一网格(m)，0=关闭（避免动到未受影响乡镇） |

**snap_m 怎么定**：以"旧乡镇边界↔黑线中心"贴合度为准。文昌实测：
20→0.025px、25→0.006px、**30→0.001px**、40→0.006px、50→0.106px（过冲）。
一般取 `≈1.5px`（20 m/px 时 = 30 m；30 m/px 时 = 45 m）。

---

## 6. 输出文件说明

| 文件 | 内容 |
|---|---|
| `<out>.shp` / `<out>_Albers.shp` | 结果（EPSG:32649 / 源 CRS） |
| 字段 | `CODE` 9 位码、`TOWN` 名称、`CITY` 县市、`SOURCE`=沿用/2002新增、`AREA_KM2` |
| `<out>_QA报告.md` | 修复前后诊断（内环/缝隙/重叠/域缺/细屑）+ 生长表 + 未改动乡镇 |
| `<out>_overlay.png` | 新界线叠手绘图（红=目标年新增，绿=沿用） |
| `<out>_sidebyside.png` | 左=手绘原图，右=新 SHP 渲染 |
| `<out>_preview.png` | 结果着色图（带地名） |

---

## 7. 常见问题与排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 图幅像素数 ≠ round(四至/20) | 手绘图被缩放/裁剪过 | 保持与 `Hainan.py` 同参数重新出图 |
| 某色块没被识别 | 颜色非标准调色板 / 容差太小 | 用取色器核对 HEX；调大 `--color-tol` |
| 同一颜色两块分不清谁是谁 | 需要命名顺序 | 颜色表写列表 + `multi_order`（coast/area） |
| 母镇出现等面积孔洞、旧乡镇内缩 | 半像素偏移 | 跑 `fix_partition.py`（`--snap-m 30`） |
| 修复后仍有细条/细屑 | 手绘线宽不均（>2px） | 增大 `--max-gap-width`，或略增 `--snap-m` |
| 出现大量窄条 | snap_m 过小/过大 | 按 §5 用贴合度扫描选最优 |
| 未受影响乡镇被改动 | 对全层做了精度归一/去重叠 | 只对"新增/被拆分"要素做收尾（本脚本已如此） |
| 中文乱码 | 控制台 GBK | `$env:PYTHONIOENCODING='utf-8'` |
| 写图报 `Errno 22` 中文路径 | matplotlib 限制 | 脚本已改用 `BytesIO` 写出，规避 |

**校验红线（每次必查）**：
- 面积守恒（`输入 == 输出`）
- 两两重叠 = 0、域缺口 = 0、几何全部有效
- 未受影响乡镇 `symmetric_difference().area ≈ 0`
- 内环（孔洞）数、细屑数不劣于权威层基线
- 打开 `_overlay.png` / `_sidebyside.png` 人工核对

---

## 8. 换到其他县市（快速复用）

1. 复制 `color_map_wenchang.json` → `color_map_<县>.json`，改 `city / colors / new_order / new_code_start`；
2. 准备该县的现行 SHP 与同参数手绘 PNG；
3. 依次跑：
   ```powershell
   python reverse_by_color.py --shp <县>.shp --img <县>_draw.png --out <县>2002 --color-map color_map_<县>.json
   python fix_partition.py --in <县>2002.shp --ref <县>.shp --out <县>2002_fixed --img <县>_draw.png --snap-m 30
   python check_reverse.py --shp <县>.shp --new <县>2002_fixed.shp --img <县>_draw.png --out <县>2002_fixed --color-map color_map_<县>.json
   ```
4. 若手绘不是"在现行图上改"而是**完全重绘**，用 `town/ReverseCounty2002.py`（洪泛+EDT 唯一归属），
   再 `Sanya/fix_slivers.py --mode snap-ref` 配准修复（见 `手绘地图粗糙映射空隙问题_解决方案.md`）。

---

## 9. 代码索引（可复用脚本）

| 脚本 | 作用 |
|---|---|
| `ExtractCountyShp.py` | 从全岛 SHP 裁出指定县市 |
| `color_map_wenchang.json` | 颜色→乡镇名表（换县改此文件） |
| `reverse_by_color.py` | 手绘上色 PNG → 目标年乡镇 SHP（分区切分，零缝隙） |
| `fix_partition.py` | 分区修复：贴黑线中心、消孔洞/残条、未受影响乡镇不变 |
| `check_reverse.py` | 校验 + overlay/side-by-side 出图 |
| `run.bat` | 一键跑完三步 |
| 参考 | `town/2002海口转换总结.txt`、`手绘地图粗糙映射空隙问题_解决方案.md` |
