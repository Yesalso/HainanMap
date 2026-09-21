@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
echo === 1/3 反向映射：手绘图 -> 2002 乡镇 SHP ===
python reverse_by_color.py --color-map color_map_wenchang.json
if errorlevel 1 goto :end
echo.
echo === 2/3 分区修复：贴黑线中心 / 消孔洞 / 消碎屑 ===
python fix_partition.py --snap-m 30
if errorlevel 1 goto :end
echo.
echo === 3/3 校验 + 出图 ===
python check_reverse.py --new wenchang2002_fixed.shp --out wenchang2002_fixed
:end
echo.
pause
