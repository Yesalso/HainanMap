# -*- coding: utf-8 -*-
"""调试：resolve_partition 之后各步骤的耗时/规模"""
import time
import geopandas as gpd
import numpy as np
import importlib.util

spec = importlib.util.spec_from_file_location('fs', 'fix_slivers.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
T0 = time.time()


def P(*a):
    print(round(time.time() - T0, 1), *a)


g = gpd.read_file('三亚乡镇2002_123.shp')
d = g.dissolve(by='乡镇码').reset_index()
labels = list(d['乡镇码'].astype(str))
ref = gpd.read_file('../海南村界.shp', encoding='gbk').to_crs('EPSG:32649')
ref['XZQDM'] = ref['XZQDM'].astype(str).str[:9]
ref = ref[ref['XZQDM'].str.startswith('4602')]
ref_t = ref.copy()
ref_t['乡镇码'] = ref_t['XZQDM']
ref_t = ref_t.dissolve(by='乡镇码').reset_index()[['乡镇码', 'geometry']]
domain = m.union_all(list(ref_t.geometry))
orig = [m.precision_normalize(m.repair_validity(x), 0.001) for x in d.geometry]
ref_geoms = [m.union_all(list(ref_t[ref_t['乡镇码'] == c].geometry)) for c in labels]
geoms = m.conflate_to_reference(list(orig), ref_geoms, 45.0)
P('conflate ok')

geoms, ov, log, rest, rc = m.resolve_partition(
    geoms, domain, labels, orig, ref_geoms, 30.0, 60.0, grid=0.001, min_hole_area=1.0)
P('resolve ok ov=', round(ov, 2), 'rest=', round(rest, 2), rc)

nv = lambda gm: sum(len(p.exterior.coords) + sum(len(r.coords) for r in p.interiors)
                    for p in m.parts_of(gm))
for i, x in enumerate(geoms):
    P(f'  out{i} verts={nv(x)} nparts={len(m.parts_of(x))} '
      f'nholes={sum(len(p.interiors) for p in m.parts_of(x))} area={x.area/1e6:.4f} '
      f'valid={x.is_valid}')

P('union_all start')
cov = m.union_all(geoms)
P('union_all ok, parts=', len(m.parts_of(cov)),
  'holes=', sum(len(p.interiors) for p in m.parts_of(cov)))

gdf_c = d.copy()
gdf_c['geometry'] = geoms
P('per-feature symdiff start')
a = gdf_c.dissolve(by='乡镇码').geometry
b = ref_t.dissolve(by='乡镇码').geometry
tot = 0.0
for k in a.index:
    P('   k', k)
    if k in b.index:
        s = a[k].symmetric_difference(b[k])
        tot += s.area
        P('     ok', k, round(s.area / 1e6, 5))
P('symdiff total km2 =', round(tot / 1e6, 6))
P('verify start')
v = m.verify(gdf_c, ref_t, '乡镇码', '乡镇码', domain)
P('verify ok')
P(v)
