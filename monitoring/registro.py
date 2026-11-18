"""Log append-only em JSON Lines: uma linha por pedido ao serviço.

Cada linha é um objeto JSON independente, então o arquivo pode ser lido por pandas,
DuckDB, Loki ou um `tail -f` sem nenhum parser especial. O arquivo só cresce; quando
passa de MAX_BYTES ele é renomeado para `requisicoes.jsonl.1` (o .1 vira .2 e assim por
diante, até BACKUPS arquivos) e um arquivo novo é aberto.
"""

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

PASTA_LOGS = Path(os.environ.get("MONITOR_LOG_DIR") or "logs")
ARQUIVO = PASTA_LOGS / "requisicoes.jsonl"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB por arquivo
BACKUPS = 10  # guarda até 10 arquivos girados (~110 MB no total)


class FormatoJson(logging.Formatter):
    """Põe o horário do evento no início da linha, em ISO 8601 com microssegundos e fuso."""

    def format(self, record: logging.LogRecord) -> str:
        quando = datetime.fromtimestamp(record.created).astimezone()
        return json.dumps({"ts": quando.isoformat(timespec="microseconds"), **record.msg},
                          ensure_ascii=False)


def _criar_logger() -> logging.Logger:
    logger = logging.getLogger("monitoramento")
    if not logger.handlers:
        PASTA_LOGS.mkdir(parents=True, exist_ok=True)
        # Modo "a" (append): reabrir o arquivo nunca apaga o que já estava lá.
        handler = RotatingFileHandler(ARQUIVO, mode="a", maxBytes=MAX_BYTES,
                                      backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(FormatoJson())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # não duplica no terminal do BentoML
    return logger


def registrar(evento: dict) -> None:
    _criar_logger().info(evento)


def ler_eventos() -> list[dict]:
    """Todos os eventos, do mais antigo ao mais novo, incluindo os arquivos girados."""
    arquivos = sorted(PASTA_LOGS.glob("requisicoes.jsonl.*"),
                      key=lambda p: int(p.suffix[1:]), reverse=True) + [ARQUIVO]
    eventos = []
    for arquivo in arquivos:
        if arquivo.exists():
            with open(arquivo, encoding="utf-8") as f:
                eventos += [json.loads(linha) for linha in f if linha.strip()]
    return eventos
