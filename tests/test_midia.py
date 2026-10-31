import json
import subprocess
import threading
import time

import pytest

from transcritor import cancelamento, midia
from transcritor.erros import ErroTranscritor


def duracao(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(p)],
                       capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def codec(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def test_video_e_audio_decididos_pelo_conteudo(midias):
    assert midia.analisar(midias["video_aac"]).eh_video is True
    assert midia.analisar(midias["wav"]).eh_video is False


def test_mp3_com_capa_conta_como_audio(midias):
    m = midia.analisar(midias["mp3_capa"])
    assert m.eh_video is False and m.codec_audio == "mp3"


def test_extensao_nao_decide(copia):
    p = copia("wav", "parece_video.mp4")  # wav com extensao de video: ffprobe ainda diz audio
    assert midia.analisar(p).eh_video is False


def test_video_sem_audio(midias):
    with pytest.raises(ErroTranscritor) as e:
        midia.analisar(midias["video_mudo"])
    assert e.value.codigo == "sem_audio"


def test_texto_renomeado_e_vazio_e_inexistente(midias, tmp_path):
    with pytest.raises(ErroTranscritor) as e:
        midia.analisar(midias["texto"])
    assert e.value.codigo == "midia_invalida"
    with pytest.raises(ErroTranscritor) as e:
        midia.analisar(midias["vazio"])
    assert e.value.codigo == "arquivo_vazio"
    with pytest.raises(ErroTranscritor) as e:
        midia.analisar(tmp_path / "nao_existe.mp4")
    assert e.value.codigo == "arquivo_nao_encontrado"
    with pytest.raises(ErroTranscritor) as e:
        midia.analisar(tmp_path)
    assert e.value.codigo == "nao_e_arquivo"


def test_faixas_e_faixa_invalida(midias):
    m = midia.analisar(midias["duas_faixas"])
    assert len(m.faixas) == 2 and m.faixa == 0
    with pytest.raises(ErroTranscritor) as e:
        midia.analisar(midias["duas_faixas"], faixa=5)
    assert e.value.codigo == "faixa_invalida"


def test_copia_aac_para_m4a(midias, tmp_path):
    m = midia.analisar(midias["video_aac"])
    saida = midia.extrair_audio(m, tmp_path)
    assert saida.suffix == ".m4a" and codec(saida) == "aac"
    assert abs(duracao(saida) - 2.0) < 0.3


def test_copia_mp3(midias, tmp_path):
    saida = midia.extrair_audio(midia.analisar(midias["video_mp3"]), tmp_path)
    assert saida.suffix == ".mp3" and codec(saida) == "mp3"


def test_ac3_nao_cabe_e_reencoda_para_aac(midias, tmp_path):
    saida = midia.extrair_audio(midia.analisar(midias["video_ac3"]), tmp_path)
    assert saida.suffix == ".m4a" and codec(saida) == "aac"


def test_mov_maiusculo(midias, tmp_path):
    m = midia.analisar(midias["video_mov"])
    assert m.eh_video
    assert midia.extrair_audio(m, tmp_path).exists()


def test_segunda_faixa(midias, tmp_path):
    m = midia.analisar(midias["duas_faixas"], faixa=1)
    assert midia.extrair_audio(m, tmp_path).exists()


@pytest.mark.parametrize("nome", [
    "com espaco.mp4", "acentuação ção.mp4", "aspa'simples\"dupla.mp4", "-comeca-com-hifen.mp4", "a;b$(x)&c.mp4",
])
def test_nomes_dificeis(copia, tmp_path, nome):
    p = copia("video_aac", nome)
    m = midia.analisar(p)
    assert m.eh_video
    saida = midia.extrair_audio(m, tmp_path)
    assert saida.exists()


def test_divergencia_de_duracao_e_falha(midias, tmp_path):
    m = midia.analisar(midias["video_aac"])
    m.faixas[0]["duration"] = "100"  # finge um original muito mais longo
    m.duracao = 100.0
    with pytest.raises(ErroTranscritor) as e:
        midia.extrair_audio(m, tmp_path)
    assert e.value.codigo == "extracao_divergente"


def test_encerrar_processos_mata_o_ffmpeg():
    args = ["ffmpeg", "-nostdin", "-re", "-v", "error", "-f", "lavfi", "-i", "sine=d=60", "-f", "null", "-"]
    resultado = {}

    def rodar():
        resultado["r"] = midia._executar(args)

    t = threading.Thread(target=rodar)
    t.start()
    time.sleep(1.0)
    inicio = time.monotonic()
    cancelamento.encerrar_processos()
    t.join(10)
    assert not t.is_alive() and time.monotonic() - inicio < 5
    assert resultado["r"].returncode != 0
