#!/usr/bin/env python3
"""CLI falsa para testar a interface. O cenario vem do nome do arquivo de entrada.

ok, erro, aviso, recuo, morte, crashfinal, lixo, partida, lento, pausa, surdo (ignora SIGTERM)
Variaveis: FALSA_LOG (grava os argumentos), FALSA_SAIDA (onde cria o .txt).
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

args = sys.argv[1:]
arq = args[-1]
nome = Path(arq).stem
if os.environ.get("FALSA_LOG"):
    with open(os.environ["FALSA_LOG"], "a") as f:
        f.write(json.dumps(args) + "\n")


def emitir(ev, parcial=False):
    ev.setdefault("arquivo", arq)
    sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def etapa(e):
    emitir({"tipo": "etapa", "etapa": e})


def preludio():
    etapa("analisando")
    emitir({"tipo": "midia", "eh_video": True, "duracao": 12.0, "codec_audio": "aac", "canais": 2, "faixas_audio": 1})
    etapa("carregando_modelo")
    emitir({"tipo": "dispositivo", "dispositivo": "cuda", "compute_type": "int8_float16", "modelo": "large-v3-turbo"})
    emitir({"tipo": "idioma", "idioma": "pt", "probabilidade": 0.98})
    etapa("transcrevendo")


def fim():
    etapa("salvando")
    pasta = Path(os.environ.get("FALSA_SAIDA", "/tmp")) / nome
    pasta.mkdir(parents=True, exist_ok=True)
    txt = pasta / f"{nome}.txt"
    txt.write_text("ola mundo\nsegundo trecho\n", encoding="utf-8")
    emitir({"tipo": "fim", "pasta": str(pasta), "arquivos": {"txt": str(txt)}, "duracao_audio": 12.0,
            "tempo_processamento": 1.5, "vazio": False})


def segs(n=2):
    for i, t in enumerate(["ola mundo", "segundo trecho"][:n]):
        emitir({"tipo": "segmento", "inicio": i, "fim": i + 1, "texto": t, "progresso": (i + 1) / 2})


if "surdo" in nome:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
elif "lento" in nome or "pausa" in nome:
    def term(*_):
        emitir({"tipo": "erro", "codigo": "cancelado", "mensagem": "Cancelado pelo usuario."})
        sys.exit(130)
    signal.signal(signal.SIGTERM, term)

if "erro" in nome:
    etapa("analisando")
    emitir({"tipo": "erro", "codigo": "sem_audio", "mensagem": "O arquivo nao tem faixa de audio."})
    sys.exit(1)
if "recuo" in nome:
    preludio()
    segs(1)
    emitir({"tipo": "aviso", "mensagem": "A GPU falhou."})
    emitir({"tipo": "dispositivo", "dispositivo": "cpu", "compute_type": "int8", "modelo": "large-v3-turbo"})
    emitir({"tipo": "segmento", "inicio": 0, "fim": 1, "texto": "refeito em cpu", "progresso": 0.5})
    time.sleep(0.5)
    sys.exit(1)
if "morte" in nome:
    preludio()
    sys.stderr.write("linha1\nSegmentation fault real\n")
    sys.stderr.flush()
    os.kill(os.getpid(), signal.SIGKILL)
if "crashfinal" in nome:
    preludio(); segs(); fim()
    os.kill(os.getpid(), signal.SIGSEGV)
if "lixo" in nome:
    sys.stdout.write("isto nao e json\n[1, 2]\n{quebrado\n")
    sys.stdout.flush()
    preludio(); segs(); fim(); sys.exit(0)
if "partida" in nome:
    preludio()
    linha = json.dumps({"tipo": "segmento", "inicio": 0, "fim": 1, "texto": "linha partida ao meio", "progresso": 0.5, "arquivo": arq}) + "\n"
    meio = len(linha) // 2
    sys.stdout.write(linha[:meio]); sys.stdout.flush()
    time.sleep(0.4)
    sys.stdout.write(linha[meio:]); sys.stdout.flush()
    time.sleep(0.5)
    fim(); sys.exit(0)
if "aviso" in nome:
    preludio()
    emitir({"tipo": "aviso", "mensagem": "A GPU falhou, usando CPU."})
    segs(); fim(); sys.exit(0)
if "pausa" in nome or "lento" in nome or "surdo" in nome:
    preludio()
    segs(1)
    while True:
        time.sleep(0.05)
preludio(); segs(); fim()
