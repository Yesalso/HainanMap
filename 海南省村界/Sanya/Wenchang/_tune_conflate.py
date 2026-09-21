# -*- coding: utf-8 -*-
"""文昌配准容差敏感性：不同 conflate_tol(2r) 下"叠加不重合面积"收敛情况"""
import time
import os as _os
import geopandas as gpd
import importlib.util

HERE = _os.path.dirname(_os.path.abspath(__file__))
BASE = _os.path.dirname(HERE)

spec = importlib.util.spec_from_file_location('fs', _os.path.join(BASE, 'Sanya', 'fix_slivers.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
T0 = time.time()

g = gpd.read_file(_os.path.join(HERE, '文昌乡镇2002.shp'))
d = g.dissolve(by='乡镇码').reset_index()
labels = list(d['乡镇码'].astype(str))
ref = gpd.read_file(_os.path.join(BASE, '海南村界.shp'), encoding='gbk').to_crs('EPSG:32649')
ref['XZQDM'] = ref['XZQDM'].astype(str).str[:9]
ref = ref[ref['XZQDM'].str.startswith('469005')]
ref_t = ref.copy()
ref_t['乡镇码'] = ref_t['XZQDM']
ref_t = ref_t.dissolve(by='乡镇码').reset_index()[['乡镇码', 'geometry']]

orig = [m.precision_normalize(m.repair_validity(x), 0.001) for x in d.geometry]
ref_geoms = [m.union_all(list(ref_t[ref_t['乡镇码'] == c].geometry)) for c in labels]

before = gpd.GeoDataFrame({'乡镇码': labels}, geometry=orig, crs='EPSG:32649')
_, a0 = m.mismatch_band(before, ref_t, '乡镇码')
print(f'修复前 叠加不重合面积 = {a0/1e6:.4f} km²  (占文昌 {2457:.3f} km² 的 {a0/1e6/2457.27*100:.2f}%)', flush=True)

print(f"{'2r(m)':>8} | {'不重合面积km²':>14} | {'相对修复前':>10} | {'耗时s':>6}")
for tol2 in (30, 60, 90, 150, 240, 400, 800, 2000):
    t = time.time()
    geoms = m.conflate_to_reference(list(orig), ref_geoms, tol2 / 2.0)
    after = gpd.GeoDataFrame({'乡镇码': labels}, geometry=geoms, crs='EPSG:32649')
    _, a = m.mismatch_band(after, ref_t, '乡镇码')
    print(f"{tol2:>8} | {a/1e6:>14.4f} | {a/a0*100:>9.1f}% | {time.time()-t:>6.1f}",
          flush=True)
print(f'总耗时 {time.time()-T0:.1f}s')