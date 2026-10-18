# -*- coding: utf-8 -*-
"""Convert Hainan town shapefile (CGCS2000 Albers) -> GeoJSON (WGS84) + data.js (GCJ02 for Tencent Map)."""
import json, math
import shapefile
from pyproj import CRS, Transformer

# CGCS2000 Albers: central meridian 105, std parallels 25/47, GRS80 ellipsoid
CRS_AEA = CRS.from_proj4("+proj=aea +lat_1=25 +lat_2=47 +lat_0=0 +lon_0=105 "
                         "+x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs")

SRC = r"D:/Windows/Documents/海南省村界/海南省村界/JSON/Hainan_town_topo_v3.shp"
OUT_GEOJSON = r"D:/Windows/Documents/海南省村界/海南省村界/JSON/Hainan_towns.geojson"
OUT_DATAJS = r"D:/Windows/Documents/海南省村界/海南省村界/JSON/Hainan_towns_gcj02.js"

TOL = 40.0  # meters, simplification tolerance in Albers space

def signed_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0

def dp_simplify(pts, tol):
    """Douglas-Peucker on closed ring (iterative)."""
    n = len(pts)
    if n <= 4:
        return pts
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]; bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        dmax, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            L2 = dx * dx + dy * dy
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / L2
                t = max(0.0, min(1.0, t))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol:
            keep[idx] = True
            stack.append((a, idx)); stack.append((idx, b))
    out = [p for p, k in zip(pts, keep) if k]
    # drop duplicate closing point, keep closed implicitly
    if out and out[0] == out[-1]:
        out = out[:-1]
    return out

def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]; xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def main():
    r = shapefile.Reader(SRC, encoding="utf-8")
    fields = [f[0] for f in r.fields[1:]]
    tr = Transformer.from_crs(CRS_AEA, CRS("EPSG:4326"), always_xy=True)

    features = []
    total_in = 0; total_out = 0
    for sr in enumerate(r.iterShapeRecords()):
        idx, srec = sr
        shp, rec = srec.shape, srec.record
        attrs = dict(zip(fields, rec))
        # split parts into rings
        parts = list(shp.parts) + [len(shp.points)]
        rings = []
        for i in range(len(parts) - 1):
            ring = shp.points[parts[i]:parts[i + 1]]
            if len(ring) < 3:
                continue
            total_in += len(ring)
            ring = dp_simplify(ring, TOL)
            if len(ring) < 3:
                continue
            total_out += len(ring)
            rings.append(ring)
        if not rings:
            continue
        # shapefile: exterior CW (signed_area < 0 in projected y-up coords), holes CCW
        outers = [rg for rg in rings if signed_area(rg) < 0]
        holes = [rg for rg in rings if signed_area(rg) >= 0]
        if not outers:
            outers = rings; holes = []
        polys = []
        for o in outers:
            poly = [o]
            rest = []
            for h in holes:
                hx, hy = h[0]
                if point_in_ring(hx, hy, o):
                    poly.append(h)
                else:
                    rest.append(h)
            holes = rest
            polys.append(poly)
        # project to lon/lat, round
        def proj_ring(rg):
            lonlats = tr.transform([p[0] for p in rg], [p[1] for p in rg])
            return [[round(a, 5), round(b, 5)] for a, b in zip(lonlats[0], lonlats[1])]
        polys_proj = [[[proj_ring(rg) for rg in poly]] for poly in polys]  # MultiPolygon
        features.append({
            "type": "Feature",
            "properties": {
                "code": str(attrs.get("CODE", "")),
                "town": attrs.get("TOWN", ""),
                "city": attrs.get("CITY", ""),
                "en": attrs.get("EN", ""),
                "area": attrs.get("AREA_KM2", None),
            },
            "geometry": {"type": "MultiPolygon", "coordinates": polys_proj},
        })

    fc = {"type": "FeatureCollection", "features": features}
    with open(OUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, separators=(",", ":"))

    # GCJ-02 conversion for Tencent map
    def wgs2gcj(lng, lat):
        a = 6378245.0; ee = 0.00669342162296594323
        if lng < 72.004 or lng > 137.8347 or lat < 0.8293 or lat > 55.8271:
            return lng, lat
        def tlat(x, y):
            ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
            ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
            ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
            return ret
        def tlng(x, y):
            ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
            ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
            ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
            return ret
        dlat = tlat(lng - 105.0, lat - 35.0)
        dlng = tlng(lng - 105.0, lat - 35.0)
        radlat = lat / 180.0 * math.pi
        magic = math.sin(radlat); magic = 1 - ee * magic * magic
        sqrtmagic = math.sqrt(magic)
        dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * math.pi)
        dlng = (dlng * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
        return lng + dlng, lat + dlat

    def conv_coords(node):
        if isinstance(node[0], (int, float)):
            return [round(wgs2gcj(node[0], node[1])[0], 5), round(wgs2gcj(node[0], node[1])[1], 5)]
        return [conv_coords(n) for n in node]

    towns = []
    for ft in features:
        towns.append({
            "code": ft["properties"]["code"],
            "town": ft["properties"]["town"],
            "city": ft["properties"]["city"],
            "area": ft["properties"]["area"],
            "polys": conv_coords(ft["geometry"]["coordinates"]),
        })
    with open(OUT_DATAJS, "w", encoding="utf-8") as f:
        f.write("window.HN_TOWNS=")
        json.dump(towns, f, ensure_ascii=False, separators=(",", ":"))

    cities = sorted({ft["properties"]["city"] for ft in features})
    print("features:", len(features))
    print("points:", total_in, "->", total_out, "(%.1f%%)" % (100.0 * total_out / total_in))
    print("cities(%d):" % len(cities), ",".join(cities))

if __name__ == "__main__":
    main()
