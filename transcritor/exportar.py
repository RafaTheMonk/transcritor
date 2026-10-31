"""Formatos de saida: txt, srt, vtt e json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FORMATOS = ("txt", "srt", "vtt", "json")


@dataclass(frozen=True)
class Segmento:
    inicio: float
    fim: float
    texto: str


def json_seguro(dados, **opcoes) -> str:
    """JSON que sempre cabe em UTF-8. Nome de arquivo que nao e UTF-8 vira escape do JSON."""
    texto = json.dumps(dados, ensure_ascii=False, **opcoes)
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        texto = json.dumps(dados, ensure_ascii=True, **opcoes)
    return texto


def _tempo(segundos: float, sep: str) -> str:
    ms = max(0, int(round(segundos * 1000)))
    h, resto = divmod(ms, 3_600_000)
    m, resto = divmod(resto, 60_000)
    s, ms = divmod(resto, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _linha(texto: str) -> str:
    return " ".join(texto.split())


def para_txt(segmentos: list[Segmento]) -> str:
    return "".join(_linha(s.texto) + "\n" for s in segmentos)


def para_srt(segmentos: list[Segmento]) -> str:
    blocos = []
    for i, s in enumerate(segmentos, 1):
        blocos.append(f"{i}\n{_tempo(s.inicio, ',')} --> {_tempo(s.fim, ',')}\n{_linha(s.texto)}\n")
    return "\n".join(blocos)


def para_vtt(segmentos: list[Segmento]) -> str:
    blocos = ["WEBVTT\n"]
    for s in segmentos:
        texto = _linha(s.texto).replace("-->", "->")
        blocos.append(f"{_tempo(s.inicio, '.')} --> {_tempo(s.fim, '.')}\n{texto}\n")
    return "\n".join(blocos)


def para_json(segmentos: list[Segmento], metadados: dict) -> str:
    dados = dict(metadados)
    dados["segmentos"] = [
        {"inicio": round(s.inicio, 3), "fim": round(s.fim, 3), "texto": s.texto.strip()}
        for s in segmentos
    ]
    return json_seguro(dados, indent=2) + "\n"


def gravar(pasta: Path, nome: str, segmentos: list[Segmento], formatos, metadados: dict) -> dict[str, Path]:
    """Escreve os formatos pedidos dentro de 'pasta'. Devolve formato -> caminho."""
    geradores = {
        "txt": lambda: para_txt(segmentos),
        "srt": lambda: para_srt(segmentos),
        "vtt": lambda: para_vtt(segmentos),
        "json": lambda: para_json(segmentos, metadados),
    }
    saida = {}
    for f in formatos:
        caminho = pasta / f"{nome}.{f}"
        caminho.write_text(geradores[f](), encoding="utf-8")
        saida[f] = caminho
    return saida
