"""Orquestra um lote de arquivos e emite eventos (contrato NDJSON da CLI).

Eventos (sempre com "arquivo" quando ha arquivo em andamento):
  etapa       analisando | extraindo | carregando_modelo | baixando_modelo | transcrevendo | salvando
  midia       eh_video, duracao, codec_audio, canais, faixas_audio
  dispositivo dispositivo, compute_type, modelo. Se chegar de novo no meio de um arquivo,
              a GPU falhou: descarte os segmentos recebidos, a transcricao recomecou em CPU.
  idioma      idioma, probabilidade
  aviso       mensagem
  segmento    inicio, fim, texto, progresso (0 a 1)
  fim         pasta, arquivos, duracao_audio, tempo_processamento, vazio
              (vazio=true: nada foi gravado, pasta="" e arquivos={})
  erro        codigo, mensagem
"""

from __future__ import annotations

import errno
import fcntl
import gc
import os
import shutil
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import cancelamento, gpu, midia as mod_midia, motor as mod_motor
from .cancelamento import Cancelado
from .erros import ErroTranscritor
from .exportar import FORMATOS, Segmento, gravar


@dataclass
class Opcoes:
    saida: Path
    modelo: str = "auto"
    idioma: str = "pt"  # codigo ou "auto"
    dispositivo: str = "auto"
    faixa: int | None = None
    formatos: tuple[str, ...] = FORMATOS
    salvar_audio: bool = True


PREFIXO_TEMP = ".transcritor-tmp-"
FOLGA_TEMP = 2.0  # entre criar a pasta e travar, outra rodada nao pode apaga-la
LIMITE_NOME_BYTES = 200  # sobra espaco para sufixo e extensao no limite de 255 bytes


def pasta_saida_padrao() -> Path:
    import subprocess

    try:
        r = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=5)
        base = r.stdout.strip()
    except Exception:
        base = ""
    if not base:
        base = str(Path.home() / "Documents")
    return Path(base) / "Transcricoes"


def _nome_base(caminho: Path) -> str:
    nome = caminho.stem.strip().lstrip(".").replace(os.sep, "_")
    bytes_nome = os.fsencode(nome)
    if len(bytes_nome) > LIMITE_NOME_BYTES:
        nome = bytes_nome[:LIMITE_NOME_BYTES].decode("utf-8", "ignore").strip()
    return nome or "transcricao"


