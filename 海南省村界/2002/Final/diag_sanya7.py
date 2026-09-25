# -*- coding: utf-8 -*-
"""诊断7（终判）：36 处偏移簇是"真偏移节点"还是"锐角收敛假象"？
方法：对每簇取 200m 半径内各镇边界的最近顶点，算顶点间最大离散度。
      离散度 < 5m → 干净节点（假象）；30-100m → 真偏移（需修复）。
只读。
"""
import geopandas as gpd
import shapely
import numpy as np

SHP = r"D:\Windows\Documents\海南省村界\海南省村界\2002\Final\Hainan_town_topo.shp"
CLUSTERS = [
    (301714,2036588),(359594,2032466),(325119,2033347),(347804,2022645),
    (328089,2052250),(341328,2030064),(328583,2033033),(314917,2040536),
    (316089,2030795),(339051,2023614),(342945,2016191),(343593,2019223),
    (341919,2023467),(349614,2023268),(341820,2022617),(358667,2034193),
    (342003,2017010),(303012,2042330),(367234,2017674),(332766,2054808),
    (340066,2015927),(345256,2035140),(308963,2043148),(312642,2024207),
    (367502,2030400),(338836,2020991),(298027,2031934),(348865,2013732),
    (342678,2016155),(343289,2015368),(348544,2018292),(303511,2028529),
    (326216,2023126),(353335,2034171),(349056,2017333),(345665,2016389),
]

gdf = gpd.read_file(SHP, encoding="utf-8").to_crs("EPSG:32649")
gdf["geometry"] = gdf.geometry.buffer(0)
mask = gdf["CITY"].astype(str).str.contains("三亚", na=False)
sanya = gdf[mask].reset_index(drop=True)
names = sanya["TOWN"].tolist()
polys = list(sanya.geometry)

def nearest_vertices(geom, c, r=200.0):
    """几何边界上距 c 半径 r 内的全部顶点坐标数组"""
    b = geom.boundary
    pts = []
    def ring_verts(rg):
        return np.array(rg.coords)
    if hasattr(b, "geoms"):
        arrs = [ring_verts(g) for g in b.geoms]
    else:
        arrs = [ring_verts(b)]
    for a in arrs:
        d = np.hypot(a[:, 0] - c.x, a[:, 1] - c.y)
        pts.append(a[d < r])
    if not pts:
        return np.empty((0, 2))
    return np.vstack(pts)

real, clean = [], []
for cx, cy in CLUSTERS:
    c = shapely.Point(cx, cy)
    allv = []
    for i, g in enumerate(polys):
        v = nearest_vertices(g, c)
        if len(v):
            allv.append((names[i], v))
    if not allv:
        continue
    # 每个镇取离簇心最近的顶点
    nearest = []
    for nm, v in allv:
        d = np.hypot(v[:, 0] - cx, v[:, 1] - cy)
        k = int(np.argmin(d))
        nearest.append((nm, v[k], d[k]))
    pts = np.array([p for _, p, _ in nearest])
    spread = max(np.hypot(pts[:, 0] - pts[:, 0].mean() , pts[:, 1] - pts[:, 1].mean()).max()
                 for _ in [0]) if len(pts) else 0
    # 两两最大距离
    dd = 0.0
    for a in range(len(pts)):
        for bidx in range(a + 1, len(pts)):
            dd = max(dd, float(np.hypot(*(pts[a] - pts[bidx]))))
    rec = (cx, cy, [(nm, round(float(d))) for nm, _, d in nearest], round(dd))
    (real if dd > 5 else clean).append(rec)

print(f"=== 干净节点（假象，收敛角导致） {len(clean)} 处 ===")
for cx, cy, nr, dd in clean:
    print(f"  ({cx},{cy}) 最大离散 {dd}m")
print(f"\n=== 真偏移节点（需修复） {len(real)} 处 ===")
for cx, cy, nr, dd in sorted(real, key=lambda r: -r[3]):
    print(f"  ({cx},{cy}) 最大离散 {dd}m  各镇最近顶点距簇心: {nr}")
