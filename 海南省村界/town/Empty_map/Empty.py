"""
将 Haikou_20011.png 中的杂色按照容差归为纯黑色或纯白色。
原理：计算每个像素与黑色、白色的距离，
     若距离黑色在容差内 → 纯黑；
     若距离白色在容差内 → 纯白；
     否则取距离更近的颜色。
"""
from PIL import Image
import numpy as np

# ============ 参数设置 ============
SRC = r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\Danzhou - 副本 - 副本.png"
DST = r"D:\Windows\Documents\海南省村界\海南省村界\town\Empty_map\Danzhou - 副本 - 副本_bw.png"

TOL = 60          # 容差：与黑/白距离小于该值时直接归为对应纯色
# 距离度量使用欧氏距离，范围 0 ~ 441.67（对角线）


def clean_color(src, dst, tol=60):
    # 打开图片并转为 RGB（保留原始透明度的话可转 RGBA，此处按 RGB 处理）
    img = Image.open(src).convert("RGB")
    arr = np.array(img).astype(np.int32)          # 形状 (H, W, 3)

    # 纯黑和纯白的参考值
    black = np.array([0, 0, 0], dtype=np.int32)
    white = np.array([255, 255, 255], dtype=np.int32)

    # 计算每个像素到黑色、白色的欧氏距离
    d_black = np.sqrt(np.sum((arr - black) ** 2, axis=-1))
    d_white = np.sqrt(np.sum((arr - white) ** 2, axis=-1))

    # 初始化输出为原图（后续覆盖）
    out = arr.copy()

    # 1. 距离黑色在容差内 → 纯黑
    mask_black = d_black <= tol
    out[mask_black] = [0, 0, 0]

    # 2. 距离白色在容差内 → 纯白
    mask_white = d_white <= tol
    out[mask_white] = [255, 255, 255]

    # 3. 其余像素（不在容差内）→ 比较距离，取更近的纯色
    mask_other = ~(mask_black | mask_white)
    # 在 mask_other 中，如果到黑色的距离更小，则设为黑，否则设为白
    choose_black = mask_other & (d_black < d_white)
    choose_white = mask_other & (d_black >= d_white)
    out[choose_black] = [0, 0, 0]
    out[choose_white] = [255, 255, 255]

    # 保存结果
    Image.fromarray(out.astype(np.uint8), mode="RGB").save(dst, "PNG")
    print(f"处理完成，共 {arr.shape[0] * arr.shape[1]} 像素")
    print(f"直接归黑: {mask_black.sum()}，直接归白: {mask_white.sum()}")
    print(f"按最近原则归黑: {choose_black.sum()}，归白: {choose_white.sum()}")
    print(f"已保存: {dst}")


if __name__ == "__main__":
    clean_color(SRC, DST, TOL)
