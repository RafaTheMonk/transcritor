import json

from transcritor import exportar
from transcritor.exportar import Segmento

SEGS = [Segmento(0.0, 1.5, "ola  mundo"), Segmento(3661.007, 3662.0, "fim\ncom quebra --> seta")]


def test_srt():
    s = exportar.para_srt(SEGS)
    assert s.startswith("1\n00:00:00,000 --> 00:00:01,500\nola mundo\n\n2\n")
    assert "01:01:01,007 --> 01:01:02,000" in s


def test_vtt():
    v = exportar.para_vtt(SEGS)
    assert v.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nola mundo\n")
    assert "01:01:01.007" in v and "fim com quebra -> seta" in v


def test_txt_uma_linha_por_segmento_sem_tempo():
    assert exportar.para_txt(SEGS) == "ola mundo\nfim com quebra --> seta\n"


def test_json_metadados_e_segmentos():
    d = json.loads(exportar.para_json(SEGS, {"modelo": "small", "idioma": "pt"}))
    assert d["modelo"] == "small" and len(d["segmentos"]) == 2
    assert d["segmentos"][0] == {"inicio": 0.0, "fim": 1.5, "texto": "ola  mundo"}


def test_arredondamento_nao_gera_1000_ms():
    assert exportar._tempo(0.9996, ",") == "00:00:01,000"
