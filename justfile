# Uso: uv run just <receita>   (o just vem como dependência de desenvolvimento)

set dotenv-load := true

modelo := if env("OLLAMA_MODEL", "") == "" { "qwen2.5vl:3b" } else { env("OLLAMA_MODEL") }

# Lista as receitas
default:
    @just --list

# Instala o ambiente exatamente como no uv.lock
setup:
    uv sync --frozen

# Baixa o modelo no Ollama (precisa de internet e do Ollama rodando)
model:
    ollama pull {{modelo}}

# Sobe o serviço em http://localhost:3000 (Swagger na raiz)
serve:
    uv run bentoml serve service:LeituraMedidor --port 3000

# Roda os testes (precisa do Ollama rodando com o modelo baixado)
test:
    uv run pytest -v

# Envia uma foto ao serviço já no ar e mostra a resposta
demo foto="examples/example02.jpg":
    curl -s -F "image=@{{foto}}" http://localhost:3000/extract | uv run python -m json.tool --no-ensure-ascii

# --- Monitoramento (ver monitoring/README.md) ---

# Gera carga no /extract: just carga 20 referencia | just carga 50 producao
carga n="20" perfil="referencia":
    uv run python -m monitoring.gerar_carga --n {{n}} --perfil {{perfil}}

# Acompanha o log de pedidos ao vivo (Ctrl+C para sair)
rastro:
    tail -f logs/requisicoes.jsonl

# Sobe Prometheus, Loki, Alloy e Grafana (precisa do Docker)
stack:
    docker compose -f monitoring/stack/docker-compose.yml up -d

# Derruba a stack (os dados ficam nos volumes)
stack-down:
    docker compose -f monitoring/stack/docker-compose.yml down

# Grava o /metrics no DuckDB a cada 15 s (Ctrl+C para sair)
duckdb:
    uv run python -m monitoring.metrics_para_duckdb

# Monta a planilha de análise do /metrics a partir de duas leituras
planilha a="docs/metrics/snapshot_a.txt" b="docs/metrics/snapshot_b.txt":
    uv run python -m monitoring.planilha_metrics {{a}} {{b}}

# Confere de ponta a ponta o log, o /metrics e todos os extras (serviço e stack no ar)
verificar:
    uv run python -m monitoring.verificar
