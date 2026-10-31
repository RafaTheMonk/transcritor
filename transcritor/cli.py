"""Linha de comando: transcrever ARQUIVO [ARQUIVO ...]."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from . import cancelamento, pipeline
from .cancelamento import Cancelado
from .exportar import FORMATOS, json_seguro
from .gpu import MODELOS

ETAPAS = {
    "analisando": "Analisando o arquivo",
    "extraindo": "Extraindo o áudio",
    "carregando_modelo": "Carregando o modelo",
    "baixando_modelo": "Baixando o modelo (só na primeira vez)",
    "transcrevendo": "Transcrevendo",
    "salvando": "Salvando",
}


# Os codigos que o Whisper aceita. Um teste confere com a lista do faster-whisper.
IDIOMAS = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw he hi hr ht "
    "hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl "
    "ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo yue zh".split()
)
SAIDA_PIPE_FECHADO = 141


def _pipe_fechado() -> None:
    """Quem lia a saida foi embora: limpa o que esta em andamento e sai, sem traceback."""
    cancelamento.encerrar_tudo()
    os._exit(SAIDA_PIPE_FECHADO)


def _formatos(valor: str) -> tuple[str, ...]:
    itens = [x.strip().lower() for x in valor.split(",") if x.strip()]
    ruins = [x for x in itens if x not in FORMATOS]
    if not itens or ruins:
        raise argparse.ArgumentTypeError(f"formatos válidos: {','.join(FORMATOS)}")
    return tuple(dict.fromkeys(itens))


def _idioma(valor: str) -> str:
    v = valor.strip().lower()
    if v != "auto" and v not in IDIOMAS:
        raise argparse.ArgumentTypeError(f"idioma '{valor}' desconhecido, use um código como pt, en, es, ou auto")
    return v


def montar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="transcrever",
        description="Transcreve vídeo ou áudio localmente com faster-whisper.",
    )
    p.add_argument("arquivos", nargs="+", metavar="ARQUIVO", help="vídeo ou áudio")
    p.add_argument("--modelo", choices=("auto", *MODELOS), default="auto")
    p.add_argument("--idioma", type=_idioma, default="pt", help="código (pt, en...) ou auto. Padrão: pt")
    p.add_argument("--dispositivo", choices=("auto", "cuda", "cpu"), default="auto")
    p.add_argument("--saida", type=Path, default=None, help="pasta das transcrições. Padrão: Documentos/Transcricoes")
    p.add_argument("--faixa", type=int, default=None, help="faixa de áudio, a partir de 0. Padrão: a marcada como padrão")
    p.add_argument("--formatos", type=_formatos, default=FORMATOS, help="lista separada por vírgula: txt,srt,vtt,json")
    p.add_argument("--sem-salvar-audio", action="store_true", help="não guarda o áudio extraído")
    p.add_argument("--json-eventos", action="store_true", help="eventos NDJSON em stdout")
    return p


class EmissorNDJSON:
    def __init__(self, fluxo):
        self._f = fluxo

    def __call__(self, ev: dict) -> None:
        try:
            self._f.write(json_seguro(ev) + "\n")
            self._f.flush()
        except BrokenPipeError:
            _pipe_fechado()


class EmissorHumano:
    def __init__(self, total_arquivos: int):
        self._total = total_arquivos
        self._n = 0
        self._ultimo_pct = -1
        self._tty = sys.stdout.isatty()
        self._linha_aberta = False

    def _fechar_linha(self) -> None:
        if self._linha_aberta:
            print()
            self._linha_aberta = False

    def __call__(self, ev: dict) -> None:
        try:
            self._mostrar(ev)
        except BrokenPipeError:
            _pipe_fechado()

    def _mostrar(self, ev: dict) -> None:
        t = ev["tipo"]
        if t == "etapa":
            self._fechar_linha()
            if ev["etapa"] == "analisando":
                self._n += 1
                self._ultimo_pct = -1
                print(f"\n[{self._n}/{self._total}] {ev.get('arquivo', '')}")
            print(f"  {ETAPAS.get(ev['etapa'], ev['etapa'])}...")
        elif t == "midia":
            tipo = "vídeo" if ev["eh_video"] else "áudio"
            print(f"  {tipo}, {ev['duracao']:.1f}s, áudio {ev['codec_audio']} com {ev['canais']} canal(is)")
        elif t == "dispositivo":
            print(f"  Usando {ev['dispositivo']} com o modelo {ev['modelo']} ({ev['compute_type']})")
        elif t == "idioma":
            print(f"  Idioma: {ev['idioma']} ({ev['probabilidade'] * 100:.0f}%)")
        elif t == "aviso":
            self._fechar_linha()
            print(f"  Aviso: {ev['mensagem']}")
        elif t == "segmento":
            pct = int(ev["progresso"] * 100)
            if self._tty:
                print(f"\r  Progresso: {pct}%", end="", flush=True)
                self._linha_aberta = True
            elif pct // 10 != self._ultimo_pct // 10:
                print(f"  Progresso: {pct}%", flush=True)
            self._ultimo_pct = pct
        elif t == "fim":
            self._fechar_linha()
            if ev["vazio"]:
                print("  Concluído, sem fala detectada.")
            else:
                print(f"  Pronto em {ev['tempo_processamento']:.1f}s: {ev['pasta']}")
        elif t == "erro":
            self._fechar_linha()
            print(f"  ERRO ({ev['codigo']}): {ev['mensagem']}", file=sys.stderr)
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = montar_parser().parse_args(argv)

    if args.json_eventos:
        # stdout fica so para eventos: o fd 1 original vai para um fluxo proprio
        # e o fd 1 passa a apontar para o stderr, inclusive para codigo nativo.
        fluxo = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        emitir = EmissorNDJSON(fluxo)
    else:
        sys.stdout.reconfigure(errors="backslashreplace")  # nome de arquivo que nao e UTF-8
        emitir = EmissorHumano(len(args.arquivos))

    opcoes = pipeline.Opcoes(
        saida=(args.saida or pipeline.pasta_saida_padrao()).expanduser().resolve(),
        modelo=args.modelo,
        idioma=args.idioma,
        dispositivo=args.dispositivo,
        faixa=args.faixa,
        formatos=args.formatos,
        salvar_audio=not args.sem_salvar_audio,
    )
    trabalho = pipeline.Pipeline(opcoes, emitir)
    cancelamento.instalar_tratadores(trabalho.emitir_cancelado)
    if args.json_eventos:
        # quem le os eventos morreu (mesmo com SIGKILL): cancela como um SIGTERM
        cancelamento.morrer_com_o_pai(signal.SIGTERM)
    try:
        codigo = trabalho.processar(args.arquivos)
        cancelamento.ignorar_sinais()
        return codigo
    except Cancelado:
        cancelamento.encerrar_tudo()
        trabalho.emitir_cancelado()
        cancelamento.ignorar_sinais()
        return 130


if __name__ == "__main__":
    sys.exit(main())
