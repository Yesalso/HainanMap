# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from scipy import ndimage
FP = 'C:/Users/Windows/AppData/Local/Microsoft/Windows/Fonts/mingliu.TTF'
txt = '海甸街道'  # 14px 最恶劣案例
rows = []

def add(name, mask):
    rows.append((name, mask))

# a) 原生 14px 直接渲染 + 阈值 128
f = ImageFont.truetype(FP, 14)
im = Image.new('L', (80, 30), 255); ImageDraw.Draw(im).text((40, 15), txt, font=f, fill=0, anchor='mm')
add('native14 t128', np.asarray(im) < 128)

# b) 4x + LANCZOS + 不同阈值
f4 = ImageFont.truetype(FP, 56)
im4 = Image.new('L', (320, 120), 255); ImageDraw.Draw(im4).text((160, 60), txt, font=f4, fill=0, anchor='mm')
sm = np.asarray(im4.resize((80, 30), Image.LANCZOS))
for t in (128, 150, 170, 200):
    add(f'4x t{t}', sm < t)

# c) 4x + t170 + 膨胀1
add('4x t170+dil1', ndimage.binary_dilation(sm < 170))

# d) 4x 渲染时加 1px 描边加粗 + t170
im4b = Image.new('L', (320, 120), 255)
ImageDraw.Draw(im4b).text((160, 60), txt, font=f4, fill=0, anchor='mm', stroke_width=1, stroke_fill=0)
sm2 = np.asarray(im4b.resize((80, 30), Image.LANCZOS))
add('4x stroke1 t170', sm2 < 170)

# e) 2x + LANCZOS + t170
f2 = ImageFont.truetype(FP, 28)
im2 = Image.new('L', (160, 60), 255); ImageDraw.Draw(im2).text((80, 30), txt, font=f2, fill=0, anchor='mm')
add('2x t170', np.asarray(im2.resize((80, 30), Image.LANCZOS)) < 170)

Z = 6
W = 100
canvas = np.full((len(rows) * 36 + 10, W * Z), 255, np.uint8)
y = 10
for name, m in rows:
    canvas[y:y+30, 10:10+80][m] = 0
    y += 36
Image.fromarray(canvas).save('D:/Windows/Documents/海南省村界/海南省村界/2002/Final/diag_font_exp.png')
print('rows:', [n for n, _ in rows])
