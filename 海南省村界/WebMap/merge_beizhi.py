# -*- coding: utf-8 -*-
"""把北峙岛(shapefile 记录 TOWN='北峙岛')的几何并入昌洒镇，原位覆盖 shp。
沙箱安全：备份 → 写 __tmp 文件 → os.replace 逐份覆盖。"""
import os, shutil
import shapefile
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

SRC = r"D:/Windows/Documents/海南省村界/海南省村界/JSON/Hainan_town_topo_v3.shp"
BAK = r"D:/Windows/Documents/海南省村界/海南省村界/JSON/backup_before_beizhi_merge"
SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg")

def signed_area(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return s / 2.0

# ---- 1. 备份 ----
os.makedirs(BAK, exist_ok=True)
for ext in SIDECARS:
    src = SRC[:-4] + ext
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(BAK, os.path.basename(src)))
print("备份完成 ->", BAK)

# ---- 2. 读取并定位 ----
r = shapefile.Reader(SRC, encoding="utf-8")
fields = r.fields[1:]
fi = {f[0]: i for i, f in enumerate(fields)}
records, shapes = [], []
idx_cs = idx_bzd = None
for i, srec in enumerate(r.iterShapeRecords()):
    records.append(list(srec.record))
    shapes.append(srec.shape)
    town = str(srec.record[fi["TOWN"]])
    if town == "昌洒镇":
        idx_cs = i
    elif town == "北峙岛":
        idx_bzd = i
assert idx_cs is not None and idx_bzd is not None, "未找到昌洒镇/北峙岛记录"

def to_shapely(shp):
    parts = list(shp.parts) + [len(shp.points)]
    polys = []
    for a, b in zip(parts[:-1], parts[1:]):
        ring = shp.points[a:b]
        if len(ring) < 3:
            continue
        polys.append(Polygon(ring))
    return MultiPolygon(polys) if len(polys) > 1 else polys[0]

g_cs, g_bzd = to_shapely(shapes[idx_cs]), to_shapely(shapes[idx_bzd])
merged = unary_union([g_cs.buffer(0), g_bzd.buffer(0)])
if isinstance(merged, Polygon):
    merged = MultiPolygon([merged])
print(f"昌洒镇原 {g_cs.geom_type}({len(g_cs.geoms) if hasattr(g_cs,'geoms') else 1}块) "
      f"+ 北峙岛 {g_bzd.geom_type}({len(g_bzd.geoms)}块) -> 合并 {len(merged.geoms)} 块")

# ---- 3. 组装新 shape（外环顺时针/内环逆时针，符合 shapefile 规范） ----
pts = []
new_parts = []
for g in merged.geoms:
    ext = list(g.exterior.coords)
    if signed_area(ext) > 0:            # 外环需顺时针(有向面积为负)
        ext = ext[::-1]
    new_parts.append(len(pts))
    pts.extend(ext)
    for hole in g.interiors:
        hr = list(hole.coords)
        if signed_area(hr) < 0:         # 内环需逆时针
            hr = hr[::-1]
        new_parts.append(len(pts))
        pts.extend(hr)
new_shape = shapefile.Shape(shapeType=5, points=pts, parts=new_parts)

# ---- 4. 更新属性：面积相加，删除北峙岛记录 ----
area_idx = fi["AREA_KM2"]
new_area = round(float(records[idx_cs][area_idx] or 0) + float(records[idx_bzd][area_idx] or 0), 4)
records[idx_cs][area_idx] = new_area
print(f"昌洒镇 AREA_KM2: {records[idx_cs][area_idx]} (原 {records[idx_cs][area_idx]})")
del records[idx_bzd], shapes[idx_bzd]

# ---- 5. 写临时文件并原子覆盖 ----
tmp = SRC[:-4] + "__tmp"
w = shapefile.Writer(tmp, shapeType=5, encoding="utf-8")
for f in fields:
    w.field(f[0], f[1], f[2], f[3])
for i, (rec, shp) in enumerate(zip(records, shapes)):
    target = new_shape if i == idx_cs else shp
    w.shape(target)
    w.record(*rec)
w.close()
for ext in SIDECARS:
    src, dst = tmp + ext, SRC[:-4] + ext
    if os.path.exists(src):
        os.replace(src, dst)
print("覆盖完成")

# ---- 6. 校验 ----
r2 = shapefile.Reader(SRC, encoding="utf-8")
names = [str(rec[fi["TOWN"]]) for rec in r2.iterRecords()]
assert "北峙岛" not in names, "北峙岛仍存在"
cnt = sum(1 for n in names if n == "昌洒镇")
print(f"校验通过: 共 {len(names)} 条记录, 昌洒镇 {cnt} 条, 无北峙岛")
