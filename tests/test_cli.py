import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LANCADOR = RAIZ / "bin" / "transcrever"
ENV = {**os.environ, "PYTHONPATH": str(RAIZ)}

SCRIPT_LENTO = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, %r)
    sys.path.insert(0, %r)
    from conftest import MotorFalso
    from transcritor import pipeline, cli
    from transcritor.exportar import Segmento
    from transcritor.motor import InfoTranscricao

    class Lento(MotorFalso):
        def transcrever(self, audio, idioma):
            def gerar():
                yield Segmento(0, 1, "primeiro")
                time.sleep(60)
            return InfoTranscricao("pt", 0.9, 2.0), gerar()

    pipeline.mod_motor.fabrica_padrao = lambda cfg, b: Lento(cfg)
    sys.exit(cli.main(sys.argv[1:]))
""" % (str(RAIZ), str(RAIZ / "tests")))


def test_ajuda_e_erro_de_uso():
    r = subprocess.run([str(LANCADOR), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--json-eventos" in r.stdout
    r = subprocess.run([str(LANCADOR), "x.mp4", "--formatos", "pdf"], capture_output=True, text=True)
    assert r.returncode == 2


def test_symlink_do_lancador(tmp_path):
    elo = tmp_path / "transcrever"
    elo.symlink_to(LANCADOR)
    r = subprocess.run([str(elo), "--help"], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0


def test_json_eventos_so_ndjson_e_erros_sem_traceback(midias, tmp_path):
    r = subprocess.run(
        [str(LANCADOR), str(midias["texto"]), str(tmp_path / "nada.mp3"), str(midias["video_mudo"]),
         "--json-eventos", "--saida", str(tmp_path / "o")],
        capture_output=True, text=True,
    )
    assert r.returncode == 1
    eventos = [json.loads(l) for l in r.stdout.splitlines()]
    assert [e["codigo"] for e in eventos if e["tipo"] == "erro"] == [
        "midia_invalida", "arquivo_nao_encontrado", "sem_audio"]
    assert "Traceback" not in r.stderr + r.stdout


def test_saida_humana_em_portugues(midias, tmp_path):
    r = subprocess.run([str(LANCADOR), str(midias["video_mudo"]), "--saida", str(tmp_path / "o")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "sem_audio" in r.stderr and "Analisando" in r.stdout


def test_sigterm_cancela_com_130_e_sem_sobras(midias, tmp_path):
    saida = tmp_path / "o"
    p = subprocess.Popen(
        [sys.executable, "-P", "-c", SCRIPT_LENTO, str(midias["video_aac"]), "--json-eventos", "--saida", str(saida)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV,
    )
    vistos = []
    for linha in p.stdout:
        vistos.append(json.loads(linha))
        if vistos[-1]["tipo"] == "segmento":
            break
    p.send_signal(signal.SIGTERM)
    t0 = time.monotonic()
    p.wait(timeout=15)
    assert p.returncode == 130 and time.monotonic() - t0 < 10
    resto = [json.loads(l) for l in p.stdout]
    assert resto[-1]["codigo"] == "cancelado"
    assert list(saida.iterdir()) == []


def test_idioma_invalido_recusado_antes_de_carregar_o_modelo(midias):
    r = subprocess.run([str(LANCADOR), str(midias["wav"]), "--idioma", "zz"], capture_output=True, text=True)
    assert r.returncode == 2 and "zz" in r.stderr and "Traceback" not in r.stderr


def test_lista_de_idiomas_igual_a_do_faster_whisper():
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    from transcritor.cli import IDIOMAS

    assert IDIOMAS == frozenset(_LANGUAGE_CODES)


def test_saida_humana_com_nome_que_nao_e_utf8(midias, tmp_path):
    entrada = os.fsencode(tmp_path) + b"/ruim\xff.mp4"
    with open(entrada, "wb") as f:
        f.write(b"nao e video")
    r = subprocess.run([os.fsencode(LANCADOR), entrada, b"--saida", os.fsencode(tmp_path / "o")], capture_output=True)
    assert r.returncode == 1 and b"midia_invalida" in r.stderr and b"Traceback" not in r.stderr


def test_lancador_da_janela_nao_define_ld_library_path():
    assert "LD_LIBRARY_PATH=" not in (RAIZ / "bin" / "transcritor").read_text()
