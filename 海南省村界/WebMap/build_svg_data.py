# -*- coding: utf-8 -*-
"""Build data for SVG map: town polygons (simplified, WGS84) + city-level merged polygons (union)."""
import json, math
import shapefile
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.strtree import STRtree
from pyproj import CRS, Transformer

# CGCS2000 Albers: central meridian 105, std parallels 25/47, GRS80 ellipsoid
CRS_AEA = CRS.from_proj4("+proj=aea +lat_1=25 +lat_2=47 +lat_0=0 +lon_0=105 "
                         "+x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs")

SRC = r"D:/Windows/Documents/海南省村界/海南省村界/JSON/Hainan_town_topo_v3.shp"
OUT = r"D:/Windows/Documents/海南省村界/海南省村界/JSON/data.js"
TOL = 40.0

# 市县重划：海口市仅保留以下乡镇，其余划归琼山县
# 注意：同一份名单也用于 summarize_cities.py 的「市县乡镇汇总」统计，
#       两处口径必须保持一致（改动请同步）。
HAIKOU_KEEP = {
    "西秀镇", "长流镇", "海秀镇", "城西镇", "海秀街道", "秀英街道", "人民路街道",
    "海甸街道", "新埠街道", "金宇街道", "中山街道", "滨海街道", "金贸街道",
    "大同街道", "海垦街道", "国兴街道", "海府街道", "博爱街道", "白龙街道",
    "蓝天街道", "和平南街道", "白沙街道", "新海乡",
}

def signed_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0

def dp_simplify(pts, tol):
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
    if out and out[0] == out[-1]:
        out = out[:-1]
    return out

