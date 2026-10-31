"""Analise (ffprobe) e extracao de audio (ffmpeg).

Video ou audio e decidido pelo conteudo, nunca pela extensao. Sempre lista de
argumentos, nunca shell, e caminhos absolutos para um nome comecando com hifen
nao virar opcao.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import cancelamento
from .erros import ErroTranscritor

# codec de audio -> extensao do conteiner onde cabe sem reencodar
CONTEINER_COPIA = {
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_s32le": ".wav",
    "pcm_u8": ".wav",
    "pcm_f32le": ".wav",
    "pcm_f64le": ".wav",
}
EXT_REENCODE = ".m4a"
TOLERANCIA_MIN = 2.0
TOLERANCIA_REL = 0.05
LIMITE_ERRO_STDERR = 600


@dataclass
class Midia:
    caminho: Path
    eh_video: bool
    duracao: float
    faixas: list[dict] = field(default_factory=list)  # so as de audio
    faixa: int = 0  # indice entre as faixas de audio (como em -map 0:a:N)

    @property
    def codec_audio(self) -> str:
        return self.faixas[self.faixa].get("codec_name", "desconhecido")

    @property
    def canais(self) -> int:
        return int(self.faixas[self.faixa].get("channels") or 0)

    @property
    def duracao_faixa(self) -> float:
        d = _numero(self.faixas[self.faixa].get("duration"))
        return d if d > 0 else self.duracao


def _numero(v) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    return n if n == n and n >= 0 else 0.0


def _antes_do_exec() -> None:
    """No filho: morre junto se o pai morrer."""
    cancelamento.morrer_com_o_pai(signal.SIGKILL)


def _executar(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_antes_do_exec,
    )
    cancelamento.registrar_processo(proc)
    try:
        saida, erro = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        cancelamento.liberar_processo(proc)
    return subprocess.CompletedProcess(args, proc.returncode, saida, erro)


def _probe(caminho: Path) -> dict:
    try:
        r = _executar(
            [
                "ffprobe", "-v", "error", "-protocol_whitelist", "file",
                "-show_format", "-show_streams", "-of", "json", str(caminho),
            ],
            timeout=120,
        )
    except FileNotFoundError:
        raise ErroTranscritor("ffmpeg_ausente", "ffprobe não foi encontrado. Instale o pacote ffmpeg.")
    except subprocess.TimeoutExpired:
        raise ErroTranscritor("midia_invalida", "A análise do arquivo demorou demais e foi interrompida.")
    if r.returncode != 0:
        raise ErroTranscritor("midia_invalida", "O arquivo não é um vídeo ou áudio que o ffmpeg entenda.")
    try:
        return json.loads(r.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise ErroTranscritor("midia_invalida", "Não consegui ler a análise do arquivo.")


def validar_arquivo(caminho: Path) -> None:
    if not caminho.exists():
        raise ErroTranscritor("arquivo_nao_encontrado", f"Arquivo não encontrado: {caminho}")
    if not caminho.is_file():
        raise ErroTranscritor("nao_e_arquivo", f"Não é um arquivo: {caminho}")
    if not os.access(caminho, os.R_OK):
        raise ErroTranscritor("sem_permissao", f"Sem permissão para ler: {caminho}")
    if caminho.stat().st_size == 0:
        raise ErroTranscritor("arquivo_vazio", f"O arquivo está vazio: {caminho}")


def analisar(caminho: str | Path, faixa: int | None = None) -> Midia:
    caminho = Path(os.path.abspath(caminho))
    validar_arquivo(caminho)
    dados = _probe(caminho)
    streams = dados.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [
        s for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not audio:
        raise ErroTranscritor("sem_audio", "O arquivo não tem nenhuma faixa de áudio.")
    if faixa is None:
        faixa = next(
            (i for i, s in enumerate(audio) if (s.get("disposition") or {}).get("default")), 0
        )
    elif not 0 <= faixa < len(audio):
        raise ErroTranscritor(
            "faixa_invalida",
            f"Faixa {faixa} não existe: o arquivo tem {len(audio)} faixa(s) de áudio (de 0 a {len(audio) - 1}).",
        )
    duracao = _numero((dados.get("format") or {}).get("duration"))
    if duracao <= 0:
        duracao = max((_numero(s.get("duration")) for s in audio), default=0.0)
    return Midia(caminho=caminho, eh_video=bool(video), duracao=duracao, faixas=audio, faixa=faixa)


def _duracao_arquivo(caminho: Path) -> float:
    dados = _probe(caminho)
    return _numero((dados.get("format") or {}).get("duration"))


def _ffmpeg(midia: Midia, destino: Path, opcoes_audio: list[str]) -> None:
    args = [
        "ffmpeg", "-nostdin", "-y", "-v", "error", "-protocol_whitelist", "file",
        "-i", str(midia.caminho),
        "-map", f"0:a:{midia.faixa}", "-vn", "-sn", "-dn",
        "-map_metadata", "-1", "-map_chapters", "-1",
        *opcoes_audio, str(destino),
    ]
    try:
        r = _executar(args)
    except FileNotFoundError:
        raise ErroTranscritor("ffmpeg_ausente", "ffmpeg não foi encontrado. Instale o pacote ffmpeg.")
    if r.returncode != 0:
        detalhe = r.stderr.decode("utf-8", "replace").strip()[-LIMITE_ERRO_STDERR:]
        raise ErroTranscritor("extracao_falhou", f"O ffmpeg não conseguiu extrair o áudio. {detalhe}")


def _confere(destino: Path, esperado: float) -> None:
    if not destino.exists() or destino.stat().st_size == 0:
        raise ErroTranscritor("extracao_falhou", "O áudio extraído ficou vazio.")
    if esperado <= 0:
        return
    obtido = _duracao_arquivo(destino)
    if abs(obtido - esperado) > max(TOLERANCIA_MIN, TOLERANCIA_REL * esperado):
        raise ErroTranscritor(
            "extracao_divergente",
            f"O áudio extraído tem {obtido:.1f}s, mas o original tem {esperado:.1f}s.",
        )


def extrair_audio(midia: Midia, pasta: Path, avisar=lambda msg: None) -> Path:
    """Extrai a faixa escolhida para dentro de 'pasta'. Copia sem reencodar quando cabe."""
    esperado = midia.duracao_faixa
    ext = CONTEINER_COPIA.get(midia.codec_audio)
    if ext:
        destino = pasta / f"audio_extraido{ext}"
        try:
            _ffmpeg(midia, destino, ["-c:a", "copy"])
            _confere(destino, esperado)
            return destino
        except ErroTranscritor as e:
            if e.codigo == "ffmpeg_ausente":
                raise
            avisar("A cópia direta do áudio falhou, vou reencodar para aac.")
            destino.unlink(missing_ok=True)
    destino = pasta / f"audio_extraido{EXT_REENCODE}"
    _ffmpeg(midia, destino, ["-c:a", "aac", "-b:a", "192k"])
    _confere(destino, esperado)
    return destino