def _travar_pasta(pasta: Path) -> int:
    """Trava que o kernel solta sozinho quando o processo morre, de qualquer jeito."""
    fd = os.open(pasta, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise
    return fd


def limpar_sobras(saida: Path) -> None:
    """Apaga temporarios de rodadas que morreram sem limpar (SIGKILL, queda de energia)."""
    agora = time.time()
    try:
        entradas = list(os.scandir(saida))
    except OSError:
        return
    for e in entradas:
        if not e.name.startswith(PREFIXO_TEMP):
            continue
        try:
            if agora - e.stat(follow_symlinks=False).st_mtime < FOLGA_TEMP:
                continue
            fd = _travar_pasta(Path(e.path))  # outra rodada viva segura a trava
        except OSError:
            continue
        try:
            shutil.rmtree(e.path, ignore_errors=True)
        finally:
            os.close(fd)


def _motivo(e: OSError) -> str:
    if isinstance(e, PermissionError):
        return "sem permissão"
    if isinstance(e, (FileExistsError, NotADirectoryError)):
        return "já existe um arquivo com esse nome"
    if e.errno == errno.ENOSPC:
        return "disco cheio"
    return e.strerror or str(e)


def _permissao_padrao() -> int:
    mascara = os.umask(0)
    os.umask(mascara)
    return 0o777 & ~mascara


class Pipeline:
    def __init__(
        self,
        opcoes: Opcoes,
        emitir: Callable[[dict], None],
        fabrica_motor=None,
        tem_gpu=None,
    ):
        self.op = opcoes
        self._emitir_bruto = emitir
        self._fabrica = fabrica_motor or mod_motor.fabrica_padrao
        self._tem_gpu = tem_gpu or gpu.gpu_disponivel
        self._arquivo: str | None = None
        self._cfg: gpu.Config | None = None
        self._motor = None
        self._motor_cfg: gpu.Config | None = None
        self._cancelado_emitido = False

    # eventos ---------------------------------------------------------
    def _emit(self, tipo: str, **campos) -> None:
        ev = {"tipo": tipo}
        if self._arquivo is not None:
            ev["arquivo"] = self._arquivo
        ev.update(campos)
        self._emitir_bruto(ev)

    def _etapa(self, nome: str) -> None:
        self._emit("etapa", etapa=nome)

    def _aviso(self, msg: str) -> None:
        self._emit("aviso", mensagem=msg)

    def emitir_cancelado(self) -> None:
        if not self._cancelado_emitido:
            self._cancelado_emitido = True
            self._emit("erro", codigo="cancelado", mensagem="Cancelado pelo usuário.")

    # motor -----------------------------------------------------------
    def _liberar_motor(self) -> None:
        if self._motor is not None:
            try:
                self._motor.fechar()
            except Exception:
                pass
        self._motor = None
        self._motor_cfg = None
        gc.collect()

    def _obter_motor(self, cfg: gpu.Config):
        if self._motor is not None and self._motor_cfg == cfg:
            return self._motor
        self._liberar_motor()
        self._etapa("carregando_modelo")
        self._motor = self._fabrica(cfg, lambda: self._etapa("baixando_modelo"))
        self._motor_cfg = cfg
        return self._motor

    def _transcrever(self, audio: Path, duracao: float):
        """Transcreve com fallback GPU para CPU, no carregamento ou no meio."""
        idioma = None if self.op.idioma == "auto" else self.op.idioma
        while True:
            cfg = self._cfg
            try:
                motor = self._obter_motor(cfg)
                self._emit("dispositivo", dispositivo=cfg.dispositivo, compute_type=cfg.compute_type, modelo=cfg.modelo)
                self._etapa("transcrevendo")
                info, gerador = motor.transcrever(str(audio), idioma)
                self._emit("idioma", idioma=info.idioma, probabilidade=round(info.probabilidade, 4))
                total = duracao or info.duracao
                segmentos: list[Segmento] = []
                for seg in gerador:
                    if not seg.texto.strip():
                        continue
                    segmentos.append(seg)
                    prog = min(1.0, max(0.0, seg.fim / total)) if total > 0 else 0.0
                    self._emit(
                        "segmento", inicio=round(seg.inicio, 3), fim=round(seg.fim, 3),
                        texto=seg.texto.strip(), progresso=round(prog, 4),
                    )
                return cfg, info, segmentos, total
            except (Cancelado, ErroTranscritor):
                raise
            except Exception as e:
                if cfg.dispositivo == "cuda" and gpu.eh_erro_de_gpu(e):
                    if self.op.dispositivo == "cuda":
                        raise ErroTranscritor("gpu_falhou", f"A GPU falhou e o modo cuda foi exigido: {e}")
                    self._aviso(f"A GPU falhou ({str(e)[:200]}). Refazendo em CPU, vai demorar mais.")
                    self._liberar_motor()
                    self._cfg = gpu.config_cpu(self.op.modelo)
                    continue
                raise

    # arquivo ---------------------------------------------------------
    def _processar(self, entrada: str) -> None:
        inicio = time.monotonic()
        self._etapa("analisando")
        info_m = mod_midia.analisar(entrada, self.op.faixa)
        self._emit(
            "midia", eh_video=info_m.eh_video, duracao=round(info_m.duracao, 3),
            codec_audio=info_m.codec_audio, canais=info_m.canais, faixas_audio=len(info_m.faixas),
        )
        if self._cfg is None:
            self._cfg = gpu.escolher(self.op.modelo, self.op.dispositivo, self._tem_gpu)

        try:
            self.op.saida.mkdir(parents=True, exist_ok=True)
            limpar_sobras(self.op.saida)
            staging = Path(tempfile.mkdtemp(prefix=PREFIXO_TEMP, dir=self.op.saida))
        except OSError as e:
            raise ErroTranscritor(
                "saida_invalida", f"Não consegui usar a pasta de saída {self.op.saida}: {_motivo(e)}"
            )
        cancelamento.registrar_pasta(staging)
        trava = None
        try:
            trava = _travar_pasta(staging)
            audio = info_m.caminho
            extraido: Path | None = None
            if info_m.eh_video or info_m.faixa != 0:
                self._etapa("extraindo")
                extraido = mod_midia.extrair_audio(info_m, staging, self._aviso)
                audio = extraido

            cfg, info, segmentos, total = self._transcrever(audio, info_m.duracao)

            if not segmentos:
                self._aviso("Nenhuma fala foi detectada. Nada foi gravado.")
                self._emit(
                    "fim", pasta="", arquivos={}, duracao_audio=round(total, 3),
                    tempo_processamento=round(time.monotonic() - inicio, 3), vazio=True,
                )
                return

            self._etapa("salvando")
            nome = _nome_base(info_m.caminho)
            metadados = {
                "arquivo_origem": str(info_m.caminho),
                "modelo": cfg.modelo,
                "dispositivo": cfg.dispositivo,
                "compute_type": cfg.compute_type,
                "idioma": info.idioma,
                "probabilidade_idioma": round(info.probabilidade, 4),
                "duracao": round(total, 3),
            }
            try:
                gravar(staging, nome, segmentos, self.op.formatos, metadados)
                if extraido is not None:
                    if self.op.salvar_audio:
                        extraido.rename(staging / f"{nome}{extraido.suffix}")
                    else:
                        extraido.unlink(missing_ok=True)
                os.chmod(staging, _permissao_padrao())  # mkdtemp cria so para o dono
                final = self._publicar(staging, nome)
            except OSError as e:
                raise ErroTranscritor("saida_invalida", f"Não consegui salvar em {self.op.saida}: {_motivo(e)}")
            cancelamento.liberar_pasta(staging)
            arquivos = {f: str(final / f"{nome}.{f}") for f in self.op.formatos}
            if extraido is not None and self.op.salvar_audio:
                arquivos["audio"] = str(final / f"{nome}{extraido.suffix}")
            self._emit(
                "fim", pasta=str(final), arquivos=arquivos, duracao_audio=round(total, 3),
                tempo_processamento=round(time.monotonic() - inicio, 3), vazio=False,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            cancelamento.liberar_pasta(staging)
            if trava is not None:
                os.close(trava)

    def _publicar(self, staging: Path, nome: str) -> Path:
        """Troca o nome da pasta temporaria pelo definitivo, sem sobrescrever."""
        n = 1
        while True:
            alvo = self.op.saida / (nome if n == 1 else f"{nome}-{n}")
            if os.path.lexists(alvo):  # rename trocaria uma pasta vazia sem avisar
                n += 1
                continue
            try:
                os.rename(staging, alvo)
                return alvo
            except OSError:
                if not os.path.lexists(alvo):
                    raise
                n += 1

    # lote ------------------------------------------------------------
    def processar(self, arquivos: list[str]) -> int:
        """Devolve 0 (tudo certo) ou 1 (algum falhou). Cancelado sobe como excecao."""
        falhou = False
        try:
            for entrada in arquivos:
                self._arquivo = os.path.abspath(entrada)
                try:
                    self._processar(entrada)
                except ErroTranscritor as e:
                    falhou = True
                    self._emit("erro", codigo=e.codigo, mensagem=e.mensagem)
                except Cancelado:
                    raise
                except Exception as e:
                    falhou = True
                    if os.environ.get("TRANSCRITOR_DEBUG"):
                        traceback.print_exc()
                    self._emit("erro", codigo="erro_inesperado", mensagem=f"{type(e).__name__}: {e}")
        finally:
            self._liberar_motor()
        return 1 if falhou else 0
