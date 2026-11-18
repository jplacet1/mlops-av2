"""Gera carga no POST /extract a partir das fotos de examples/.

Cada envio é uma variação aleatória de uma das fotos, para o log não ficar com 5
imagens repetidas. Dois perfis:
  referencia  variações leves (recorte, giro de poucos graus, brilho ±10%, JPEG 75-95):
              fotos que o serviço deveria ler bem
  producao    além disso, desfoque, escurecimento e ruído como os de uma foto de campo

Uso:
  uv run python -m monitoring.gerar_carga --n 20
  uv run python -m monitoring.gerar_carga --n 100 --perfil producao --paralelo 4
"""

import argparse
import io
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from PIL import Image, ImageEnhance, ImageFilter


def variar(foto: Image.Image, perfil: str, rnd: random.Random) -> bytes:
    img = foto.convert("RGB")
    w, h = img.size
    corte = rnd.uniform(0.9, 1.0)
    x, y = rnd.uniform(0, w * (1 - corte)), rnd.uniform(0, h * (1 - corte))
    img = img.crop((x, y, x + w * corte, y + h * corte))
    img = img.rotate(rnd.uniform(-5, 5), expand=False, fillcolor=(128, 128, 128))
    img = ImageEnhance.Brightness(img).enhance(rnd.uniform(0.9, 1.1))
    if perfil == "producao":
        img = img.filter(ImageFilter.GaussianBlur(rnd.uniform(0, 4)))
        img = ImageEnhance.Brightness(img).enhance(rnd.uniform(0.3, 1.0))
        ruido = Image.effect_noise(img.size, rnd.uniform(0, 40)).convert("RGB")
        img = Image.blend(img, ruido, rnd.uniform(0, 0.25))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=rnd.randint(75, 95))
    return buffer.getvalue()


def enviar(cliente: httpx.Client, url: str, nome: str, conteudo: bytes) -> tuple[int, float]:
    inicio = time.perf_counter()
    try:
        r = cliente.post(url, files={"image": (nome, conteudo, "image/jpeg")})
        codigo = r.status_code
    except httpx.HTTPError:
        codigo = 0  # sem resposta (timeout, conexão recusada)
    return codigo, time.perf_counter() - inicio


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=20, help="quantos pedidos (padrão 20)")
    ap.add_argument("--perfil", choices=["referencia", "producao"], default="referencia")
    ap.add_argument("--paralelo", type=int, default=1,
                    help="pedidos simultâneos (padrão 1; >1 inflaciona a latência medida)")
    ap.add_argument("--url", default="http://localhost:3000/extract")
    ap.add_argument("--fotos", default="examples", help="pasta com as fotos base")
    ap.add_argument("--semente", type=int, default=42)
    args = ap.parse_args()

    fotos = [Image.open(p) for p in sorted(Path(args.fotos).iterdir())
             if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    rnd = random.Random(args.semente)
    lote = [(f"carga_{i:04d}.jpg", variar(rnd.choice(fotos), args.perfil, rnd))
            for i in range(args.n)]

    codigos, tempos = Counter(), []
    with httpx.Client(timeout=200) as cliente, ThreadPoolExecutor(args.paralelo) as pool:
        futuros = [pool.submit(enviar, cliente, args.url, nome, c) for nome, c in lote]
        for i, futuro in enumerate(as_completed(futuros), 1):
            codigo, segundos = futuro.result()
            codigos[codigo] += 1
            tempos.append(segundos)
            print(f"[{i}/{args.n}] HTTP {codigo} em {segundos:.2f} s", flush=True)

    print(f"\nPerfil {args.perfil}: {dict(codigos)}; "
          f"tempo médio {sum(tempos) / len(tempos):.2f} s; o rastro está em logs/requisicoes.jsonl")


if __name__ == "__main__":
    main()
