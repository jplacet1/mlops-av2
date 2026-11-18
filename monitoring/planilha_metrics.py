"""Monta docs/metrics/analise_metrics.xlsx a partir de duas leituras do GET /metrics.

O /metrics só traz contadores acumulados desde que o processo subiu. Para analisar
uma janela de tempo, subtrai-se uma leitura antiga (A) de uma nova (B). A planilha faz
isso com fórmulas, para mostrar o método e não só o resultado:
  Latencia  histograma, percentis (p50/p90/p95/p99) e boxplot estimados pelos buckets
  Erros     taxa de erros 4xx e 5xx na janela
  Brutas    todas as séries da leitura B

Uso:
  curl -s http://localhost:3000/metrics > docs/metrics/snapshot_a.txt
  (gere carga)
  curl -s http://localhost:3000/metrics > docs/metrics/snapshot_b.txt
  uv run python -m monitoring.planilha_metrics docs/metrics/snapshot_a.txt docs/metrics/snapshot_b.txt
"""

import argparse
import math
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Font
from prometheus_client.parser import text_string_to_metric_families

NEGRITO = Font(bold=True)


def amostras(caminho: str) -> list:
    texto = Path(caminho).read_text(encoding="utf-8")
    return [s for f in text_string_to_metric_families(texto) for s in f.samples]


def buckets(lista, endpoint: str, codigo: str) -> dict[float, float]:
    return {float(s.labels["le"]): s.value for s in lista
            if s.name == "bentoml_service_request_duration_seconds_bucket"
            and s.labels.get("endpoint") == endpoint
            and s.labels.get("http_response_code") == codigo}


def serie(lista, nome: str, endpoint: str, codigo: str) -> float:
    return next((s.value for s in lista if s.name == nome
                 and s.labels.get("endpoint") == endpoint
                 and s.labels.get("http_response_code") == codigo), 0.0)


def aba_latencia(wb, a, b, endpoint, codigo):
    ws = wb.create_sheet("Latencia")
    ws.append([f"Latência do {endpoint} (HTTP {codigo}), janela = leitura B − leitura A"])
    ws["A1"].font = NEGRITO
    ws.append(["le (s)", "acumulado A", "acumulado B", "acumulado na janela",
               "pedidos no bucket", "% no bucket", "% acumulado", "rótulo"])
    for c in ws[2]:
        c.font = NEGRITO
    ba, bb = buckets(a, endpoint, codigo), buckets(b, endpoint, codigo)
    limites = sorted(bb)
    primeira = 3
    for i, le in enumerate(limites):
        r = primeira + i
        anterior = f"D{r - 1}" if i else "0"
        ws.append([
            "+Inf" if math.isinf(le) else le, ba.get(le, 0), bb[le],
            f"=C{r}-B{r}", f"=D{r}-{anterior}",
            f"=IF($D${primeira + len(limites) - 1}=0,0,E{r}/$D${primeira + len(limites) - 1})",
            f"=IF($D${primeira + len(limites) - 1}=0,0,D{r}/$D${primeira + len(limites) - 1})",
            f"≤ {'∞' if math.isinf(le) else le} s",
        ])
    ultima = primeira + len(limites) - 1
    total = f"$D${ultima}"
    faixa_le, faixa_acum, faixa_qtd = (f"$A${primeira}:$A${ultima}", f"$D${primeira}:$D${ultima}",
                                       f"$E${primeira}:$E${ultima}")

    r = ultima + 2
    soma = "bentoml_service_request_duration_seconds_sum"
    cont = "bentoml_service_request_duration_seconds_count"
    ws.append([])
    ws.append(["Resumo", "valor", "como"])
    ws[f"A{r + 1}"].font = NEGRITO
    ws.append(["pedidos na janela", f"={total}", "último bucket (+Inf) = total"])
    ws.append(["soma A (s)", serie(a, soma, endpoint, codigo), "_sum na leitura A"])
    ws.append(["soma B (s)", serie(b, soma, endpoint, codigo), "_sum na leitura B"])
    ws.append(["média (s)", f"=IF({total}=0,0,(B{r + 4}-B{r + 3})/{total})",
               "(soma B − soma A) / pedidos"])
    ws.append([])
    ws.append(["Percentil", "valor estimado (s)", "posição", "bucket", "limite inferior",
               "limite superior", "acumulado antes", "pedidos no bucket"])
    cab = r + 7
    for c in ws[cab]:
        c.font = NEGRITO
    # Mesmo cálculo do histogram_quantile() do Prometheus: acha o bucket onde cai a
    # posição q·N e interpola linearmente dentro dele.
    for q in (0.25, 0.50, 0.75, 0.90, 0.95, 0.99):
        r = ws.max_row + 1
        ws.append([
            q,
            f'=IF({total}=0,"sem dados",IF(ISNUMBER(F{r}),E{r}+(F{r}-E{r})*(C{r}-G{r})/H{r},E{r}))',
            f"={q}*{total}",
            f'=COUNTIF({faixa_acum},"<"&C{r})+1',
            f"=IF(D{r}=1,0,INDEX({faixa_le},D{r}-1))",
            f"=INDEX({faixa_le},D{r})",
            f"=IF(D{r}=1,0,INDEX({faixa_acum},D{r}-1))",
            f"=INDEX({faixa_qtd},D{r})",
        ])
    p25, p50, p75 = cab + 1, cab + 2, cab + 3
    ws.append([])
    ws.append(["Boxplot (estimado)", "valor (s)", "como"])
    ws[f"A{ws.max_row}"].font = NEGRITO
    ws.append(["Q1 (p25)", f"=B{p25}", "percentil 25"])
    ws.append(["mediana (p50)", f"=B{p50}", "percentil 50"])
    ws.append(["Q3 (p75)", f"=B{p75}", "percentil 75"])
    ws.append(["IQR", f"=B{p75}-B{p25}", "Q3 − Q1"])
    ws.append(["bigode inferior", f"=MAX(0,B{p25}-1.5*(B{p75}-B{p25}))", "Q1 − 1,5·IQR"])
    ws.append(["bigode superior", f"=B{p75}+1.5*(B{p75}-B{p25})", "Q3 + 1,5·IQR"])
    ws.append(["Atenção: com buckets, mínimo, máximo e outliers não são conhecidos; só se sabe "
               "em que faixa cada pedido caiu. Para o boxplot exato, use o log (latencia_ms)."])

    grafico = BarChart()
    grafico.title = f"Histograma da latência do {endpoint}"
    grafico.y_axis.title = "pedidos"
    grafico.x_axis.title = "bucket (até X s)"
    grafico.add_data(Reference(ws, min_col=5, min_row=2, max_row=ultima), titles_from_data=True)
    grafico.set_categories(Reference(ws, min_col=8, min_row=primeira, max_row=ultima))
    grafico.width, grafico.height = 22, 10
    ws.add_chart(grafico, "J2")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 20


