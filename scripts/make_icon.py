"""Deterministic flat vector-style icon. Pillow is already a pinned dependency."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent / "assets"


def main():
    ROOT.mkdir(exist_ok=True)
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
<rect x="8" y="8" width="240" height="240" rx="58" fill="#6256D9"/>
<g fill="#FFFFFF"><rect x="58" y="95" width="28" height="66" rx="14"/>
<rect x="114" y="57" width="28" height="142" rx="14"/>
<rect x="170" y="78" width="28" height="100" rx="14"/></g></svg>
'''
    (ROOT / "app.svg").write_text(svg, encoding="utf-8")
    image = Image.new("RGBA", (1024, 1024))
    draw = ImageDraw.Draw(image)
    def rounded(box, radius, fill):
        draw.rounded_rectangle(tuple(v * 4 for v in box), radius=radius * 4, fill=fill)
    rounded((8, 8, 248, 248), 58, "#6256D9")
    for box in [(58, 95, 86, 161), (114, 57, 142, 199), (170, 78, 198, 178)]:
        rounded(box, 14, "white")
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(ROOT / "app.png")
    image.save(ROOT / "app.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


if __name__ == "__main__":
    main()
