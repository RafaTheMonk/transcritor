import json
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.mark.lento
def test_modelo_small_em_cpu_com_fala_real(tmp_path):
    """Gera fala com espeak-ng se existir; senao so confere que o fluxo termina."""
    wav = tmp_path / "fala.wav"
    try:
        subprocess.run(["espeak-ng", "-v", "pt-br", "-w", str(wav), "ola mundo, isto e um teste"], check=True)
        com_fala = True
    except (FileNotFoundError, subprocess.CalledProcessError):
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=3", str(wav)], check=True)
        com_fala = False
    r = subprocess.run(
        [str(RAIZ / "bin" / "transcrever"), str(wav), "--modelo", "small", "--dispositivo", "cpu",
         "--json-eventos", "--saida", str(tmp_path / "o")],
        capture_output=True, text=True, timeout=600,
    )
    assert r.returncode == 0, r.stderr
    eventos = [json.loads(l) for l in r.stdout.splitlines()]
    fim = eventos[-1]
    assert fim["tipo"] == "fim"
    assert any(e["tipo"] == "dispositivo" and e["dispositivo"] == "cpu" for e in eventos)
    if com_fala:
        assert fim["vazio"] is False
        assert "teste" in Path(fim["arquivos"]["txt"]).read_text().lower()
    else:
        assert fim["vazio"] is True
