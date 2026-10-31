import errno
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from conftest import MotorFalso
from transcritor import cli, gpu
from transcritor.cancelamento import Cancelado
from transcritor.exportar import Segmento
from transcritor.pipeline import Opcoes, Pipeline


def rodar(arquivos, saida, fabrica=None, tem_gpu=lambda: False, **kw):
    eventos = []
    op = Opcoes(saida=saida, **kw)
    pl = Pipeline(op, eventos.append, fabrica or (lambda cfg, b: MotorFalso(cfg)), tem_gpu)
    codigo = pl.processar([str(a) for a in arquivos])
    return codigo, eventos


def tipos(ev):
    return [e["tipo"] for e in ev]


def sem_sobras(saida):
    return [p for p in saida.iterdir() if p.name.startswith(".transcritor-tmp-")]


def test_video_completo(midias, tmp_path):
    saida = tmp_path / "out"
    cod, ev = rodar([midias["video_aac"]], saida)
    assert cod == 0
    etapas = [e["etapa"] for e in ev if e["tipo"] == "etapa"]
    assert etapas == ["analisando", "extraindo", "carregando_modelo", "transcrevendo", "salvando"]
    fim = ev[-1]
    assert fim["tipo"] == "fim" and fim["vazio"] is False
    pasta = Path(fim["pasta"])
    assert pasta == saida / "video"
    for f in ("txt", "srt", "vtt", "json", "audio"):
        assert Path(fim["arquivos"][f]).exists()
    assert (pasta / "video.m4a").exists()
    assert json.loads((pasta / "video.json").read_text())["modelo"] == gpu.MODELO_PADRAO_CPU
    assert all(e["arquivo"] == str(midias["video_aac"]) for e in ev)
    segs = [e for e in ev if e["tipo"] == "segmento"]
    assert segs[-1]["progresso"] == 1.0
    assert sem_sobras(saida) == []


def test_audio_direto_sem_extrair_nem_copiar(midias, tmp_path):
    cod, ev = rodar([midias["wav"]], tmp_path / "o")
    assert cod == 0
    assert "extraindo" not in [e.get("etapa") for e in ev]
    assert "audio" not in ev[-1]["arquivos"]
    assert [p.name for p in Path(ev[-1]["pasta"]).iterdir() if p.suffix == ".wav"] == []


def test_sem_salvar_audio_e_formatos(midias, tmp_path):
    cod, ev = rodar([midias["video_aac"]], tmp_path / "o", salvar_audio=False, formatos=("txt",))
    nomes = sorted(p.name for p in Path(ev[-1]["pasta"]).iterdir())
    assert nomes == ["video.txt"]


def test_pasta_existente_ganha_sufixo(midias, tmp_path):
    saida = tmp_path / "o"
    rodar([midias["wav"]], saida)
    _, ev = rodar([midias["wav"], midias["wav"]], saida)
    assert [Path(e["pasta"]).name for e in ev if e["tipo"] == "fim"] == ["som-2", "som-3"]
    assert (saida / "som").is_dir()


def test_erro_em_um_arquivo_nao_para_os_demais(midias, tmp_path):
    cod, ev = rodar([midias["texto"], midias["video_mudo"], midias["wav"]], tmp_path / "o")
    assert cod == 1
    erros = [e["codigo"] for e in ev if e["tipo"] == "erro"]
    assert erros == ["midia_invalida", "sem_audio"]
    assert ev[-1]["tipo"] == "fim"


def test_audio_sem_fala(midias, tmp_path):
    saida = tmp_path / "o"
    fab = lambda cfg, b: MotorFalso(cfg, segmentos=[])
    cod, ev = rodar([midias["video_aac"]], saida, fab)
    assert cod == 0
    assert ev[-1]["vazio"] is True and ev[-1]["pasta"] == ""
    assert "aviso" in tipos(ev)
    assert list(saida.iterdir()) == []


