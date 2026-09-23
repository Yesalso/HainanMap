"""
从 SRC_DIR 中所有图片提取颜色 #3F48CC，其余像素全部透明，
输出透明底色的 PNG 地图到 DST_DIR；抠出的像素填充为 #000000。
"""
from PIL import Image
import numpy as np
from pathlib import Path

# ============ 参数设置 ============
SRC_DIR = Path(r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\ing_blue\Trance")
DST_DIR = Path(r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\ing_blue\Trance_blue")
SUFFIX = "_blue"

TARGET = (0x3F, 0x48, 0xCC)   # 要匹配的颜色 #3F48CC
FILL = (0x00, 0x00, 0x00)     # 抠出后填充的颜色 #000000
TOL = 0                       # 颜色容差，0 表示完全相等；如边缘有抗锯齿可设为 10~30


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
    DST_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in SRC_DIR.iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"))
    if not files:
        print(f"未找到图片: {SRC_DIR}")
    for src in files:
        dst = DST_DIR / f"{src.stem}{SUFFIX}.png"
        print(f"处理: {src.name}")
        extract_color(str(src), str(dst), TARGET, FILL, TOL)
