"""Confere, de ponta a ponta, que o monitoramento e todos os extras funcionam.

Pré-requisitos: serviço no ar (just serve) e, para os extras, a stack (just stack).
Manda 3 pedidos marcados (foto boa, foto borrada, arquivo inválido) e segue cada um
até o log, o /monitor/variaveis, o /metrics, o Prometheus, o Loki, o Grafana e o DuckDB.

Uso: uv run python -m monitoring.verificar
"""

import io
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import httpx
from PIL import Image, ImageFilter

from monitoring.metrics_para_duckdb import TABELA, raspar
from monitoring.registro import ler_eventos

SERVICO, PROM, LOKI, GRAFANA = ("http://localhost:3000", "http://localhost:9090",
                                "http://localhost:3100", "http://localhost:3001")
FOTO = "examples/example02.jpg"
falhas = 0


def checar(descricao: str, ok: bool, detalhe: str = "") -> None:
    global falhas
    falhas += not ok
    print(f"[{'OK ' if ok else 'FALHOU'}] {descricao}{f'  ({detalhe})' if detalhe else ''}",
          flush=True)


def esperar(funcao, segundos: int = 45):
    """Repete até devolver algo verdadeiro (o Prometheus coleta a cada 15 s)."""
    fim = time.time() + segundos
    while True:
        try:
            valor = funcao()
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            valor = None
        if valor or time.time() > fim:
            return valor
        time.sleep(3)