def test_fallback_gpu_no_carregamento(midias, tmp_path):
    def fab(cfg, b):
        if cfg.dispositivo == "cuda":
            raise RuntimeError("libcublas.so.12 not found")
        return MotorFalso(cfg)

    cod, ev = rodar([midias["wav"]], tmp_path / "o", fab, tem_gpu=lambda: True)
    assert cod == 0
    disp = [(e["dispositivo"], e["modelo"]) for e in ev if e["tipo"] == "dispositivo"]
    assert disp == [("cpu", gpu.MODELO_PADRAO_CPU)]
    assert "aviso" in tipos(ev)


def test_fallback_gpu_no_meio_descarta_parcial(midias, tmp_path):
    def fab(cfg, b):
        if cfg.dispositivo == "cuda":
            return MotorFalso(cfg, falha_no_meio=RuntimeError("CUDA failed with error out of memory"))
        return MotorFalso(cfg)

    cod, ev = rodar([midias["wav"]], tmp_path / "o", fab, tem_gpu=lambda: True)
    assert cod == 0
    disp = [e["dispositivo"] for e in ev if e["tipo"] == "dispositivo"]
    assert disp == ["cuda", "cpu"]
    j = json.loads(Path(ev[-1]["arquivos"]["json"]).read_text())
    assert j["dispositivo"] == "cpu" and len(j["segmentos"]) == 2
    assert MotorFalso.instancias[0].fechado
    # depois do fallback, o proximo arquivo ja vai direto para CPU
    cod, ev = rodar([midias["wav"], midias["wav"]], tmp_path / "o2", fab, tem_gpu=lambda: True)
    assert [e["dispositivo"] for e in ev if e["tipo"] == "dispositivo"].count("cuda") == 1


def test_fallback_mantem_modelo_escolhido_pelo_usuario(midias, tmp_path):
    def fab(cfg, b):
        if cfg.dispositivo == "cuda":
            raise RuntimeError("CUDA error")
        return MotorFalso(cfg)

    _, ev = rodar([midias["wav"]], tmp_path / "o", fab, tem_gpu=lambda: True, modelo="medium")
    d = [e for e in ev if e["tipo"] == "dispositivo"][0]
    assert (d["dispositivo"], d["modelo"], d["compute_type"]) == ("cpu", "medium", "int8")


def test_cuda_explicito_nao_cai(midias, tmp_path):
    def fab(cfg, b):
        raise RuntimeError("CUDA error")

    cod, ev = rodar([midias["wav"]], tmp_path / "o", fab, tem_gpu=lambda: True, dispositivo="cuda")
    assert cod == 1 and ev[-1]["codigo"] == "gpu_falhou"


def test_cuda_explicito_sem_gpu(midias, tmp_path):
    cod, ev = rodar([midias["wav"]], tmp_path / "o", dispositivo="cuda", tem_gpu=lambda: False)
    assert cod == 1 and ev[-1]["codigo"] == "gpu_indisponivel"


def test_erro_comum_do_motor_nao_vira_fallback(midias, tmp_path):
    def fab(cfg, b):
        return MotorFalso(cfg, falha_no_meio=ValueError("audio corrompido"))

    cod, ev = rodar([midias["wav"]], tmp_path / "o", fab, tem_gpu=lambda: True)
    assert cod == 1 and ev[-1]["codigo"] == "erro_inesperado"
    assert [e["dispositivo"] for e in ev if e["tipo"] == "dispositivo"] == ["cuda"]


def test_cancelamento_no_meio_nao_deixa_saida(midias, tmp_path):
    saida = tmp_path / "o"
    fab = lambda cfg, b: MotorFalso(cfg, falha_no_meio=Cancelado())
    with pytest.raises(Cancelado):
        rodar([midias["video_aac"]], saida, fab)
    assert list(saida.iterdir()) == []


def test_escolha_de_dispositivo():
    t = lambda c: (c.dispositivo, c.modelo, c.compute_type)
    assert t(gpu.escolher("auto", "auto", lambda: True)) == ("cuda", "large-v3-turbo", "float16")
    assert t(gpu.escolher("auto", "auto", lambda: False)) == ("cpu", "large-v3-turbo", "int8")
    assert t(gpu.escolher("medium", "cpu", lambda: True)) == ("cpu", "medium", "int8")
    assert gpu.eh_erro_de_gpu(RuntimeError("CUDA out of memory"))
    assert not gpu.eh_erro_de_gpu(ValueError("cuda"))
    assert not gpu.eh_erro_de_gpu(RuntimeError("audio ruim"))


