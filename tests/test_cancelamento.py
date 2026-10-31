"""Sinais e morte de processos, com processos e ffmpeg de verdade (sem modelo)."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import MotorFalso, ff
from transcritor.pipeline import Opcoes, Pipeline

RAIZ = Path(__file__).resolve().parent.parent
LANCADOR = str(RAIZ / "bin" / "transcrever")
ENV = {**os.environ, "PYTHONPATH": str(RAIZ)}


@pytest.fixture(scope="module")
def longo(tmp_path_factory):
    """Audio ac3 obriga a reencodar: a extracao demora o bastante para mandar o sinal."""
    arq = tmp_path_factory.mktemp("longo") / "longo.mkv"
    ff("-f", "lavfi", "-i", "testsrc=size=64x48:rate=1:duration=3600",
       "-f", "lavfi", "-i", "sine=frequency=300:duration=3600", "-c:v", "mpeg4", "-c:a", "ac3", str(arq))
    return arq


def iniciar(arq, saida, *extra):
    return subprocess.Popen(
        [LANCADOR, str(arq), "--json-eventos", "--saida", str(saida), "--modelo", "small", "--dispositivo", "cpu", *extra],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def ate_extrair(p):
    eventos = []
    for linha in p.stdout:
        eventos.append(json.loads(linha))
        if eventos[-1].get("etapa") == "extraindo":
            return eventos
    raise AssertionError(f"nao chegou a extrair: {eventos} {p.stderr.read()}")


def ffmpeg_de(saida):
    r = subprocess.run(["pgrep", "-af", "ffmpeg"], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if str(saida) in l]


def esperar_sem_ffmpeg(saida, prazo=5):
    fim = time.monotonic() + prazo
    while ffmpeg_de(saida) and time.monotonic() < fim:
        time.sleep(0.05)
    return ffmpeg_de(saida)


def test_sigterm_na_extracao(longo, tmp_path):
    saida = tmp_path / "o"
    p = iniciar(longo, saida)
    ate_extrair(p)
    time.sleep(0.3)
    assert ffmpeg_de(saida)
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=15) == 130
    ultimo = json.loads(p.stdout.read().splitlines()[-1])
    assert ultimo["codigo"] == "cancelado"
    assert ultimo["arquivo"] == str(longo)  # contrato: arquivo em andamento vai no evento
    assert esperar_sem_ffmpeg(saida) == []
    assert list(saida.iterdir()) == []
    assert "Traceback" not in p.stderr.read()


def test_sigkill_na_extracao_nao_deixa_ffmpeg_e_a_proxima_rodada_limpa(longo, midias, tmp_path):
    saida = tmp_path / "o"
    p = iniciar(longo, saida)
    ate_extrair(p)
    time.sleep(0.3)
    assert ffmpeg_de(saida)
    p.kill()
    p.wait(timeout=5)
    assert esperar_sem_ffmpeg(saida) == []
    sobras = [x for x in saida.iterdir() if x.name.startswith(".transcritor-tmp-")]
    assert len(sobras) == 1
    os.utime(sobras[0], (time.time() - 60, time.time() - 60))
    pl = Pipeline(Opcoes(saida=saida), lambda e: None, lambda cfg, b: MotorFalso(cfg), lambda: False)
    assert pl.processar([str(midias["wav"])]) == 0
    assert sorted(x.name for x in saida.iterdir()) == ["som"]


def test_rodada_viva_nao_perde_o_temporario_para_outra(longo, midias, tmp_path):
    saida = tmp_path / "o"
    p = iniciar(longo, saida)
    ate_extrair(p)
    time.sleep(0.3)
    viva = [x for x in saida.iterdir() if x.name.startswith(".transcritor-tmp-")][0]
    os.utime(viva, (time.time() - 60, time.time() - 60))
    pl = Pipeline(Opcoes(saida=saida), lambda e: None, lambda cfg, b: MotorFalso(cfg), lambda: False)
    assert pl.processar([str(midias["wav"])]) == 0
    assert viva.exists()
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=15) == 130
    assert sorted(x.name for x in saida.iterdir()) == ["som"]


def test_janela_morta_com_sigkill_encerra_a_cli(longo, tmp_path):
    """O pai que le os eventos morre: a CLI cancela, mata o ffmpeg e limpa."""
    saida = tmp_path / "o"
    pai = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys, time\n"
         "p = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE)\n"
         "print(p.pid, flush=True)\n"
         "time.sleep(600)\n",
         LANCADOR, str(longo), "--json-eventos", "--saida", str(saida), "--modelo", "small", "--dispositivo", "cpu"],
        stdout=subprocess.PIPE, text=True,
    )
    filho = int(pai.stdout.readline())
    fim = time.monotonic() + 15
    while not ffmpeg_de(saida) and time.monotonic() < fim:
        time.sleep(0.05)
    assert ffmpeg_de(saida)
    pai.kill()
    pai.wait()
    fim = time.monotonic() + 8
    while Path(f"/proc/{filho}").exists() and time.monotonic() < fim:
        time.sleep(0.05)
    assert not Path(f"/proc/{filho}").exists()
    assert esperar_sem_ffmpeg(saida) == []
    assert list(saida.iterdir()) == []


@pytest.mark.parametrize("modo", ["--json-eventos", None])
def test_quem_le_a_saida_foi_embora(midias, tmp_path, modo):
    """Pipe fechado: sai com 141, limpa o temporario e nao mostra traceback."""
    saida = tmp_path / "o"
    args = [LANCADOR, str(midias["video_aac"]), "--saida", str(saida), "--modelo", "small", "--dispositivo", "cpu"]
    if modo:
        args.append(modo)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdout.readline()
    p.stdout.close()
    assert p.wait(timeout=60) == 141
    erro = p.stderr.read().decode()
    assert "Traceback" not in erro and "BrokenPipe" not in erro
    assert not saida.exists() or list(saida.iterdir()) == []


def test_sinal_preso_em_codigo_nativo_forca_a_saida():
    """pbkdf2 solta o GIL e nao volta para o interpretador, como o modelo transcrevendo."""
    codigo = (
        "import hashlib\n"
        "from transcritor import cancelamento as c\n"
        "c.ATRASO_SAIDA_FORCADA = 1.0\n"
        "c.instalar_tratadores(lambda: print('forcou', flush=True))\n"
        "print('pronto', flush=True)\n"
        "hashlib.pbkdf2_hmac('sha256', b'x', b'y', 2_000_000_000)\n"
    )
    p = subprocess.Popen([sys.executable, "-P", "-c", codigo], stdout=subprocess.PIPE, text=True, env=ENV)
    assert p.stdout.readline().strip() == "pronto"
    p.send_signal(signal.SIGTERM)
    t0 = time.monotonic()
    assert p.wait(timeout=15) == 130
    assert time.monotonic() - t0 < 6
    assert p.stdout.readline().strip() == "forcou"


def test_sinal_com_a_trava_na_mao_nao_trava_o_processo():
    codigo = (
        "import os, signal\n"
        "from transcritor import cancelamento as c\n"
        "c.instalar_tratadores()\n"
        "try:\n"
        "    with c._trava:\n"
        "        os.kill(os.getpid(), signal.SIGTERM)\n"
        "        signal.pause()\n"
        "except c.Cancelado:\n"
        "    print('cancelado', flush=True)\n"
    )
    r = subprocess.run([sys.executable, "-P", "-c", codigo], capture_output=True, text=True, env=ENV, timeout=10)
    assert r.stdout.strip() == "cancelado"