def point_in_ring(x, y, ring):
    inside = False
    n = len(ring); j = n - 1
    for i in range(n):
        xi, yi = ring[i]; xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def dedup_ring(ring, eps=30.0, max_area=100000.0):
    """切除零/近零面积回溯段(尖刺/悬挂边)。
    条件：环上某点重访距已有点 < eps，且夹住的回溯段面积 < max_area（双条件），
    避免误切合法的窄长地块。坐标单位与 eps 一致（Albers 米）。"""
    ring = list(ring)
    if len(ring) > 1 and math.hypot(ring[0][0] - ring[-1][0], ring[0][1] - ring[-1][1]) <= eps:
        ring = ring[:-1]
    while True:
        grid = {}
        banned = set()
        cut = None
        for i, p in enumerate(ring):
            kx, ky = int(p[0] // eps), int(p[1] // eps)
            found = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((kx + dx, ky + dy), ()):
                        if i - j <= 1 or (j, i) in banned:
                            continue
                        if math.hypot(p[0] - ring[j][0], p[1] - ring[j][1]) <= eps:
                            found = j
                            break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is not None:
                if abs(signed_area(ring[found:i + 1])) <= max_area:
                    cut = (found, i)
                    break
                banned.add((found, i))
            grid.setdefault((kx, ky), []).append(i)
        if cut is None:
            break
        ring = ring[:cut[0] + 1] + ring[cut[1] + 1:]
    return ring

def centroid_of(ring):
    a = cxx = cyy = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]; x2, y2 = ring[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross; cxx += (x1 + x2) * cross; cyy += (y1 + y2) * cross
    a *= 0.5
    if abs(a) < 1e-12:
        xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return cxx / (6 * a), cyy / (6 * a)

tr_fwd = Transformer.from_crs(CRS_AEA, CRS("EPSG:4326"), always_xy=True)
tr_inv = Transformer.from_crs(CRS("EPSG:4326"), CRS_AEA, always_xy=True)

def to_lonlat(ring):
    xs, ys = tr_fwd.transform([p[0] for p in ring], [p[1] for p in ring])
    return [[round(a, 5), round(b, 5)] for a, b in zip(xs, ys)]

def main():
    r = shapefile.Reader(SRC, encoding="utf-8")
    fields = [f[0] for f in r.fields[1:]]

    towns = []
    city_town_polys = {}   # city -> list of shapely polys (Albers)
    orig_geoms = []        # 原始未抽稀几何（用于邻接判断）
    orig_city = []
    for srec in r.iterShapeRecords():
        shp, rec = srec.shape, srec.record
        attrs = dict(zip(fields, rec))
        city = attrs.get("CITY", "")
        if city == "海口市" and attrs.get("TOWN", "") not in HAIKOU_KEEP:
            city = "琼山县"
        parts = list(shp.parts) + [len(shp.points)]
        rings = []
        for i in range(len(parts) - 1):
            ring = shp.points[parts[i]:parts[i + 1]]
            if len(ring) < 3:
                continue
            ring = dp_simplify(ring, TOL)
            if len(ring) < 3:
                continue
            ring = dedup_ring(ring)
            if len(ring) < 3 or abs(signed_area(ring)) < 1000:   # 退化碎片环
                continue
            rings.append(ring)
        if not rings:
            continue
        outers = [rg for rg in rings if signed_area(rg) < 0]
        holes = [rg for rg in rings if signed_area(rg) >= 0]
        if not outers:
            outers, holes = rings, []
        # build polygon parts: exterior + its holes
        poly_parts = []   # list of (outer_ring, [hole rings]) in Albers
        for o in outers:
            mine, rest = [], []
            for h in holes:
                (mine if point_in_ring(h[0][0], h[0][1], o) else rest).append(h)
            holes = rest
            poly_parts.append((o, mine))
        # shapely geom (Albers) for city union
        sp_polys = [Polygon(o, hs) for o, hs in poly_parts]
        city_town_polys.setdefault(city, []).extend(sp_polys)
        # 原始未抽稀几何，用于精确邻接判断
        raw_parts = []
        for i in range(len(parts) - 1):
            rr = shp.points[parts[i]:parts[i + 1]]
            if len(rr) >= 3:
                raw_parts.append(rr)
        raw_outers = [rg for rg in raw_parts if signed_area(rg) < 0]
        raw_holes = [rg for rg in raw_parts if signed_area(rg) >= 0]
        if not raw_outers:
            raw_outers, raw_holes = raw_parts, []
        sp_orig = []
        for ro in raw_outers:
            rhs = [rh for rh in raw_holes if point_in_ring(rh[0][0], rh[0][1], ro)]
            p = Polygon(ro, rhs)
            if p.is_valid:
                sp_orig.append(p)
        orig_geoms.extend(sp_orig)
        orig_city.extend([city] * len(sp_orig))
        # WGS84 rings for SVG
        geo_polys = [[[to_lonlat(o), *[to_lonlat(h) for h in hs]]] for o, hs in poly_parts]
        # label point: centroid of largest exterior
        big = max(poly_parts, key=lambda oh: abs(signed_area(oh[0])))[0]
        cx, cy = centroid_of(big)
        lon, lat = tr_fwd.transform(cx, cy)
        towns.append({
            "code": str(attrs.get("CODE", "")),
            "town": attrs.get("TOWN", ""),
            "city": city,
            "area": attrs.get("AREA_KM2", 0),
            "polys": geo_polys,
            "lp": [round(lon, 5), round(lat, 5)],
        })

    # city-level union
    cities = []
    for city, polys in city_town_polys.items():
        u = unary_union([p.buffer(0) for p in polys])
        if isinstance(u, Polygon):
            u = MultiPolygon([u])
        geo_polys = []
        best = None; best_a = -1
        for g in u.geoms:
            o = dedup_ring(dp_simplify(list(g.exterior.coords), TOL))
            # 过滤简化产生的细碎孔洞（< 1 km²）
            hs = [dp_simplify(list(h.coords), TOL) for h in g.interiors
                  if Polygon(h).area > 1e6]
            hs = [dedup_ring(h) for h in hs if len(h) >= 4]
            if len(o) < 4:
                continue
            gp = [[to_lonlat(o), *[to_lonlat(h) for h in hs if len(h) >= 4]]]
            geo_polys.append(gp)
            a = abs(signed_area(o))
            if a > best_a:
                best_a = a; best = o
        cx, cy = centroid_of(best)
        lon, lat = tr_fwd.transform(cx, cy)
        total_area = sum(t["area"] or 0 for t in towns if t["city"] == city)
        cities.append({
            "name": city,
            "area": round(total_area, 1),
            "towns": sum(1 for t in towns if t["city"] == city),
            "polys": geo_polys,
            "lp": [round(lon, 5), round(lat, 5)],
        })
    cities.sort(key=lambda c: -c["towns"])

    # ---- 市县邻接图（基于原始几何；共享边界 >100m 才算相邻，仅点接触不算） ----
    cname = [c["name"] for c in cities]
    cidx = {n: i for i, n in enumerate(cname)}
    tree = STRtree(orig_geoms)
    adj = [set() for _ in cities]
    seen_pairs = set()
    for i, g in enumerate(orig_geoms):
        gi = cidx[orig_city[i]]
        for j in tree.query(g.buffer(60)):
            if j <= i:
                continue
            h = orig_geoms[j]
            gj = cidx[orig_city[j]]
            if gi == gj:
                continue
            key = (min(gi, gj), max(gi, gj))
            if key in seen_pairs:
                continue
            if g.distance(h) > 60:
                continue
            inter = g.boundary.intersection(h.boundary)
            if inter.length > 100:   # 有实质共享边界
                seen_pairs.add(key)
                adj[gi].add(gj)
                adj[gj].add(gi)
    print("邻接关系:")
    for i, c in enumerate(cities):
        print(" ", c["name"], "->", sorted(cname[j] for j in adj[i]))

    # ---- 贪心图着色（6 色，度数大的先分配） ----
    N = len(cities)
    order = sorted(range(N), key=lambda i: -len(adj[i]))
    color = [-1] * N
    for i in order:
        used = {color[j] for j in adj[i] if color[j] >= 0}
        for c6 in range(6):
            if c6 not in used:
                color[i] = c6
                break
        if color[i] < 0:
            raise RuntimeError("6 色不够: " + cities[i]["name"])
    for i, c in enumerate(cities):
        c["ci"] = color[i]
    for t in towns:
        t["ci"] = color[cidx[t["city"]]]
    bad = [(cities[i]["name"], color[i]) for i in range(N)
           for j in adj[i] if color[i] == color[j]]
    print("着色冲突:", len(bad), "| 颜色分布:",
          {k: color.count(k) for k in range(6)})

    # ---- 预投影：归一化到像素坐标（Mercator，y 向下），SVG 无需大 scale ----
    PI = math.pi
    def mx(lng): return lng * PI / 180
    def my(lat): return math.log(math.tan(PI / 4 + lat * PI / 360))
    lons = [p[0] for t in towns for poly in t["polys"] for rings in poly for rg in rings for p in rg]
    lats = [p[1] for t in towns for poly in t["polys"] for rings in poly for rg in rings for p in rg]
    x0, x1 = mx(min(lons)), mx(max(lons))
    y0, y1 = my(min(lats)), my(max(lats))
    SCALE = 1000.0 / (x1 - x0)
    def to_px(pt):
        return [round((mx(pt[0]) - x0) * SCALE, 2), round((y1 - my(pt[1])) * SCALE, 2)]
    def conv(node):
        if isinstance(node[0], (int, float)):
            return to_px(node)
        return [conv(n) for n in node]
    def lp_px(lp):
        return [round((mx(lp[0]) - x0) * SCALE, 2), round((y1 - my(lp[1])) * SCALE, 2)]
    for t in towns:
        t["polys"] = conv(t["polys"])
        t["lp"] = lp_px(t["lp"])
    for c in cities:
        c["polys"] = conv(c["polys"])
        c["lp"] = lp_px(c["lp"])
    view_h = round((y1 - y0) * SCALE, 2)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.HN_DATA=")
        json.dump({"towns": towns, "cities": cities, "viewW": 1000, "viewH": view_h}, f,
                  ensure_ascii=False, separators=(",", ":"))
    print("towns:", len(towns), "cities:", len(cities), "viewH:", view_h)

if __name__ == "__main__":
    main()
