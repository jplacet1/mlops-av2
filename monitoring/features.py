"""Variáveis de entrada da foto que vêm das atividades da disciplina (código do Henrique,
branch av2-monitoramento de henriquebouwman/ml-ops-av1):

- aula 011: médias dos canais R, G e B;
- atividade de características (04/11): croma média, qualidade tonal e nitidez_512.

`nitidez` depende do tamanho da imagem (achado da atividade de características), então
`nitidez_512` mede a mesma coisa depois de reduzir o maior lado para 512 px: é ela que
se compara entre fotos de tamanhos diferentes.
"""

import numpy as np
from PIL import Image, ImageFilter, ImageStat

LADO_NORMALIZADO = 512

# Limiares da qualidade tonal (mesmos da atividade de características; não calibrados).
ESCURO_MAX, CLARO_MIN = 16, 240
FRAC_ESCURO, FRAC_CLARO, AMPLITUDE_MIN = 0.30, 0.05, 150


def nitidez_512(imagem: Image.Image) -> float:
    cinza = imagem.convert("L")
    escala = LADO_NORMALIZADO / max(cinza.size)
    tamanho = (max(1, round(cinza.width * escala)), max(1, round(cinza.height * escala)))
    cinza = cinza.resize(tamanho, Image.Resampling.BILINEAR)
    return ImageStat.Stat(cinza.filter(ImageFilter.FIND_EDGES)).var[0]


def croma_media(rgb: Image.Image) -> float:
    """Distância média ao cinza no plano CbCr: medidor e display quase não têm cor."""
    ycbcr = np.asarray(rgb.convert("YCbCr"), dtype=np.float32)
    return float(np.hypot(ycbcr[..., 1] - 128, ycbcr[..., 2] - 128).mean())


def qualidade_tonal(luma: np.ndarray) -> dict:
    frac_escuro = float((luma <= ESCURO_MAX).mean())
    frac_claro = float((luma >= CLARO_MIN).mean())
    p5, p95 = np.percentile(luma, [5, 95])
    amplitude = float(p95 - p5)
    if frac_escuro >= FRAC_ESCURO:
        classe = "SUBEXPOSTA"
    elif frac_claro >= FRAC_CLARO:
        classe = "SUPEREXPOSTA"
    elif amplitude < AMPLITUDE_MIN:
        classe = "BAIXO_CONTRASTE"
    else:
        classe = "NORMAL"
    return {
        "qualidade_tonal": classe,
        "frac_escuro": round(frac_escuro, 4),
        "frac_claro": round(frac_claro, 4),
        "amplitude_p5_p95": round(amplitude, 1),
    }


def features_imagem(imagem: Image.Image) -> dict:
    rgb = imagem.convert("RGB")
    rgb.thumbnail((1024, 1024))  # só para o cálculo, mesmo limite da nitidez do serviço
    arr = np.asarray(rgb, dtype=np.float32)
    return {
        "media_r": round(float(arr[..., 0].mean()), 2),
        "media_g": round(float(arr[..., 1].mean()), 2),
        "media_b": round(float(arr[..., 2].mean()), 2),
        "croma_media": round(croma_media(rgb), 2),
        "nitidez_512": round(nitidez_512(imagem), 1),
        **qualidade_tonal(np.asarray(rgb.convert("L"), dtype=np.float32)),
    }
