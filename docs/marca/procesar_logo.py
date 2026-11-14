"""Quita el fondo blanco del logo (y el hueco de la R), recorta y genera tamaños web."""
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

src, out = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGB")
a = np.asarray(im).astype(int)
minc = a.min(axis=2)
casi_blanco = minc > 232

# 1) fondo exterior: relleno desde el borde
mask = Image.fromarray(np.where(casi_blanco, 255, 0).astype("uint8"), "L").copy()
ImageDraw.floodfill(mask, (0, 0), 128)
m = np.asarray(mask).copy()
# 2) huecos interiores grandes (contrahueco de la R…): componentes blancas de más de 1500 px
ys, xs = np.where(m == 255)
visit = Image.fromarray(m).copy()
fondo = (m == 128)
pend = set(zip(ys.tolist()[::7], xs.tolist()[::7]))
for (y, x) in list(pend):
    if np.asarray(visit)[y, x] != 255:
        continue
    ImageDraw.floodfill(visit, (x, y), 64)
    comp = np.asarray(visit) == 64
    if comp.sum() > 1500:
        fondo |= comp
    visit = Image.fromarray(np.where(comp, 1, np.asarray(visit)).astype("uint8")).copy()
# 3) alfa: transparente en el fondo, suave en el borde según lo blanco que sea el píxel
alfa = np.where(fondo, 0, 255).astype(float)
borde = np.asarray(Image.fromarray(fondo.astype("uint8") * 255).filter(ImageFilter.MaxFilter(5))) > 0
suave = np.clip((255 - minc) / (255 - 200), 0, 1) * 255
alfa = np.where(borde & ~fondo, np.minimum(alfa, suave), alfa)
rgba = np.dstack([a, alfa]).astype("uint8")
img = Image.fromarray(rgba, "RGBA")
img = img.crop(img.getbbox())
w, h = img.size
pad = int(h * 0.04)
lienzo = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
lienzo.paste(img, (pad, pad))
img = lienzo
print("recortado:", img.size)

def guardar(im, nombre, ancho):
    r = im.resize((ancho, round(im.height * ancho / im.width)), Image.LANCZOS)
    r.save(f"{out}/{nombre}", optimize=True)
    r.save(f"{out}/{nombre.replace('.png', '.webp')}", quality=86, method=6)
    print(nombre, r.size)

guardar(img, "logo_rms.png", 900)       # login / cabeceras grandes
guardar(img, "logo_rms_sm.png", 440)    # barra lateral
# icono: la camiseta-R (primer tercio del logo), en un cuadrado
w, h = img.size
icono = img.crop((0, 0, int(w * 0.392), h))
icono = icono.crop(icono.getbbox())
lado = max(icono.size)
cuad = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
cuad.paste(icono, ((lado - icono.width) // 2, (lado - icono.height) // 2))
for n in (192, 64):
    cuad.resize((n, n), Image.LANCZOS).save(f"{out}/icono_rms_{n}.png", optimize=True)
print("icono", cuad.size)