def test_cancelado_traz_o_arquivo_em_andamento(midias, tmp_path):
    eventos = []
    pl = Pipeline(Opcoes(saida=tmp_path / "o"), eventos.append,
                  lambda cfg, b: MotorFalso(cfg, falha_no_meio=Cancelado()), lambda: False)
    with pytest.raises(Cancelado):
        pl.processar([str(midias["wav"])])
    pl.emitir_cancelado()
    pl.emitir_cancelado()  # a saida forcada pode chamar de novo
    assert [e for e in eventos if e["tipo"] == "erro"] == [
        {"tipo": "erro", "arquivo": str(midias["wav"]), "codigo": "cancelado", "mensagem": "Cancelado pelo usuário."}
    ]


def test_nome_de_arquivo_que_nao_e_utf8(midias, tmp_path):
    entrada = os.fsdecode(os.fsencode(tmp_path) + b"/ruim\xff.wav")
    shutil.copy(midias["wav"], entrada)
    cod, ev = rodar([entrada], tmp_path / "o")
    assert cod == 0
    d = json.loads(open(ev[-1]["arquivos"]["json"], encoding="utf-8").read())
    assert d["arquivo_origem"] == entrada
    linha = cli.json_seguro(ev[-1])
    assert json.loads(linha.encode("utf-8"))["arquivo"] == entrada


def test_nome_longo_demais_nao_perde_o_trabalho_na_hora_de_salvar(midias, tmp_path):
    for nome in ("a" * 251 + ".wav", "é" * 125 + ".wav"):
        entrada = tmp_path / nome
        shutil.copy(midias["wav"], entrada)
        cod, ev = rodar([entrada], tmp_path / "o")
        assert cod == 0 and ev[-1]["tipo"] == "fim"
        assert Path(ev[-1]["arquivos"]["json"]).exists()
    assert (tmp_path / "o").is_dir()


def test_pasta_final_segue_a_umask_e_nao_a_do_mkdtemp(midias, tmp_path):
    antiga = os.umask(0o022)
    try:
        _, ev = rodar([midias["wav"]], tmp_path / "o")
    finally:
        os.umask(antiga)
    assert stat.S_IMODE(os.stat(ev[-1]["pasta"]).st_mode) == 0o755


def test_nome_ja_ocupado_por_pasta_vazia_ou_link_quebrado(midias, tmp_path):
    saida = tmp_path / "o"
    saida.mkdir()
    (saida / "som").mkdir()
    (saida / "som-2").symlink_to(tmp_path / "nao_existe")
    _, ev = rodar([midias["wav"]], saida)
    assert Path(ev[-1]["pasta"]).name == "som-3"
    assert (saida / "som-2").is_symlink() and list((saida / "som").iterdir()) == []


@pytest.mark.parametrize("ruim", ["arquivo", "sem_permissao"])
def test_pasta_de_saida_invalida_vira_erro_em_portugues(midias, tmp_path, ruim):
    if ruim == "arquivo":
        saida = tmp_path / "ocupado"
        saida.write_text("x")
    else:
        protegida = tmp_path / "protegida"
        protegida.mkdir(mode=0o555)
        saida = protegida / "filha"
    cod, ev = rodar([midias["wav"]], saida)
    assert cod == 1 and ev[-1]["codigo"] == "saida_invalida"
    assert str(saida) in ev[-1]["mensagem"]
    assert not [e for e in ev if e["tipo"] == "dispositivo"]  # falha antes de carregar o modelo


def test_falha_ao_gravar_vira_erro_em_portugues_e_nao_deixa_sobra(midias, tmp_path, monkeypatch):
    def cheio(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("transcritor.pipeline.gravar", cheio)
    saida = tmp_path / "o"
    cod, ev = rodar([midias["wav"]], saida)
    assert cod == 1 and ev[-1]["codigo"] == "saida_invalida" and "disco cheio" in ev[-1]["mensagem"]
    assert list(saida.iterdir()) == []
