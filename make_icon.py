"""
Gera assets/icon.ico — o ícone do app (disco de vinil + lupa, sobre fundo índigo).

O vinil representa "música"; a lupa com o cifrão representa "buscar preço/onde
comprar" — a ideia central do app (agregador de Beatport/Bandcamp/Soulseek).

Rode:  python make_icon.py
Depois: recrie o atalho da área de trabalho (make_shortcut.ps1).
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parent / "assets"
ASSETS.mkdir(exist_ok=True)

# Paleta (mesma da interface: índigo profundo com acento âmbar de "preço/valor")
BG_TOP = (67, 56, 202)      # indigo-700
BG_BOTTOM = (49, 46, 129)   # indigo-900 (dá o efeito de profundidade no fundo)
VINYL = (17, 24, 39)        # quase preto, com leve azul
GROOVE = (55, 65, 81)       # sulcos do disco
LABEL_BG = (238, 242, 255)  # label central do vinil (quase branco)
LABEL_RING = (199, 210, 254)
SHINE = (255, 255, 255)     # brilho no disco
GLASS_RING = (251, 191, 36) # âmbar — a "lupa" de buscar preço
GLASS_GLASS = (255, 251, 235)
HANDLE = (180, 83, 9)


def vertical_gradient_bg(size, top, bottom, radius):
    """Fundo com leve degradê vertical, cantos arredondados."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGB", (1, size), (0, 0, 0))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    grad = grad.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def draw_master(size=512):
    img = vertical_gradient_bg(size, BG_TOP, BG_BOTTOM, int(size * 0.22))
    d = ImageDraw.Draw(img)

    cx, cy = size * 0.44, size * 0.46
    r = size * 0.30

    # Disco de vinil
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=VINYL)
    for frac in (0.86, 0.72, 0.58, 0.44):
        rr = r * frac
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=GROOVE,
                  width=max(2, int(size * 0.006)))

    # Label central
    lr = r * 0.30
    d.ellipse([cx - lr, cy - lr, cx + lr, cy + lr], fill=LABEL_BG, outline=LABEL_RING,
              width=max(2, int(size * 0.005)))
    hole = lr * 0.16
    d.ellipse([cx - hole, cy - hole, cx + hole, cy + hole], fill=VINYL)

    # Brilho (arco de luz no canto superior do disco)
    shine = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shine)
    sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 28))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).pieslice([cx - r, cy - r, cx + r, cy + r], 200, 300, fill=255)
    img.paste(Image.new("RGB", (size, size), SHINE), (0, 0), Image.composite(
        Image.new("L", (size, size), 60), Image.new("L", (size, size), 0), mask))

    # Lupa com cifrão — "buscar o preço", ancorada no canto inferior direito
    gx, gy = size * 0.72, size * 0.72
    gr = size * 0.20
    ring_w = max(4, int(size * 0.045))
    d.ellipse([gx - gr, gy - gr, gx + gr, gy + gr], fill=GLASS_GLASS, outline=GLASS_RING,
              width=ring_w)

    handle_len = size * 0.16
    hx0 = gx + gr * 0.72
    hy0 = gy + gr * 0.72
    hx1 = hx0 + handle_len * 0.72
    hy1 = hy0 + handle_len * 0.72
    d.line([hx0, hy0, hx1, hy1], fill=HANDLE, width=max(6, int(size * 0.05)))
    cap = max(4, int(size * 0.03))
    d.ellipse([hx1 - cap, hy1 - cap, hx1 + cap, hy1 + cap], fill=HANDLE)

    # cifrão dentro da lupa
    try:
        font = ImageFont.truetype("arialbd.ttf", int(gr * 1.3))
    except Exception:
        font = ImageFont.load_default()
    text = "$"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((gx - tw / 2 - bbox[0], gy - th / 2 - bbox[1] - gr * 0.05), text,
           font=font, fill=GLASS_RING)

    return img


def main():
    master = draw_master(512)
    png_path = ASSETS / "icon.png"
    master.save(png_path)

    ico_path = ASSETS / "icon.ico"
    master.save(
        ico_path,
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Icone gerado: {ico_path}")
    print(f"PNG gerado:   {png_path}")


if __name__ == "__main__":
    main()
