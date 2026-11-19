"""Monta o conjunto de referência: referencia/referencia.jsonl.

O qwen2.5vl:3b não foi treinado por nós e não temos o conjunto de treino dele, então a
referência é definida à mão: as fotos de examples/ com as variações leves do perfil
`referencia` do gerador de carga, passadas pelo próprio serviço com o modelo real. Assim
a referência tem o mesmo formato do log de produção, entrada e saída, e é com ela que a
Parte 2 compara a produção (KS e PSI).

O serviço roda em processo e grava num log temporário, para não misturar a referência
com logs/requisicoes.jsonl. Só os pedidos com HTTP 200 entram.

Uso: uv run python -m monitoring.gerar_referencia --n 100   (~10 s por foto)
"""

import argparse
import json
import os
import random
import shutil
import tempfile
from pathlib import Path

REFERENCIA = Path("referencia") / "referencia.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--semente", type=int, default=7)
    args = ap.parse_args()

    pasta = tempfile.mkdtemp(prefix="referencia_")
    os.environ["MONITOR_LOG_DIR"] = pasta  # antes de importar o serviço
    from PIL import Image
    from starlette.testclient import TestClient

    from monitoring.gerar_carga import variar
    from monitoring.registro import ler_eventos
    from service import LeituraMedidor

    fotos = [Image.open(p) for p in sorted(Path("examples").iterdir())
             if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    rnd = random.Random(args.semente)
    with TestClient(LeituraMedidor.to_asgi()) as cliente:
        for i in range(args.n):
            conteudo = variar(rnd.choice(fotos), "referencia", rnd)
            r = cliente.post("/extract", files={"image": (f"ref_{i:04d}.jpg", conteudo)})
            print(f"[{i + 1}/{args.n}] HTTP {r.status_code}", flush=True)

    eventos = [e for e in ler_eventos() if e.get("http_status") == 200]
    REFERENCIA.parent.mkdir(exist_ok=True)
    with open(REFERENCIA, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in eventos)
    shutil.rmtree(pasta, ignore_errors=True)
    print(f"{len(eventos)} de {args.n} pedidos com HTTP 200 em {REFERENCIA}")


if __name__ == "__main__":
    main()
