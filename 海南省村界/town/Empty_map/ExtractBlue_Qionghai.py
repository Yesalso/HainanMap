"""
从 Danzhou.png 中提取颜色 #3F48CC，转为 #000000，其余像素全部透明，
输出一张透明底色的 PNG 地图。
"""
from PIL import Image
import numpy as np

# ============ 参数设置 ============
SRC = r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\Qionghai - 副本.png"
DST = r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\Qionghai_blue.png"

TARGET = (0x3F, 0x48, 0xCC)   # 要匹配的颜色 #3F48CC
FILL = (0x00, 0x00, 0x00)     # 抠出后填充的颜色 #000000
TOL = 0                       # 颜色容差，0 表示完全相等；如边缘有抗锯齿可设为 10~30


def extract_color(src, dst, target, fill, tol=0):
    img = Image.open(src).convert("RGBA")
    arr = np.array(img)

    rgb = arr[..., :3].astype(np.int16)
    diff = np.abs(rgb - np.array(target, dtype=np.int16))
    mask = np.all(diff <= tol, axis=-1)

    out = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    out[mask] = (fill[0], fill[1], fill[2], 255)

    Image.fromarray(out, mode="RGBA").save(dst, "PNG")
    print(f"命中像素: {int(mask.sum())} / {mask.size}")
    print(f"已保存: {dst}")


if __name__ == "__main__":
    extract_color(SRC, DST, TARGET, FILL, TOL)