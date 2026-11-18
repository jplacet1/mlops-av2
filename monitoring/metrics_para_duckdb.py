"""Lê o GET /metrics periodicamente e grava cada amostra num banco DuckDB.

Faz para o banco relacional o mesmo que o Prometheus faz para o TSDB dele: a cada
--intervalo segundos, uma "raspagem". Cada linha da tabela `amostras` é uma série
do /metrics naquele instante: (ts, metrica, rotulos, valor).

Uso:
  uv run python -m monitoring.metrics_para_duckdb                 # roda até Ctrl+C
  uv run python -m monitoring.metrics_para_duckdb --vezes 1       # uma raspagem só

Consulta de exemplo (as primeiras amostras de latência):
  uv run python -c "import duckdb; print(duckdb.connect('logs/metrics.duckdb').sql(
    \"select ts::varchar, metrica, valor from amostras where metrica like 'bentoml_service_request_duration%' limit 5\"))"
"""

import argparse
import json
import time
from datetime import datetime

import duckdb
import httpx
from prometheus_client.parser import text_string_to_metric_families

TABELA = """
create table if not exists amostras (
    ts        timestamptz not null,  -- horário da raspagem
    metrica   varchar     not null,  -- nome da série, ex.: ..._bucket, ..._sum, ..._count
    rotulos   json        not null,  -- labels do Prometheus, ex.: {"endpoint": "/extract"}
    valor     double      not null
)"""


def raspar(url: str) -> list[tuple]:
    agora = datetime.now().astimezone()
    texto = httpx.get(url, timeout=10).text
    return [(agora, s.name, json.dumps(s.labels, sort_keys=True), s.value)
            for familia in text_string_to_metric_families(texto) for s in familia.samples]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:3000/metrics")
    ap.add_argument("--banco", default="logs/metrics.duckdb")
    ap.add_argument("--intervalo", type=float, default=15, help="segundos entre raspagens")
    ap.add_argument("--vezes", type=int, default=0, help="0 = sem fim")
    args = ap.parse_args()

    con = duckdb.connect(args.banco)
    con.execute(TABELA)
    feitas = 0
    while True:
        try:
            linhas = raspar(args.url)
            con.executemany("insert into amostras values (?, ?, ?, ?)", linhas)
            print(f"{datetime.now():%H:%M:%S} {len(linhas)} amostras gravadas", flush=True)
        except httpx.HTTPError as erro:
            print(f"{datetime.now():%H:%M:%S} serviço fora do ar ({erro}); tento de novo")
        feitas += 1
        if args.vezes and feitas >= args.vezes:
            break
        time.sleep(args.intervalo)
    con.close()


if __name__ == "__main__":
    main()
