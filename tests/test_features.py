"""Variáveis de entrada da atividade de características (não precisam de Ollama)."""

from PIL import Image

from monitoring.features import features_imagem


def test_foto_cinza_tem_croma_zero_e_colorida_tem_croma_alta():
    assert features_imagem(Image.new("RGB", (100, 100), (128, 128, 128)))["croma_media"] == 0
    assert features_imagem(Image.new("RGB", (100, 100), (200, 30, 30)))["croma_media"] > 50


def test_qualidade_tonal_classifica_escura_estourada_e_lavada():
    assert features_imagem(Image.new("RGB", (50, 50), (5, 5, 5)))["qualidade_tonal"] == "SUBEXPOSTA"
    assert features_imagem(Image.new("RGB", (50, 50), (250, 250, 250)))["qualidade_tonal"] == "SUPEREXPOSTA"
    assert features_imagem(Image.new("RGB", (50, 50), (128, 128, 128)))["qualidade_tonal"] == "BAIXO_CONTRASTE"


def test_nitidez_512_nao_depende_do_tamanho_como_a_nitidez():
    foto = Image.open("examples/example02.jpg")
    grande = foto.resize((foto.width * 3, foto.height * 3))
    a, b = features_imagem(foto)["nitidez_512"], features_imagem(grande)["nitidez_512"]
    assert abs(a - b) / a < 0.35
