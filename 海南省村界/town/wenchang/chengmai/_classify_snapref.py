import numpy as np
import cv2
import geopandas as gpd
import sys

BASE = r"D:\Windows\Documents\海南省村界\海南省村界\town\wenchang\chengmai"
OUT = sys.argv[1] if len(sys.argv) > 1 else "chengmai2002_final.shp"

def rasterize_lines_fill(gdf, h, w, x0, x1, y0, y1, line_thick=2):
    """边界线栅格化，返回 bool。"""
    out = np.zeros((h, w), dtype=np.uint8)
    sx, sy = w / (x1 - x0), h / (y1 - y0)
    for g in gdf.geometry:
        for p in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            for ring in (list(p.interiors) + [p.exterior]):
                pts = np.array([((x - x0) * sx, (y1 - y) * sy)
                                for x, y in ring.coords], dtype=np.int32)
                cv2.polylines(out, [pts], True, 255, thickness=line_thick)
    return out > 0

def main():
    ref = gpd.read_file(BASE + r"\Hainan_town_chengmai.shp", encoding="utf-8").to_crs(32649)
    out = gpd.read_file(BASE + "\\" + OUT, encoding="utf-8").to_crs(32649)
    img = cv2.imdecode(np.fromfile(BASE + r"\Chengmai.png", dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = ref.total_bounds

    e_out = rasterize_lines_fill(out, h, w, x0, x1, y0, y1)
    e_ref = rasterize_lines_fill(ref, h, w, x0, x1, y0, y1)
    dt_ref = cv2.distanceTransform((~e_ref).astype(np.uint8), cv2.DIST_L2, 5)
    # 与现行SHP重合(<2px)的段=蓝，其余(手绘重画)=红
    coincide = e_out & (dt_ref <= 2)
    redrawn = e_out & ~coincide
    render = img.copy()
    render[redrawn] = (40, 40, 240)
    render[coincide] = (240, 220, 0)
    out_png = BASE + "\\" + OUT[:-4] + "_分类.png"
    cv2.imencode(".png", render)[1].tofile(out_png)
    print("已输出", out_png)
    print(f"重合段(蓝) 像素: {coincide.sum()}, 重画段(红) 像素: {redrawn.sum()}")

    # 写正方形 overlay 图（透明底）
    vis = np.full((h, w, 3), 255, dtype=np.uint8)
    vis[redrawn] = (0, 0, 255)
    vis[coincide] = (0, 140, 255)
    ov = BASE + "\\" + OUT[:-4] + "_叠加.png"
    cv2.imencode(".png", vis)[1].tofile(ov)
    print("已输出", ov)

if __name__ == "__main__":
    main()