"""Testes do endpoint POST /extract, chamando o app BentoML em processo (sem subir servidor)."""

import io

import pytest
from PIL import Image, ImageFilter
from starlette.testclient import TestClient

from extractor import ollama_pronto
from monitoring.registro import ler_eventos
from service import LeituraMedidor

FOTO_BOA = "examples/example02.jpg"


@pytest.fixture(scope="module")
def cliente():
    with TestClient(LeituraMedidor.to_asgi()) as c:
        yield c


def enviar(cliente, conteudo: bytes, nome: str = "foto.jpg"):
    return cliente.post("/extract", files={"image": (nome, conteudo)})


@pytest.mark.skipif(not ollama_pronto(), reason="Ollama fora do ar ou modelo não baixado")
def test_foto_boa_extrai_leitura(cliente):
    with open(FOTO_BOA, "rb") as f:
        resposta = enviar(cliente, f.read())

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "OK"
    assert corpo["consumo"] == 1385
    assert corpo["confianca"]["geral"] > 0


def test_foto_ruim_devolve_ilegivel(cliente):
    # A mesma foto boa, desfocada até o display ficar ilegível.
    borrada = Image.open(FOTO_BOA).filter(ImageFilter.GaussianBlur(4))
    buffer = io.BytesIO()
    borrada.save(buffer, format="JPEG")

    resposta = enviar(cliente, buffer.getvalue())

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ILEGIVEL"
    assert corpo["numero_medidor"] is None
    assert corpo["funcao"] is None
    assert corpo["consumo"] is None
    assert corpo["confianca"]["geral"] == 0


def test_entrada_invalida_devolve_400(cliente):
    resposta = enviar(cliente, b"isto nao e uma imagem", nome="falsa.jpg")

    assert resposta.status_code == 400
    assert "não é uma imagem válida" in resposta.text


def test_cada_pedido_vira_uma_linha_no_log(cliente):
    antes = len(ler_eventos())
    borrada = Image.open(FOTO_BOA).filter(ImageFilter.GaussianBlur(4))
    buffer = io.BytesIO()
    borrada.save(buffer, format="JPEG")
    enviar(cliente, buffer.getvalue())
    enviar(cliente, b"isto nao e uma imagem")

    eventos = ler_eventos()
    assert len(eventos) == antes + 2  # só acrescenta, nunca reescreve
    ok, erro = eventos[-2], eventos[-1]
    assert ok["http_status"] == 200
    assert ok["entrada"]["largura"] == 480
    assert ok["entrada"]["nitidez"] < 300 and ok["chamou_modelo"] is False
    assert ok["saida"]["status"] == "ILEGIVEL"
    assert "+" in ok["ts"] or "-" in ok["ts"][19:]  # horário com fuso
    assert erro["http_status"] == 400 and "erro" in erro


def test_monitor_variaveis_devolve_colunas_achatadas(cliente):
    enviar(cliente, b"isto nao e uma imagem")

    corpo = cliente.get("/monitor/variaveis", params={"status": "400"}).json()
    assert corpo["total"] >= 1
    assert all(linha["http_status"] == 400 for linha in corpo["variaveis"])
    assert "entrada_bytes" in corpo["variaveis"][-1]

    csv = cliente.get("/monitor/variaveis", params={"formato": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("ts,")

    assert cliente.get("/monitor/variaveis", params={"desde": "ontem"}).status_code == 400
