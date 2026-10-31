"""Motor de transcricao com faster-whisper. Tudo local, o audio nunca sai da maquina."""

from __future__ import annotations

import gc
import os
from dataclasses import dataclass
from typing import Callable, Iterable

from .erros import ErroTranscritor
from .exportar import Segmento
from .gpu import Config


@dataclass
class InfoTranscricao:
    idioma: str
    probabilidade: float
    duracao: float


class MotorFaster:
    def __init__(self, cfg: Config, ao_baixar: Callable[[], None]):
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model

        try:
            caminho = download_model(cfg.modelo, local_files_only=True)
        except ValueError as e:
            raise ErroTranscritor("modelo_invalido", str(e))
        except Exception:
            ao_baixar()
            try:
                caminho = download_model(cfg.modelo)
            except Exception as e:
                motivo = (str(e).splitlines() or [type(e).__name__])[0][:200]
                raise ErroTranscritor(
                    "modelo_indisponivel",
                    f"O modelo {cfg.modelo} não está no cache e o download falhou. Confira a internet. ({motivo})",
                )
        self._modelo = WhisperModel(caminho, device=cfg.dispositivo, compute_type=cfg.compute_type)

    def transcrever(self, audio: str, idioma: str | None) -> tuple[InfoTranscricao, Iterable[Segmento]]:
        segmentos, info = self._modelo.transcribe(
            audio,
            language=idioma,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        resumo = InfoTranscricao(info.language, float(info.language_probability), float(info.duration))

        def gerar():
            for s in segmentos:
                yield Segmento(float(s.start), float(s.end), s.text.strip())

        return resumo, gerar()

    def fechar(self) -> None:
        self._modelo = None
        gc.collect()


def fabrica_padrao(cfg: Config, ao_baixar: Callable[[], None]) -> MotorFaster:
    return MotorFaster(cfg, ao_baixar)
