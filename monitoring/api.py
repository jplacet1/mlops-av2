"""GET /monitor/variaveis: devolve as variáveis do log, uma linha por pedido, já
achatadas em colunas (entrada.brilho_medio vira entrada_brilho_medio) e prontas para
histograma, boxplot, KS e PSI.

Parâmetros (todos opcionais):
  desde, ate   horário ISO 8601 (ex.: 2026-11-18T10:00:00-03:00); filtra por `ts`
  status       só pedidos com esse código HTTP (ex.: 200)
  formato      json (padrão) ou csv
"""

import csv
import io
from datetime import datetime

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from monitoring.registro import ler_eventos


def achatar(evento: dict) -> dict:
    linha = {}
    for chave, valor in evento.items():
        if isinstance(valor, dict):
            linha.update({f"{chave}_{k}": v for k, v in valor.items()})
        else:
            linha[chave] = valor
    return linha


def filtrar(eventos: list[dict], desde: str | None, ate: str | None,
            status: str | None) -> list[dict]:
    inicio = datetime.fromisoformat(desde) if desde else None
    fim = datetime.fromisoformat(ate) if ate else None
    saida = []
    for e in eventos:
        ts = datetime.fromisoformat(e["ts"])
        if inicio and ts < inicio or fim and ts > fim:
            continue
        if status and str(e.get("http_status")) != status:
            continue
        saida.append(achatar(e))
    return saida


async def variaveis(request: Request) -> Response:
    p = request.query_params
    try:
        linhas = filtrar(ler_eventos(), p.get("desde"), p.get("ate"), p.get("status"))
    except (ValueError, TypeError):  # TypeError: horário sem fuso comparado com o do log
        return JSONResponse({"erro": "desde/ate devem estar em ISO 8601, com fuso"}, 400)

    if p.get("formato") == "csv":
        colunas = list(dict.fromkeys(c for linha in linhas for c in linha))
        buffer = io.StringIO()
        escritor = csv.DictWriter(buffer, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(linhas)
        return Response(buffer.getvalue(), media_type="text/csv; charset=utf-8")
    return JSONResponse({"total": len(linhas), "variaveis": linhas})


app = Starlette(routes=[Route("/variaveis", variaveis, methods=["GET"])])
