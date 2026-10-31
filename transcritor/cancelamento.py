"""Cancelamento: registro de processos filhos e pastas temporarias.

O tratador de sinal mata o ffmpeg filho, apaga as pastas temporarias e levanta
Cancelado na thread principal.

O Python so roda o tratador quando a thread principal volta para o interpretador.
Se ela estiver presa em codigo nativo (o modelo carregando ou transcrevendo), o
tratador nao roda. Por isso uma thread vigia le o byte que o proprio sinal escreve
no wakeup fd e, passado o prazo, limpa tudo e forca a saida com 130.
"""

from __future__ import annotations

import ctypes
import os
import select
import shutil
import signal
import subprocess
import threading
from pathlib import Path
from typing import Callable

ATRASO_SAIDA_FORCADA = 5.0
PR_SET_PDEATHSIG = 1

try:
    # carregada aqui, e nao no filho depois do fork, onde abrir biblioteca nao e seguro
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
except OSError:
    _libc = None


class Cancelado(BaseException):
    """Derivada de BaseException para nao ser engolida por 'except Exception'."""


# RLock: o tratador de sinal roda na mesma thread que pode estar segurando a trava
_trava = threading.RLock()
_processos: set[subprocess.Popen] = set()
_pastas: set[Path] = set()
_cancelando = False
_ao_forcar: Callable[[], None] | None = None


def morrer_com_o_pai(sinal: int) -> None:
    """Pede ao kernel que mande 'sinal' a este processo quando o pai morrer (so Linux)."""
    if _libc is not None:
        _libc.prctl(PR_SET_PDEATHSIG, int(sinal))


def registrar_processo(p: subprocess.Popen) -> None:
    with _trava:
        _processos.add(p)


def liberar_processo(p: subprocess.Popen) -> None:
    with _trava:
        _processos.discard(p)


def registrar_pasta(p: Path) -> None:
    with _trava:
        _pastas.add(Path(p))


def liberar_pasta(p: Path) -> None:
    with _trava:
        _pastas.discard(Path(p))


def encerrar_processos() -> None:
    with _trava:
        vivos = list(_processos)
    for p in vivos:
        try:
            if p.poll() is None:
                p.kill()
        except OSError:
            pass
    for p in vivos:
        try:
            p.wait(timeout=5)
        except Exception:
            pass


def apagar_pastas() -> None:
    with _trava:
        pastas = list(_pastas)
        _pastas.clear()
    for pasta in pastas:
        shutil.rmtree(pasta, ignore_errors=True)


def encerrar_tudo() -> None:
    encerrar_processos()
    apagar_pastas()


def _forcar_saida() -> None:
    encerrar_tudo()
    if _ao_forcar is not None:
        try:
            _ao_forcar()
        except Exception:
            pass
    os._exit(130)


def _vigiar(leitura: int) -> None:
    os.read(leitura, 1)
    # um segundo sinal encurta a espera
    select.select([leitura], [], [], ATRASO_SAIDA_FORCADA)
    _forcar_saida()


def _tratador(signum, frame):
    global _cancelando
    if _cancelando:
        _forcar_saida()
    _cancelando = True
    encerrar_tudo()
    raise Cancelado()


def instalar_tratadores(ao_forcar: Callable[[], None] | None = None) -> None:
    """'ao_forcar' roda na thread vigia, logo antes da saida forcada."""
    global _ao_forcar
    _ao_forcar = ao_forcar
    leitura, escrita = os.pipe()
    os.set_blocking(escrita, False)
    signal.set_wakeup_fd(escrita, warn_on_full_buffer=False)
    threading.Thread(target=_vigiar, args=(leitura,), daemon=True).start()
    signal.signal(signal.SIGTERM, _tratador)
    signal.signal(signal.SIGINT, _tratador)


def ignorar_sinais() -> None:
    """Trabalho terminado: um sinal agora nao tem o que cancelar."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