def promql(consulta: str) -> float:
    r = httpx.get(f"{PROM}/api/v1/query", params={"query": consulta}).json()
    return float(r["data"]["result"][0]["value"][1])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # no Windows, `> arquivo` usaria cp1252
    c = httpx.Client(timeout=200)
    print("== Serviço")
    checar("GET /healthz responde 200", c.get(f"{SERVICO}/healthz").status_code == 200)
    checar("GET /readyz responde 200 (Ollama com o modelo)",
           c.get(f"{SERVICO}/readyz").status_code == 200)

    print("== Log em arquivo")
    antes = len(ler_eventos())
    borrada = io.BytesIO()
    Image.open(FOTO).filter(ImageFilter.GaussianBlur(4)).save(borrada, format="JPEG")
    inicio = time.perf_counter()
    r1 = c.post(f"{SERVICO}/extract", files={"image": ("boa.jpg", Path(FOTO).read_bytes())})
    r2 = c.post(f"{SERVICO}/extract", files={"image": ("borrada.jpg", borrada.getvalue())})
    r3 = c.post(f"{SERVICO}/extract", files={"image": ("falsa.jpg", b"nao e imagem")})
    print(f"   3 pedidos em {time.perf_counter() - inicio:.1f} s: HTTP "
          f"{r1.status_code}, {r2.status_code}, {r3.status_code}")
    eventos = ler_eventos()
    novos = eventos[antes:]
    checar("cada pedido acrescentou exatamente 1 linha", len(novos) == 3,
           f"{antes} → {len(eventos)} linhas")
    checar("linhas antigas intactas (append-only)", eventos[:antes] == ler_eventos()[:antes])
    boa, ruim, falsa = novos if len(novos) == 3 else ({}, {}, {})
    checar("foto boa: status OK, nitidez e confiança registradas",
           boa.get("saida", {}).get("status") == "OK" and "nitidez" in boa.get("entrada", {}),
           f"nitidez {boa.get('entrada', {}).get('nitidez')}, "
           f"confiança {boa.get('saida', {}).get('confianca_geral')}")
    novas = ("nitidez_512", "croma_media", "media_r", "media_g", "media_b", "qualidade_tonal")
    checar("variáveis da atividade de características no log",
           all(v in boa.get("entrada", {}) for v in novas),
           ", ".join(f"{v} {boa.get('entrada', {}).get(v)}" for v in novas[:2] + novas[-1:]))
    checar("foto borrada: ILEGIVEL sem chamar o modelo",
           ruim.get("saida", {}).get("status") == "ILEGIVEL" and ruim.get("chamou_modelo") is False,
           f"nitidez {ruim.get('entrada', {}).get('nitidez')}")
    checar("arquivo inválido: HTTP 400 registrado com o erro",
           falsa.get("http_status") == 400 and "erro" in falsa)
    checar("ts em ISO 8601 com microssegundos e fuso", len(boa.get("ts", "")) == 32,
           boa.get("ts", ""))
    checar("número do medidor não vai para o log (LGPD)",
           "00175519" not in str(novos) and "numero_medidor" not in boa.get("saida", {}))

    print("== GET /metrics")
    texto = c.get(f"{SERVICO}/metrics").text
    for serie in ('bentoml_service_request_duration_seconds_bucket{endpoint="/extract"',
                  'bentoml_service_request_total{endpoint="/extract",http_response_code="400"',
                  'leitura_status_total{status="ILEGIVEL"}', "leitura_confianca_geral_bucket",
                  "leitura_nitidez_entrada_bucket"):
        checar(f"/metrics tem {serie.split('{')[0]}", serie in texto)

    print("== Extra: GET /monitor/variaveis (20 pts)")
    j = c.get(f"{SERVICO}/monitor/variaveis", params={"status": "400"}).json()
    checar("JSON filtrado por status traz o pedido inválido",
           any(v["request_id"] == falsa.get("request_id") for v in j["variaveis"]),
           f"{j['total']} pedidos com 400")
    csv = c.get(f"{SERVICO}/monitor/variaveis", params={"formato": "csv"})
    checar("CSV com colunas achatadas", "entrada_nitidez" in csv.text.splitlines()[0],
           f"{len(csv.text.splitlines()) - 1} linhas")

    print("== Extra: Prometheus coletando o /metrics (15 pts)")
    alvos = esperar(lambda: httpx.get(f"{PROM}/api/v1/targets").json())
    saude = [t["health"] for t in (alvos or {}).get("data", {}).get("activeTargets", [])]
    checar("alvo leitura_medidor está UP", saude == ["up"], str(saude))
    total_servico = sum(float(l.rsplit(" ", 1)[1]) for l in texto.splitlines()
                        if l.startswith('bentoml_service_request_total{endpoint="/extract"'))
    no_prom = esperar(lambda: promql('sum(bentoml_service_request_total{endpoint="/extract"})')
                      >= total_servico and promql(
                          'sum(bentoml_service_request_total{endpoint="/extract"})'))
    checar("Prometheus já tem os pedidos de agora", bool(no_prom),
           f"/metrics: {total_servico:.0f}, Prometheus: {no_prom}")
    p95 = esperar(lambda: promql('histogram_quantile(0.95, sum by (le) (rate('
                                 'bentoml_service_request_duration_seconds_bucket'
                                 '{endpoint="/extract"}[1h])))'))
    checar("PromQL calcula o p95 da latência", p95 is not None, f"p95 = {p95} s")

    print("== Extra: /metrics salvo no banco do Prometheus (10 pts)")
    coletas = esperar(lambda: promql('count_over_time(sum(bentoml_service_request_total'
                                     '{endpoint="/extract"})[24h:15s])'))
    checar("TSDB guarda o histórico de coletas", (coletas or 0) > 1, f"{coletas} pontos em 24 h")
    tsdb = httpx.get(f"{PROM}/api/v1/status/tsdb").json()["data"]["headStats"]
    checar("banco com séries gravadas", tsdb["numSeries"] > 0, f"{tsdb['numSeries']} séries")

    print("== Extra: log no Loki (5 pts)")
    def no_loki():
        r = httpx.get(f"{LOKI}/loki/api/v1/query_range", params={
            "query": f'{{job="leitura_medidor"}} |= "{falsa["request_id"]}"',
            "start": str(int((time.time() - 3600) * 1e9))}).json()
        return r["data"]["result"]
    achou = esperar(no_loki)
    checar("linha do pedido inválido chegou ao Loki (pelo request_id)", bool(achou),
           achou[0]["stream"].get("http_status", "") if achou else "")

    print("== Grafana")
    checar("Grafana de pé", httpx.get(f"{GRAFANA}/api/health").status_code == 200)
    fontes = sorted(d["type"] for d in httpx.get(f"{GRAFANA}/api/datasources").json())
    checar("fontes Prometheus e Loki configuradas", fontes == ["loki", "prometheus"], str(fontes))

    print("== Extra: /metrics salvo no DuckDB (5 pts)")
    with tempfile.TemporaryDirectory() as pasta:
        con = duckdb.connect(f"{pasta}/teste.duckdb")
        con.execute(TABELA)
        con.executemany("insert into amostras values (?, ?, ?, ?)", raspar(f"{SERVICO}/metrics"))
        n = con.execute("select count(*) from amostras").fetchone()[0]
        v400 = con.execute("select valor from amostras where metrica='bentoml_service_request_total' "
                       "and rotulos->>'endpoint'='/extract' "
                       "and rotulos->>'http_response_code'='400'").fetchone()
        con.close()
    checar("raspagem gravada na tabela amostras", n > 0, f"{n} linhas")
    checar("consulta SQL acha o contador de 400", bool(v400) and v400[0] >= 1, str(v400))

    print(f"\n{'TUDO OK' if not falhas else f'{falhas} FALHA(S)'}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
