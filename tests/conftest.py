import subprocess
from pathlib import Path

import pytest

from transcritor.exportar import Segmento


def ff(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def midias(tmp_path_factory):
    d = tmp_path_factory.mktemp("midias")
    m = {}
    seno = ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    video = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2"]
    m["video_aac"] = d / "video.mp4"
    ff(*video, *seno, "-c:v", "mpeg4", "-c:a", "aac", str(m["video_aac"]))
    m["video_mov"] = d / "CLIPE.MOV"
    ff(*video, *seno, "-c:v", "mpeg4", "-c:a", "aac", "-f", "mov", str(m["video_mov"]))
    m["video_ac3"] = d / "ac3.mkv"
    ff(*video, *seno, "-c:v", "mpeg4", "-c:a", "ac3", str(m["video_ac3"]))
    m["video_mp3"] = d / "mp3.mkv"
    ff(*video, *seno, "-c:v", "mpeg4", "-c:a", "libmp3lame", str(m["video_mp3"]))
    m["video_mudo"] = d / "mudo.mp4"
    ff(*video, "-c:v", "mpeg4", str(m["video_mudo"]))
    m["wav"] = d / "som.wav"
    ff(*seno, str(m["wav"]))
    m["capa"] = d / "capa.png"
    ff("-f", "lavfi", "-i", "testsrc=size=64x64:duration=1", "-frames:v", "1", str(m["capa"]))
    m["mp3_capa"] = d / "musica.mp3"
    ff(*seno, "-i", str(m["capa"]), "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame",
       "-c:v", "mjpeg", "-disposition:v", "attached_pic", "-id3v2_version", "3", str(m["mp3_capa"]))
    m["duas_faixas"] = d / "duas.mkv"
    ff(*video, *seno, "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
       "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "mpeg4", "-c:a", "aac", str(m["duas_faixas"]))
    m["texto"] = d / "falso.mp4"
    m["texto"].write_text("isto nao e um video\n")
    m["vazio"] = d / "vazio.mp3"
    m["vazio"].write_bytes(b"")
    return m


@pytest.fixture
def copia(tmp_path, midias):
    """Copia uma midia para um nome dificil dentro de tmp_path."""
    import shutil

    def _copiar(chave, nome):
        destino = tmp_path / "entrada" / nome
        destino.parent.mkdir(exist_ok=True)
        shutil.copy(midias[chave], destino)
        return destino

    return _copiar


class MotorFalso:
    """Motor de mentira: devolve segmentos fixos, opcionalmente falha no meio."""

    instancias = []

    def __init__(self, cfg, segmentos=None, falha_no_meio=None, falha_ao_carregar=None, idioma="pt"):
        if falha_ao_carregar:
            raise falha_ao_carregar
        self.cfg = cfg
        self.segmentos = segmentos if segmentos is not None else [
            Segmento(0.0, 1.0, "ola mundo"), Segmento(1.0, 2.0, "segundo trecho")
        ]
        self.falha_no_meio = falha_no_meio
        self.idioma = idioma
        self.fechado = False
        MotorFalso.instancias.append(self)

    def transcrever(self, audio, idioma):
        from transcritor.motor import InfoTranscricao

        def gerar():
            for i, s in enumerate(self.segmentos):
                if self.falha_no_meio and i == 1:
                    raise self.falha_no_meio
                yield s

        return InfoTranscricao(self.idioma, 0.99, 2.0), gerar()

    def fechar(self):
        self.fechado = True


@pytest.fixture(autouse=True)
def _zerar_motor():
    MotorFalso.instancias.clear()
