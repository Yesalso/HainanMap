"""
从 Danzhou1.png 中提取颜色 #000000，其余像素全部透明，
输出一张透明底色的 PNG 地图；抠出的像素填充为 #000000。
"""
from PIL import Image
import numpy as np

# ============ 参数设置 ============
SRC = r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\Danzhou1.png"
DST = r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\Danzhou1_black.png"

TARGET = (0x00, 0x00, 0x00)   # 要匹配的颜色 #000000
FILL = (0x00, 0x00, 0x00)     # 抠出后填充的颜色 #000000
TOL = 120                     # 颜色容差；扫描图线条为抗锯齿灰阶，取 120 可得到连续黑线


def extract_color(src, dst, target, fill, tol=0):
    # 读取图片并统一转为 RGBA（兼容调色板 / 灰度 / RGB 等模式）
    img = Image.open(src).convert("RGBA")
    arr = np.array(img)                       # 形状: (H, W, 4)

    # 拆出 RGB 三个通道（忽略原图 alpha，因为底图通常是不透明的白底）
    rgb = arr[..., :3].astype(np.int16)

    # 生成掩膜：与目标色的各通道差值都在容差范围内
    diff = np.abs(rgb - np.array(target, dtype=np.int16))
    mask = np.all(diff <= tol, axis=-1)       # 形状: (H, W)

    # 新建全透明画布
    out = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)

    # 仅把命中像素涂成填充色且不透明
    out[mask] = (fill[0], fill[1], fill[2], 255)

    # 保存透明 PNG
    Image.fromarray(out, mode="RGBA").save(dst, "PNG")
    print(f"命中像素: {int(mask.sum())} / {mask.size}")
    print(f"已保存: {dst}")


if __name__ == "__main__":
    extract_color(SRC, DST, TARGET, FILL, TOL)
