# Monitoramento do serviço de leitura de medidor (AV2, Parte 1)

Este documento é o roteiro de avaliação da Parte 1 da AV2. O serviço é o mesmo da AV1
(`POST /extract`, ver [README principal](../README.md)). Nesta parte, o serviço passou a
gravar o rastro de cada pedido e de cada resposta num arquivo, e ganhou a infraestrutura
para analisar esse rastro.

| Item do enunciado | Onde está | Pontos |
|---|---|---|
| Log em arquivo, append-only | [`registro.py`](registro.py) + [`service.py`](../service.py) → `logs/requisicoes.jsonl` | 50 |
| `GET /metrics` + texto e planilha de análise | [seção /metrics](#3-get-metrics-como-ler) + [`docs/metrics/analise_metrics.xlsx`](../docs/metrics/analise_metrics.xlsx) | 10 |
| Extra: log no Loki | [`stack/`](stack/) (Alloy lê o arquivo e envia ao Loki) | 5 |
| Extra: endpoint que extrai as variáveis | `GET /monitor/variaveis` ([`api.py`](api.py)) | 20 |
| Extra: Prometheus coletando o `/metrics` | [`stack/prometheus.yml`](stack/prometheus.yml) | 15 |
| Extra: `/metrics` salvo no banco do Prometheus | volume `prometheus_dados` (TSDB), retenção de 15 dias | 10 |
| Extra: `/metrics` salvo em banco relacional | [`metrics_para_duckdb.py`](metrics_para_duckdb.py) → `logs/metrics.duckdb` | 5 |

**Conferência rápida.** Com o serviço e a stack no ar, `uv run just verificar` manda 3
pedidos marcados e segue cada um pelo log, `/monitor/variaveis`, `/metrics`, Prometheus,
Loki, Grafana e DuckDB, imprimindo `OK` ou `FALHOU` por item. A saída de uma execução
num clone limpo está em [`docs/evidencias_av2.txt`](../docs/evidencias_av2.txt).

## 1. Do zero até ver o rastro evoluir

Pré-requisitos: os mesmos do README principal (uv, Ollama com `qwen2.5vl:3b`). Os
extras precisam também do [Docker](https://docs.docker.com/get-docker/) com Compose.
Testado em Windows 11 (Git Bash e PowerShell) com Docker Desktop 4.99 (Engine 29.9).

```bash
# Terminal 1: serviço
uv run just setup
uv run just model        # só na primeira vez (3,2 GB)
uv run just serve        # http://localhost:3000; o modelo é aquecido em segundo plano
```

```bash
# Terminal 2: acompanhar o rastro ao vivo
uv run just rastro       # = tail -f logs/requisicoes.jsonl
```

No PowerShell, o equivalente é `Get-Content logs/requisicoes.jsonl -Wait -Tail 5`.

```bash
# Terminal 3: gerar carga
uv run just carga 10 referencia    # 10 variações leves das fotos de examples/
uv run just carga 30 producao      # 30 variações degradadas (desfoque, escuro, ruído)
```

Cada pedido aparece como uma linha nova no terminal 2. O script de carga
([`gerar_carga.py`](gerar_carga.py)) aceita `--paralelo N` para mandar N pedidos ao mesmo
tempo, mas isso infla a latência medida, porque os pedidos esperam na fila do modelo.

**Tempo esperado.** Nesta máquina (Ryzen 5 5600X, Radeon RX 6600), uma foto que chega
ao modelo leva de 2 a 16 s; uma foto barrada pela checagem de nitidez volta em ~10 ms
sem chamar o modelo. Por isso o perfil `producao` roda bem mais rápido que o
`referencia`.

## 2. O log em arquivo

**Arquivo:** `logs/requisicoes.jsonl` (pasta configurável pela variável `MONITOR_LOG_DIR`).
A pasta `logs/` está no `.gitignore`.

**Formato:** [JSON Lines](https://jsonlines.org/). Cada linha é um pedido e é um JSON
completo, então pandas (`pd.read_json(..., lines=True)`), DuckDB
(`read_json_auto('logs/requisicoes.jsonl')`), Loki e um simples `tail` leem o arquivo
sem parser próprio. Exemplo real (quebrado em linhas aqui só para leitura):

```json
{"ts": "2026-11-17T21:02:13.987504-03:00", "request_id": "09f5f92a21814cd8bf5eb99ff0859f31",
 "endpoint": "/extract",
 "entrada": {"largura": 443, "altura": 442, "formato": "JPEG", "modo_cor": "RGB",
             "bytes": 19034, "brilho_medio": 126.88, "contraste": 33.48,
             "sha256": "2abf2620…", "nitidez": 212.47},
 "chamou_modelo": false,
 "saida": {"status": "ILEGIVEL", "confianca_geral": 0.0, "confianca_numero_medidor": 0.0,
           "confianca_funcao": 0.0, "confianca_consumo": 0.0, "leu_numero_medidor": false,
           "leu_funcao": false, "consumo": null, "campos_lidos": 0,
           "modelo": "qwen2.5vl:3b", "tempo_modelo_ms": 2},
 "http_status": 200, "latencia_ms": 7.83}
```

**Data e hora:** `ts` em ISO 8601 com microssegundos e fuso horário
(`2026-11-17T21:02:13.987504-03:00`). É o formato mais detalhado que o `logging` do Python
oferece, e o fuso evita ambiguidade quando o log é juntado com o de outra máquina.

**Append-only:** o arquivo é aberto em modo `"a"` por um `RotatingFileHandler`
([`registro.py`](registro.py)). Linhas novas só são acrescentadas no fim; nada é
reescrito. Quando o arquivo passa de 10 MB, ele é renomeado para `requisicoes.jsonl.1`
(o `.1` vira `.2`, e assim por diante, até 10 arquivos) e um arquivo novo é aberto.
`GET /monitor/variaveis` e o Alloy leem também os arquivos girados.

**Pedidos com erro também são registrados**, com `http_status` (400, 502, 503 ou 500) e
`erro`. Um arquivo que não é imagem gera uma linha com `entrada.bytes` e a mensagem do
erro. A exceção é o pedido sem o campo `image`: ele é recusado pelo BentoML antes de chegar
ao nosso código, então só aparece no `/metrics` (código 400).

### Por que estas variáveis

O objetivo do serviço, no entendimento do negócio (CRISP-DM), é **triar as fotos de
leitura que hoje passam por conferência manual**. O problema central apontado no
entendimento dos dados é a **qualidade da foto de campo**: baixa resolução, desfoque,
reflexo e pouca luz. É isso que faz o modelo errar ou inventar números (ver Limitações no
README principal). As variáveis foram escolhidas para que se possa ver, ao longo do tempo,
se as fotos que chegam estão piores que as de referência e se as respostas mudam com isso.

| Variável | Tipo | Por que monitorar | Teste de drift |
|---|---|---|---|
| `entrada.nitidez` | contínua | É o mesmo sinal que decide se o modelo é chamado (limiar 300). Se a distribuição cair, mais fotos viram `ILEGIVEL`. | KS |
| `entrada.brilho_medio`, `entrada.contraste` | contínuas (0–255) | Fotos noturnas ou com reflexo. | KS |
| `entrada.largura`, `altura`, `bytes` | contínuas | Mudança no app do leiturista ou na compressão das fotos. | KS |
| `entrada.formato` | categórica | Mudança de origem das fotos. | PSI |
| `saida.status` | categórica (`OK`/`ILEGIVEL`) | Principal indicador de negócio: fração de fotos que o serviço não consegue ler. | PSI |
| `saida.confianca_geral` | contínua (0–1) | Queda indica que o modelo está menos seguro, mesmo com `status` OK. | KS |
| `saida.campos_lidos` | categórica (0–3) | Quantos dos três campos vieram preenchidos. | PSI |
| `saida.consumo` | contínua | Leituras fora da faixa habitual. | KS |
| `latencia_ms` | contínua | Percentis de tempo de resposta e SLA (Parte 2). | — |
| `chamou_modelo` | binária | Separa as respostas rápidas (barradas na nitidez) das lentas. | PSI |

Para a Parte 2, as variáveis principais são **`entrada.nitidez` (entrada, KS)** e
**`saida.status` (saída, PSI)**.

**O que não vai para o log, de propósito:** a foto e o número do medidor. Em produção,
o número do medidor identifica o cliente (LGPD), então o log guarda só se ele foi lido
(`leu_numero_medidor`). A foto é representada pelo `sha256`, que permite achar repetições
sem guardar a imagem.

**Os dados bastam?** Fizemos uma conferência com 45 pedidos de carga: 15 de referência
(`--semente 1`) e 30 de produção (`--semente 2`). As variáveis escolhidas separam bem os
dois perfis, que é o tipo de diferença que KS e PSI devem captar na Parte 2:

| | referência (15) | produção (30) |
|---|---|---|
| `entrada.nitidez`: mín. / mediana / máx. | 412 / 2185 / 4851 | 25 / 223 / 1626 |
| fotos acima do limiar de 300 | 15 (100%) | 12 (40%) |
| `saida.status = OK` | 15 (100%) | 12 (40%) |
| `latencia_ms`, mediana | 9628 ms | 9 ms |

A conferência pode ser refeita com `curl "localhost:3000/monitor/variaveis?formato=csv"`
e uma planilha.

## 3. GET /metrics: como ler

`GET http://localhost:3000/metrics` é o endpoint que o próprio BentoML expõe, no
[formato de exposição do Prometheus](https://prometheus.io/docs/instrumenting/exposition_formats/).
Cada linha é `nome{rótulos} valor`. As séries que interessam:

| Série | Tipo | O que é |
|---|---|---|
| `bentoml_service_request_total{endpoint, http_response_code}` | counter | pedidos atendidos, por endpoint e código HTTP |
| `bentoml_service_request_duration_seconds_bucket{endpoint, http_response_code, le}` | histogram | quantos pedidos levaram **até** `le` segundos (acumulado) |
| `bentoml_service_request_duration_seconds_sum` / `_count` | histogram | soma dos tempos e número de pedidos |
| `bentoml_service_request_in_progress{endpoint}` | gauge | pedidos sendo atendidos agora |
| `leitura_status_total{status}` | counter (nosso) | respostas `OK` e `ILEGIVEL` |
| `leitura_confianca_geral_bucket{le}` | histogram (nosso) | distribuição da confiança geral |
| `leitura_nitidez_entrada_bucket{le}` | histogram (nosso) | distribuição da nitidez das fotos recebidas |

As três últimas são métricas próprias, criadas com `bentoml.metrics` em
[`service.py`](../service.py). Elas aparecem no mesmo `/metrics`.

**Tudo é acumulado desde que o processo subiu.** Contadores e buckets só crescem. Para
analisar um intervalo, subtrai-se uma leitura antiga (A) de uma nova (B). É isso que o
Prometheus faz com `rate()` e `increase()`, e é o que a planilha faz com fórmulas.

### Histograma da latência

Os buckets são acumulados: `le="10.0"` conta todos os pedidos com até 10 s, inclusive
os que já contaram em `le="5.0"`. O número de pedidos **em cada faixa** é a diferença
entre buckets vizinhos:

```
pedidos entre 5 e 10 s = bucket{le="10.0"} − bucket{le="5.0"}
total                  = bucket{le="+Inf"} = _count
média                  = _sum / _count
```

Os limites padrão do BentoML (0,005; 0,01; 0,025; 0,05; 0,1; 0,25; 0,5; 1; 2,5; 5; 10;
30; 60; 120; 180 s) pulam de 5 para 10 s e de 10 para 30 s, justo onde caem as fotos que
vão ao modelo (2 a 16 s nesta máquina). Na primeira janela medida, isso fez o p50
estimado dar 5,83 s, contra 9,33 s exatos no log. Por isso o serviço define os próprios
limites (`LATENCIA_S` em [`service.py`](../service.py), via `metrics.duration.buckets` da
configuração do serviço): 0,01; 0,025; 0,05; 0,1; 0,5 s; de 1 em 1 s entre 1 e 12 s;
de 2 em 2 s até 20 s; 30; 60; 180 s e +Inf. As faixas de até 50 ms separam as fotos
barradas na nitidez (~10 ms).

### Percentis e boxplot da latência

Para o percentil q, calcula-se a posição `q × total`, acha-se o primeiro bucket cujo
acumulado alcança essa posição e interpola-se linearmente dentro dele:

```
p_q ≈ limite_inferior + (limite_superior − limite_inferior)
                        × (q·total − acumulado_antes) / pedidos_no_bucket
```

É o mesmo cálculo do `histogram_quantile()` do Prometheus. O boxplot sai de Q1 (p25),
mediana (p50) e Q3 (p75), com bigodes em Q1 − 1,5·IQR e Q3 + 1,5·IQR.

**Limitação:** com buckets, só se sabe em que faixa cada pedido caiu. Mínimo, máximo e
outliers não são conhecidos, e a precisão depende da largura do bucket. Entre 20 e 30 s,
por exemplo, o erro pode chegar a 10 s. Para percentis e boxplot exatos, use o campo
`latencia_ms` do log, que é o que a Parte 2 faz.

### Taxa de erros

```
taxa de erro 5xx = Δ request_total{http_response_code=~"5.."} / Δ request_total (todos)
taxa de erro 4xx = Δ request_total{http_response_code=~"4.."} / Δ request_total (todos)
```

Erro 4xx é culpa do cliente (arquivo que não é imagem); 5xx é do servidor (Ollama fora
do ar, resposta do modelo fora do formato). Para SLA, normalmente só o 5xx conta.

### Planilha

[`docs/metrics/analise_metrics.xlsx`](../docs/metrics/analise_metrics.xlsx) faz tudo isso
com fórmulas, a partir de duas leituras reais do `/metrics` (`docs/metrics/snapshot_a.txt`
e `snapshot_b.txt`), já com as faixas novas. Entre elas foram 45 pedidos de carga (15 de
referência com `--semente 1` e 30 de produção com `--semente 2`) e 3 arquivos inválidos:

- **Latencia:** buckets A e B, pedidos por faixa, % acumulado, gráfico do histograma,
  média, p25/p50/p75/p90/p95/p99 e boxplot estimado.
- **Erros:** pedidos por código HTTP na janela e taxas de erro 4xx e 5xx.
- **Brutas:** todas as séries da leitura B.

Resultado dessa janela: 44 pedidos com HTTP 200 e média de 6,41 s. A taxa de erro 4xx
foi de 6,25% (3 de 48) e a de 5xx, de 2,08% (1 de 48): o Ollama devolveu 500 num pedido
de referência e o serviço respondeu 503, como previsto na tabela de erros. A distribuição
é bimodal: 18 pedidos levaram até 25 ms (fotos barradas na nitidez) e 24 levaram entre 9
e 11 s (fotos que foram ao modelo); os outros 2 levaram até 14 s e até 30 s.

| | estimado pelos buckets | exato pelo log (`latencia_ms`) |
|---|---|---|
| p50 | 9,44 s | 9,79 s |
| p75 | 10,40 s | 10,23 s |
| p95 | 10,99 s | 10,44 s |

Com as faixas padrão, o erro do p50 era de 3,5 s (5,83 contra 9,33 s); com as novas, é de
0,35 s. O que sobra vem da interpolação linear, que supõe os pedidos espalhados por igual
dentro de cada faixa de 1 s.

Para refazer com dados novos:

```bash
curl -s localhost:3000/metrics > docs/metrics/snapshot_a.txt
uv run just carga 20 producao
curl -s localhost:3000/metrics > docs/metrics/snapshot_b.txt
uv run just planilha
```

Com o Prometheus no ar (seção 4), as mesmas contas em PromQL, na janela dos últimos 15 min:

```promql
# histograma: pedidos por bucket
sum by (le) (increase(bentoml_service_request_duration_seconds_bucket{endpoint="/extract"}[15m]))
# p95
histogram_quantile(0.95, sum by (le) (rate(bentoml_service_request_duration_seconds_bucket{endpoint="/extract"}[15m])))
# taxa de erro 5xx
sum(rate(bentoml_service_request_total{endpoint="/extract",http_response_code=~"5.."}[15m]))
  / sum(rate(bentoml_service_request_total{endpoint="/extract"}[15m]))
```

### Por quanto tempo o /metrics guarda as informações

**No servidor, só até o serviço reiniciar.** O BentoML usa o modo multiprocesso do
`prometheus_client`: cada processo grava os contadores em arquivos na pasta
`prometheus_multiproc_dir`, dentro de `BENTOML_HOME` (por padrão `~/bentoml/`), e o
`/metrics` soma esses arquivos. Ao subir, o `bentoml serve` apaga essa pasta
(`ensure_prometheus_dir` em `bentoml/serving.py`), então tudo volta a zero. Testamos: depois de
reiniciar o `just serve`, `bentoml_service_request_total` do `/extract` voltou a 0. O
`/metrics` também não tem histórico, porque cada leitura mostra só o acumulado daquele
instante.

Para guardar histórico, alguém precisa ler o `/metrics` periodicamente e gravar. É o que
fazem os extras: o Prometheus (15 dias, configurável) e o DuckDB (sem limite). O log em
arquivo, por outro lado, fica no disco até ser apagado: até 11 arquivos de 10 MB pela
política de rotação.

## 4. Extras

Todos os extras, exceto o endpoint, precisam do Docker para a stack. A stack roda à parte
do serviço, que continua na máquina:

```bash
uv run just stack        # docker compose -f monitoring/stack/docker-compose.yml up -d
```

| Serviço | Endereço | Para quê |
|---|---|---|
| Prometheus | http://localhost:9090 | coleta e guarda o `/metrics` |
| Loki | http://localhost:3100 | guarda o log de pedidos |
| Alloy | (sem porta) | lê `logs/requisicoes.jsonl` e envia ao Loki |
| Grafana | http://localhost:3001 | telas para Prometheus e Loki (sem login) |

`uv run just stack-down` derruba a stack. Os dados ficam nos volumes do Docker; para apagar,
use `docker compose -f monitoring/stack/docker-compose.yml down -v`.

### Log no Loki (5 pts)

O [Alloy](https://grafana.com/docs/alloy/) acompanha o arquivo de log, inclusive os
girados, e envia cada linha nova ao Loki ([`stack/config.alloy`](stack/config.alloy)).
Ele usa o `ts` da linha como horário do evento e põe `endpoint` e `http_status` como
rótulos. O resto do JSON fica no corpo, para consulta.

Conferir: Grafana → Explore → Loki →
`{job="leitura_medidor"} | json | saida_status="ILEGIVEL"`. Pela API:

```bash
curl -sG localhost:3100/loki/api/v1/query_range --data-urlencode 'query={job="leitura_medidor"}'
```

### Endpoint que extrai as variáveis (20 pts)

`GET /monitor/variaveis` devolve o log achatado em colunas (`entrada.nitidez` vira
`entrada_nitidez`), pronto para histograma, boxplot, KS e PSI:

```bash
curl "localhost:3000/monitor/variaveis"                                   # JSON: {"total", "variaveis": [...]}
curl "localhost:3000/monitor/variaveis?formato=csv" > variaveis.csv        # CSV para planilha/pandas
curl "localhost:3000/monitor/variaveis?status=200&desde=2026-11-18T00:00:00-03:00"
```

| Parâmetro | Exemplo | Efeito |
|---|---|---|
| `desde`, `ate` | `2026-11-18T10:00:00-03:00` | filtra pelo `ts`; precisa do fuso (senão: 400) |
| `status` | `200` | só pedidos com esse código HTTP |
| `formato` | `csv` | CSV em vez de JSON |

Ele aparece no Swagger? Não: é um app Starlette montado em `/monitor`, fora das APIs
BentoML. A documentação é esta tabela.

### Prometheus coletando o /metrics (15 pts)

[`stack/prometheus.yml`](stack/prometheus.yml) lê `host.docker.internal:3000/metrics` a
cada 15 s. Para conferir, abra http://localhost:9090/targets: o alvo `leitura_medidor`
deve aparecer como **UP**. Em http://localhost:9090/query, rode as consultas PromQL da
seção 3.

### /metrics salvo no banco do Prometheus (10 pts)

O Prometheus grava cada coleta no TSDB, o banco de séries temporais dele, em `/prometheus`,
dentro do volume Docker `prometheus_dados`. Por isso os dados sobrevivem a
`stack-down`/`stack` e a reinícios do serviço, ao contrário do `/metrics`. A retenção é de
15 dias (`--storage.tsdb.retention.time=15d` no compose). Para conferir que o histórico
persiste:

```bash
uv run just stack-down && uv run just stack
# depois, em localhost:9090/query:
#   max_over_time(bentoml_service_request_total{endpoint="/extract",http_response_code="200"}[2h])
```

Testamos assim: depois de 55 pedidos, reiniciamos o serviço, e o `/metrics` voltou a 0.
Depois derrubamos e subimos a stack. A consulta acima continuou devolvendo 55, com 23
coletas guardadas.

Para tirar uma cópia do banco (API de admin ligada no compose):

```bash
curl -XPOST localhost:9090/api/v1/admin/tsdb/snapshot
# o snapshot fica em /prometheus/snapshots/<nome> dentro do volume
```

### /metrics salvo em banco relacional, DuckDB (5 pts)

[`metrics_para_duckdb.py`](metrics_para_duckdb.py) faz o que o Prometheus faz, mas num
banco SQL: a cada 15 s lê o `/metrics` e grava uma linha por série na tabela `amostras`
de `logs/metrics.duckdb`.

```bash
uv run just duckdb       # roda até Ctrl+C; --vezes 1 faz uma leitura só
```

```sql
create table amostras (ts timestamptz, metrica varchar, rotulos json, valor double);
```

Exemplo de consulta, com pedidos por código HTTP em cada leitura:

```bash
uv run python -c "import duckdb; print(duckdb.connect('logs/metrics.duckdb', read_only=True).sql(\"
  select ts::varchar as ts, rotulos->>'http_response_code' as codigo, valor
  from amostras where metrica = 'bentoml_service_request_total'
    and rotulos->>'endpoint' = '/extract' order by ts\").fetchall())"
```

## 5. Testes

`uv run just test` roda os 3 testes da AV1 e mais 2 desta parte. Os novos verificam que
cada pedido, inclusive com erro, vira exatamente uma linha nova no log, com fuso no `ts`,
e que o `/monitor/variaveis` filtra, achata e devolve CSV. O `/metrics` não entra nos
testes, porque é servido pelo `bentoml serve` e não pelo app em processo dos testes. Ele
foi conferido com o serviço no ar.

## 6. Limitações

- **As fotos de carga são variações das 5 fotos de `examples/`.** São dados simulados, o
  que o enunciado permite. Os 5 tipos de medidor se repetem; a variedade está na
  degradação.
- **Um processo só.** O `RotatingFileHandler` não é seguro com vários processos
  escrevendo no mesmo arquivo. O serviço roda com 1 worker (padrão do BentoML). Para
  escalar, cada worker precisaria do próprio arquivo, ou o log iria direto para o Loki.
- **O `docker-compose.yml` foi testado só no Docker Desktop para Windows.** No Linux,
  `host.docker.internal` depende do `extra_hosts` já incluído.
