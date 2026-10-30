"""Small, local vector drawings for the paper-and-headphones interface.

Render both palettes once; CTkImage handles appearance changes and DPI scaling.
No downloaded artwork, fonts, animation timers or network access are needed.
"""
from functools import lru_cache

import customtkinter as ctk
from PIL import Image, ImageDraw


def _drawing(width, height):
    scale = 4
    image = Image.new("RGBA", (width * scale, height * scale))
    draw = ImageDraw.Draw(image)

    def line(points, color, width=1.8):
        draw.line([(round(x * scale), round(y * scale)) for x, y in points],
                  fill=color, width=round(width * scale), joint="curve")

    def rounded(box, radius, fill, outline=None, width=1.8):
        draw.rounded_rectangle([round(value * scale) for value in box], round(radius * scale),
                               fill=fill, outline=outline, width=round(width * scale))

    return image, draw, scale, line, rounded


@lru_cache(maxsize=64)
def icon(name, colors, size=18):
    images = []
    for color in colors:
        image, draw, scale, line, rounded = _drawing(24, 24)
        if name == "read":
            rounded((5, 3, 19, 21), 3, None, color)
            line([(9, 8), (15, 8)], color)
            line([(9, 12), (15, 12)], color)
            line([(9, 16), (12, 16)], color)
        elif name == "headphones":
            draw.arc((4 * scale, 3 * scale, 20 * scale, 21 * scale),
                     180, 360, fill=color, width=2 * scale)
            rounded((3, 11, 7, 19), 2, color)
            rounded((17, 11, 21, 19), 2, color)
            line([(10, 12), (10, 16)], color)
            line([(14, 10), (14, 18)], color)
        elif name == "keys":
            rounded((2, 5, 22, 19), 3, None, color)
            for x in (6, 10, 14, 18):
                line([(x, 9), (x, 10)], color)
            line([(6, 14), (7, 14)], color)
            line([(10, 14), (17, 14)], color)
        elif name == "settings":
            for y, x in ((6, 9), (12, 16), (18, 7)):
                line([(3, y), (21, y)], color)
                rounded((x - 2, y - 2, x + 2, y + 2), 2, color)
        elif name == "play":
            draw.polygon([(8 * scale, 4 * scale), (20 * scale, 12 * scale),
                          (8 * scale, 20 * scale)], fill=color)
        elif name == "clipboard":
            rounded((5, 5, 19, 21), 3, None, color)
            rounded((9, 3, 15, 7), 2, color)
            line([(9, 12), (15, 12)], color)
            line([(9, 16), (13, 16)], color)
        elif name == "export":
            line([(12, 3), (12, 15)], color)
            line([(7, 10), (12, 15), (17, 10)], color)
            line([(4, 15), (4, 20), (20, 20), (20, 15)], color)
        else:
            raise ValueError(f"Unknown interface icon: {name}")
        images.append(image)
    return ctk.CTkImage(light_image=images[0], dark_image=images[1], size=(size, size))


@lru_cache(maxsize=1)
def listening_paper():
    """An unlabelled paper-and-headphones vignette for the empty editor."""
    images = []
    for halo, paper, ink, purple, pale, mint in (
        ("#F2ECFB", "#FFFFFF", "#79638F", "#9473D2", "#E1D3F4", "#C8E6DA"),
        ("#342A47", "#30283F", "#C4AEDF", "#B99CE9", "#4A385F", "#7EBAA7"),
    ):
        image, draw, scale, line, rounded = _drawing(260, 148)
        draw.ellipse((29 * scale, 9 * scale, 232 * scale, 143 * scale), fill=halo)
        draw.ellipse((69 * scale, 129 * scale, 211 * scale, 138 * scale), fill=pale)
        draw.arc((59 * scale, 8 * scale, 201 * scale, 133 * scale),
                 180, 360, fill=purple, width=8 * scale)
        rounded((58, 67, 76, 108), 8, purple, ink)
        rounded((185, 67, 203, 108), 8, purple, ink)
        rounded((82, 31, 180, 127), 10, paper, ink)
        rounded((97, 48, 162, 61), 4, pale)
        line([(98, 74), (160, 74)], ink, 1.6)
        line([(98, 83), (142, 83)], ink, 1.6)
        for x, height in ((105, 8), (115, 17), (125, 25), (135, 13), (145, 20), (155, 8)):
            line([(x, 105 - height / 2), (x, 105 + height / 2)], purple, 3)
        rounded((210, 31, 242, 59), 8, pale, ink, 1.6)
        line([(217, 51), (235, 51)], ink, 1.4)
        line([(40, 43), (40, 58)], purple)
        line([(33, 50), (47, 50)], purple)
        draw.ellipse((215 * scale, 109 * scale, 226 * scale, 120 * scale), fill=mint)
        draw.ellipse((62 * scale, 25 * scale, 67 * scale, 30 * scale), fill=purple)
        images.append(image)
    return ctk.CTkImage(light_image=images[0], dark_image=images[1], size=(260, 148))
