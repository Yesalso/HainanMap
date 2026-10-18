# -*- coding: utf-8 -*-
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
src = "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/diag_label_haikou.png"
im = Image.open(src)
w, h = im.size
# 文字区放大（海口小街道两行标签）——坐标按 1px=40m 新分辨率（旧 78.4m/px 版的约 1.96 倍）
im.crop((235, 235, 630, 395)).resize((395*4, 160*4), Image.NEAREST).save(
    "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/diag_zoom_haidian.png")
# 圆圈群放大
im.crop((355, 355, 630, 570)).resize((275*4, 215*4), Image.NEAREST).save(
    "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/diag_zoom_circles.png")
# 全岛总览
full = Image.open("D:/Windows/Documents/海南省村界/海南省村界/2002/Hainan_town_county_town1_county2_labeled.png")
fw, fh = full.size
full.resize((1200, int(fh*1200/fw)), Image.LANCZOS).save(
    "D:/Windows/Documents/海南省村界/海南省村界/2002/Final/diag_overview_small.png")
print("ok", full.size)