def aba_erros(wb, a, b, endpoint, inicio, fim):
    ws = wb.create_sheet("Erros")
    ws.append([f"Pedidos ao {endpoint} por código HTTP (bentoml_service_request_total)"])
    ws["A1"].font = NEGRITO
    ws.append(["código", "leitura A", "leitura B", "na janela"])
    for c in ws[2]:
        c.font = NEGRITO
    codigos = sorted({s.labels["http_response_code"] for s in b
                      if s.name == "bentoml_service_request_total"
                      and s.labels.get("endpoint") == endpoint})
    for i, cod in enumerate(codigos):
        r = 3 + i
        ws.append([int(cod), serie(a, "bentoml_service_request_total", endpoint, cod),
                   serie(b, "bentoml_service_request_total", endpoint, cod), f"=C{r}-B{r}"])
    u = 2 + len(codigos)
    faixa_cod, faixa_n = f"A3:A{u}", f"D3:D{u}"
    ws.append([])
    ws.append(["total na janela", f"=SUM({faixa_n})"])
    t = ws.max_row
    ws.append(["erros 4xx (cliente)", f'=SUMIFS({faixa_n},{faixa_cod},">=400",{faixa_cod},"<500")'])
    ws.append(["erros 5xx (servidor)", f'=SUMIFS({faixa_n},{faixa_cod},">=500")'])
    ws.append(["taxa de erro 4xx", f"=IF(B{t}=0,0,B{t + 1}/B{t})"])
    ws.append(["taxa de erro 5xx", f"=IF(B{t}=0,0,B{t + 2}/B{t})"])
    ws.append(["taxa de erro total", f"=IF(B{t}=0,0,(B{t + 1}+B{t + 2})/B{t})"])
    for linha in range(t + 3, t + 6):
        ws[f"B{linha}"].number_format = "0.0%"
    ws.append([])
    ws.append(["leitura A em", inicio])
    ws.append(["leitura B em", fim])
    ws.append(["Observação: contadores com valor 0 (ex.: 500) aparecem porque o BentoML já "
               "cria a série; o código só conta quando acontece."])
    ws.column_dimensions["A"].width = 24


def aba_brutas(wb, b):
    ws = wb.create_sheet("Brutas")
    ws.append(["série", "rótulos", "valor"])
    for c in ws[1]:
        c.font = NEGRITO
    for s in b:
        ws.append([s.name, ", ".join(f"{k}={v}" for k, v in sorted(s.labels.items())), s.value])
    ws.column_dimensions["A"].width = 50
    ws.column_dimensions["B"].width = 90


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("leitura_a")
    ap.add_argument("leitura_b")
    ap.add_argument("--endpoint", default="/extract")
    ap.add_argument("--codigo", default="200", help="código HTTP da latência analisada")
    ap.add_argument("--saida", default="docs/metrics/analise_metrics.xlsx")
    args = ap.parse_args()

    a, b = amostras(args.leitura_a), amostras(args.leitura_b)
    quando = lambda p: (Path(p).with_suffix(".quando").read_text().strip()  # noqa: E731
                        if Path(p).with_suffix(".quando").exists() else "")
    wb = Workbook()
    wb.remove(wb.active)
    aba_latencia(wb, a, b, args.endpoint, args.codigo)
    aba_erros(wb, a, b, args.endpoint, quando(args.leitura_a), quando(args.leitura_b))
    aba_brutas(wb, b)
    wb.save(args.saida)
    print(f"planilha gravada em {args.saida}")


if __name__ == "__main__":
    main()
