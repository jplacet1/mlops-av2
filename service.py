"""Serviço BentoML: valida a imagem recebida, chama o extrator e devolve a extração.

Cada pedido ao /extract vira uma linha em logs/requisicoes.jsonl (ver monitoring/).
"""

import hashlib
import threading
import time
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Literal

import bentoml
import httpx
from bentoml.exceptions import BentoMLException, InvalidArgument, ServiceUnavailable
from PIL import Image, ImageStat, UnidentifiedImageError
from pydantic import BaseModel

from extractor import aquecer_modelo, extrair, ollama_pronto
from monitoring.api import app as app_monitor
from monitoring.registro import registrar

# Métricas próprias, expostas no mesmo GET /metrics do BentoML (formato Prometheus).
CONFIANCA = bentoml.metrics.Histogram(
    name="leitura_confianca_geral", documentation="Confiança geral devolvida pelo /extract",
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0))
NITIDEZ = bentoml.metrics.Histogram(
    name="leitura_nitidez_entrada", documentation="Nitidez (variância de bordas) da foto recebida",
    buckets=(50, 100, 200, 300, 500, 1000, 2000, 4000, 8000))
STATUS = bentoml.metrics.Counter(
    name="leitura_status", documentation="Respostas do /extract por status", labelnames=["status"])


class Confianca(BaseModel):
    numero_medidor: float
    funcao: float
    consumo: float
    geral: float


class Extracao(BaseModel):
    numero_medidor: str | None
    funcao: str | None
    consumo: float | None
    confianca: Confianca
    status: Literal["OK", "ILEGIVEL"]
    modelo: str
    tempo_ms: int


def sinais_de_entrada(arquivo: Path, imagem: Image.Image) -> dict:
    """O que se mede da foto recebida. A foto em si nunca vai para o log, só o hash."""
    cinza = ImageStat.Stat(imagem.convert("L"))
    return {
        "largura": imagem.width,
        "altura": imagem.height,
        "formato": imagem.format,
        "modo_cor": imagem.mode,
        "bytes": arquivo.stat().st_size,
        "brilho_medio": round(cinza.mean[0], 2),
        "contraste": round(cinza.stddev[0], 2),
        "sha256": hashlib.sha256(arquivo.read_bytes()).hexdigest(),
    }


def sinais_de_saida(resultado: dict) -> dict:
    """O que se mede da resposta. O número do medidor identifica o cliente em produção,
    então só se registra se foi lido, não o valor."""
    return {
        "status": resultado["status"],
        "confianca_geral": resultado["confianca"]["geral"],
        "confianca_numero_medidor": resultado["confianca"]["numero_medidor"],
        "confianca_funcao": resultado["confianca"]["funcao"],
        "confianca_consumo": resultado["confianca"]["consumo"],
        "leu_numero_medidor": resultado["numero_medidor"] is not None,
        "leu_funcao": resultado["funcao"] is not None,
        "consumo": resultado["consumo"],
        "campos_lidos": sum(resultado[c] is not None
                            for c in ("numero_medidor", "funcao", "consumo")),
        "modelo": resultado["modelo"],
        "tempo_modelo_ms": resultado["tempo_ms"],
    }


@bentoml.asgi_app(app_monitor, path="/monitor")
@bentoml.service(name="leitura_medidor", traffic={"timeout": 180})
class LeituraMedidor:
    def __init__(self) -> None:
        # Em segundo plano, para o serviço subir na hora mesmo com o Ollama lento.
        threading.Thread(target=aquecer_modelo, daemon=True).start()

    def __is_ready__(self) -> bool:
        # /readyz só responde 200 quando o Ollama está de pé com o modelo baixado.
        return ollama_pronto()

    @bentoml.api(route="/extract")
    def extract(self, image: Path) -> Extracao:
        """Recebe a foto do medidor (multipart, campo `image`) e devolve a extração."""
        inicio = time.perf_counter()
        evento = {"request_id": uuid.uuid4().hex, "endpoint": "/extract"}
        try:
            resposta = self._extrair(image, evento)
            evento["http_status"] = 200
            return resposta
        except BentoMLException as erro:
            evento["http_status"] = int(erro.error_code)
            evento["erro"] = str(erro)
            raise
        except Exception as erro:  # qualquer outra falha sai como 500 no BentoML
            evento["http_status"] = 500
            evento["erro"] = f"{type(erro).__name__}: {erro}"
            raise
        finally:
            evento["latencia_ms"] = round((time.perf_counter() - inicio) * 1000, 2)
            registrar(evento)

    def _extrair(self, image: Path, evento: dict) -> Extracao:
        try:
            imagem = Image.open(image)
            imagem.load()  # força a leitura completa: pega arquivos truncados
        except (UnidentifiedImageError, OSError):
            evento["entrada"] = {"bytes": image.stat().st_size}
            raise InvalidArgument(
                "O campo 'image' não é uma imagem válida. Envie um arquivo JPEG, PNG ou WebP."
            )
        evento["entrada"] = sinais_de_entrada(image, imagem)

        try:
            resultado = extrair(imagem)
        except httpx.HTTPError:
            raise ServiceUnavailable(
                "Modelo indisponível: confira se o Ollama está rodando (ollama serve)."
            )
        except ValueError:
            raise BentoMLException(
                "O modelo devolveu uma resposta fora do formato esperado.",
                error_code=HTTPStatus.BAD_GATEWAY,
            )
        evento["entrada"]["nitidez"] = resultado.pop("_nitidez")
        evento["chamou_modelo"] = resultado.pop("_chamou_modelo")
        evento["saida"] = sinais_de_saida(resultado)

        NITIDEZ.observe(evento["entrada"]["nitidez"])
        CONFIANCA.observe(resultado["confianca"]["geral"])
        STATUS.labels(status=resultado["status"]).inc()
        return Extracao(**resultado)
