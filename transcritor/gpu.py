"""Escolha de dispositivo, modelo e tipo de calculo.

Padroes escolhidos por medicao em video real em portugues (317s, RTX 3050 6GB):
o large-v3-turbo errou menos nomes que o small e que o large-v3. Numeros no README.
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass

from .erros import ErroTranscritor

MODELOS = ("small", "medium", "large-v3-turbo", "large-v3")
MODELO_PADRAO_GPU = "large-v3-turbo"
MODELO_PADRAO_CPU = "large-v3-turbo"
COMPUTE_TYPE_GPU = "float16"
COMPUTE_TYPE_CPU = "int8"

_PADRAO_ERRO_GPU = re.compile(
    r"cuda|cublas|cudnn|out of memory|libcu\w*\.so|cannot (open|load) shared|nvrtc|nvidia|gpu",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Config:
    dispositivo: str  # "cuda" ou "cpu"
    modelo: str
    compute_type: str
    modelo_auto: bool  # o modelo veio de "auto"


def gpu_disponivel() -> bool:
    """Ha dispositivo CUDA e a libcublas.so.12 carrega."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return False
        ctypes.CDLL("libcublas.so.12")
        return True
    except Exception:
        return False


def config_cpu(modelo: str) -> Config:
    auto = modelo == "auto"
    return Config("cpu", MODELO_PADRAO_CPU if auto else modelo, COMPUTE_TYPE_CPU, auto)


def config_gpu(modelo: str) -> Config:
    auto = modelo == "auto"
    return Config("cuda", MODELO_PADRAO_GPU if auto else modelo, COMPUTE_TYPE_GPU, auto)


def escolher(modelo: str, dispositivo: str, tem_gpu=gpu_disponivel) -> Config:
    if dispositivo == "cpu":
        return config_cpu(modelo)
    if tem_gpu():
        return config_gpu(modelo)
    if dispositivo == "cuda":
        raise ErroTranscritor(
            "gpu_indisponivel",
            "Pediu GPU, mas não há CUDA utilizável (falta dispositivo ou a libcublas.so.12). "
            "Use --dispositivo auto ou cpu.",
        )
    return config_cpu(modelo)


def eh_erro_de_gpu(exc: BaseException) -> bool:
    """Falha que justifica refazer em CPU: biblioteca ausente, falta de memoria, CUDA."""
    if isinstance(exc, MemoryError):
        return True
    if isinstance(exc, (RuntimeError, OSError, ImportError)):
        return bool(_PADRAO_ERRO_GPU.search(str(exc)))
    return False
