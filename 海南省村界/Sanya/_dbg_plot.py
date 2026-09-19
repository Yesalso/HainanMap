# -*- coding: utf-8 -*-
"""调试出图"""
import traceback
import geopandas as gpd
import importlib.util

spec = importlib.util.spec_from_file_location('fs', 'fix_slivers.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

before = gpd.read_file('三亚乡镇2002_123.shp')
after = gpd.read_file('三亚乡镇2002_123_fixed.shp')
ref = gpd.read_file('../海南村界.shp', encoding='gbk').to_crs('EPSG:32649')
ref['XZQDM'] = ref['XZQDM'].astype(str).str[:9]
ref = ref[ref['XZQDM'].str.startswith('4602')]
ref_t = ref.copy()
ref_t['乡镇码'] = ref_t['XZQDM']
ref_t = ref_t.dissolve(by='乡镇码').reset_index()[['乡镇码', 'geometry']]

print('before', len(before), 'after', len(after), 'ref_t', len(ref_t))
rc = m.union_all(list(ref_t.geometry))
print('ref_cov', rc.geom_type, rc.area / 1e6)
b = rc.boundary
print('boundary type', b.geom_type)
print('parts_of(boundary)', len(m.parts_of(b)))
print('get_parts', len(m.get_parts(b)))
for gdf, name in ((before, 'before'), (after, 'after')):
    try:
        d = m.union_all(list(gdf.geometry)).symmetric_difference(rc)
        print(name, 'symdiff km2', round(d.area / 1e6, 4), 'parts', len(m.parts_of(d)))
    except Exception:
        print(name, 'FAILED')
        traceback.print_exc()

# 直接调用出图函数
try:
    m.plot_before_after(before, after, ref_t, 'zz_test.png')
except Exception:
    traceback.print_exc()
