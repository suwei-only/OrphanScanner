#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 OrphanScanner 应用图标(蓝色盾牌 + 白色对勾)"""
from PIL import Image, ImageDraw

SIZE = 256


def rounded_bg(d: ImageDraw.ImageDraw):
    """蓝色渐变圆角方块背景"""
    top, bottom = (13, 71, 161), (66, 165, 245)   # #0D47A1 -> #42A5F5
    for y in range(SIZE):
        t = y / SIZE
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (SIZE, y)], fill=c)
    # 圆角遮罩(画一个带透明角的图)
    mask = Image.new("L", (SIZE, SIZE), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=48, fill=255)
    return mask


def main():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    mask = rounded_bg(d)

    # 盾牌(白色)
    cx = SIZE / 2
    shield = [(cx, 28), (cx - 78, 52), (cx - 78, 122),
              (cx - 78, 150), (cx, 218), (cx + 78, 150),
              (cx + 78, 122), (cx + 78, 52)]
    d.polygon(shield, fill=(255, 255, 255, 255))
    # 盾牌描边
    d.line(shield + [shield[0]], fill=(13, 71, 161, 255), width=6,
           joint="curve")

    # 放大镜(蓝色):扫描语义
    lx, ly, r = 128, 112, 46
    d.ellipse([lx - r, ly - r, lx + r, ly + r],
              outline=(21, 101, 192, 255), width=10)
    # 镜内浅蓝
    d.ellipse([lx - r + 8, ly - r + 8, lx + r - 8, ly + r - 8],
              fill=(227, 242, 253, 255))
    # 手柄
    d.line([lx + 34, ly + 34, lx + 78, ly + 78],
           fill=(21, 101, 192, 255), width=14)
    # 镜内对勾(绿):干净
    d.line([lx - 20, ly + 2, lx - 4, ly + 18], fill=(46, 125, 50, 255),
           width=9)
    d.line([lx - 4, ly + 18, lx + 24, ly - 14], fill=(46, 125, 50, 255),
           width=9)

    # 应用遮罩
    img.putalpha(mask)
    # 多尺寸 ico
    img.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32),
                                (48, 48), (64, 64), (128, 128),
                                (256, 256)])
    # 同时输出 png 预览
    img.save("icon.png")
    print("icon.ico / icon.png 已生成")


if __name__ == "__main__":
    main()
