import json
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts")

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from transcritor import gui
from transcritor.gui import CANCELADO, ERRO, NA_FILA, PRONTO, PROCESSANDO, Janela, LeitorLinhas

FALSA = os.path.join(os.path.dirname(__file__), "cli_falsa.py")


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cenario(app, tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRITOR_CLI", FALSA)
    monkeypatch.setenv("FALSA_SAIDA", str(tmp_path / "saida"))
    monkeypatch.setenv("FALSA_LOG", str(tmp_path / "log.jsonl"))
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    abertos = []
    monkeypatch.setattr(gui.QDesktopServices, "openUrl", lambda url: abertos.append(url.toLocalFile()) or True)
    cfg = QSettings(str(tmp_path / "cfg.ini"), QSettings.IniFormat)
    janelas = []

    class C:
        pass

    c = C()
    c.tmp, c.entrada, c.abertos, c.cfg = tmp_path, entrada, abertos, cfg

    def nova():
        j = Janela(QSettings(str(tmp_path / "cfg.ini"), QSettings.IniFormat))
        janelas.append(j)
        return j

    def arq(nome):
        p = entrada / nome
        p.write_bytes(b"x")
        return str(p)

    c.nova, c.arq = nova, arq
    yield c
    for j in janelas:
        j._encerrando = True
        j._matar_sincrono()
        j.deleteLater()


def esperar(cond, tempo=15):
    fim = time.monotonic() + tempo
    while time.monotonic() < fim:
        QApplication.processEvents()
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("tempo esgotado")


def estados(j):
    return [i.estado for i in j._itens]


def test_leitor_linha_partida():
    l = LeitorLinhas()
    assert l.alimentar(b'{"a":') == []
    assert l.alimentar(b'1}\n{"b"') == ['{"a":1}']
    assert l.alimentar(b':2}\n') == ['{"b":2}']
    assert l.descarregar() == []
    l.alimentar(b"sem quebra")
    assert l.descarregar() == ["sem quebra"]


def test_sucesso_e_texto_lido_do_txt(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("ok.mp4")])
    esperar(lambda: estados(j) == [PRONTO])
    it = j._itens[0]
    assert it.dispositivo.startswith("GPU")
    assert "large-v3-turbo" in it.dispositivo
    assert "ola mundo" in j.texto.toPlainText()
    assert j.btn_copiar.isEnabled() and j.btn_pasta.isEnabled() and j.btn_txt.isEnabled()
    j._copiar()
    assert "segundo trecho" in QApplication.clipboard().text()
    j._abrir_pasta(); j._abrir_txt()
    assert cenario.abertos[0].endswith("/ok") and cenario.abertos[1].endswith("ok.txt")
    # clicar de novo no item pronto le o .txt do disco
    open(it.txt, "w").write("conteudo editado\n")
    j._exibido = None
    j._selecionou(0)
    assert j.texto.toPlainText() == "conteudo editado\n"


def test_argumentos_para_a_cli(cenario):
    j = cenario.nova()
    j.cb_qualidade.setCurrentIndex(j.cb_qualidade.findData("small"))
    j.cb_idioma.setCurrentIndex(j.cb_idioma.findData("auto"))
    j.cb_disp.setCurrentIndex(j.cb_disp.findData("cpu"))
    j._saida = str(cenario.tmp / "minha saida")
    j.adicionar([cenario.arq("ok.mp4")])
    esperar(lambda: estados(j) == [PRONTO])
    args = json.loads(open(os.environ["FALSA_LOG"]).read().splitlines()[0])
    assert args[:7] == ["--json-eventos", "--modelo", "small", "--idioma", "auto", "--dispositivo", "cpu"]
    assert args[7:9] == ["--saida", str(cenario.tmp / "minha saida")]
    assert args[-2:] == ["--", cenario.entrada.joinpath("ok.mp4").as_posix()]


def test_erro_do_nucleo_e_fila_segue(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("erro.mp4"), cenario.arq("ok.mp4")])
    esperar(lambda: estados(j) == [ERRO, PRONTO])
    assert "faixa de audio" in j._itens[0].erro


def test_morte_subita_mostra_codigo_e_stderr(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("morte.mp4"), cenario.arq("ok.mp4")])
    esperar(lambda: estados(j) == [ERRO, PRONTO])
    e = j._itens[0].erro
    assert "Segmentation fault real" in e and "código de saída" in e


def test_linha_nao_json_ignorada(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("lixo.mp4")])
    esperar(lambda: estados(j) == [PRONTO])


def test_linha_partida_nao_perde_segmento(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("partida.mp4")])
    esperar(lambda: "linha partida ao meio" in j._itens[0].texto)
    esperar(lambda: estados(j) == [PRONTO])


def test_aviso_visivel_sem_dialogo(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("aviso.mp4")])
    esperar(lambda: estados(j) == [PRONTO])
    assert "GPU falhou" in j.lbl_aviso.text()


def test_cancelar_so_este_segue_a_fila(cenario, monkeypatch):
    j = cenario.nova()
    monkeypatch.setattr(j, "_perguntar_limpar_fila", lambda: "so_este")
    j.adicionar([cenario.arq("pausa.mp4"), cenario.arq("ok.mp4")])
    esperar(lambda: "ola mundo" in j._itens[0].texto)
    pid_antes = j._proc.processId()
    j._cancelar()
    esperar(lambda: estados(j) == [CANCELADO, PRONTO])
    with pytest.raises(ProcessLookupError):
        os.kill(pid_antes, 0)


def test_cancelar_e_limpar_fila(cenario, monkeypatch):
    j = cenario.nova()
    monkeypatch.setattr(j, "_perguntar_limpar_fila", lambda: "limpar")
    j.adicionar([cenario.arq("pausa.mp4"), cenario.arq("ok.mp4"), cenario.arq("ok2.mp4")])
    esperar(lambda: "ola mundo" in j._itens[0].texto)
    j._cancelar()
    esperar(lambda: j._proc is None)
    assert estados(j) == [CANCELADO, CANCELADO, CANCELADO]
    QApplication.processEvents()
    assert j._proc is None


def test_cancelar_voltar_nao_faz_nada(cenario, monkeypatch):
    j = cenario.nova()
    monkeypatch.setattr(j, "_perguntar_limpar_fila", lambda: "voltar")
    j.adicionar([cenario.arq("pausa.mp4"), cenario.arq("ok.mp4")])
    esperar(lambda: "ola mundo" in j._itens[0].texto)
    j._cancelar()
    for _ in range(10):
        QApplication.processEvents(); time.sleep(0.03)
    assert estados(j) == [PROCESSANDO, NA_FILA]


def test_kill_apos_prazo_quando_processo_ignora_sigterm(cenario, monkeypatch):
    j = cenario.nova()
    j.prazo_kill_ms = 400
    j.adicionar([cenario.arq("surdo.mp4")])
    esperar(lambda: "ola mundo" in j._itens[0].texto)
    pid = j._proc.processId()
    t0 = time.monotonic()
    j._cancelar()
    esperar(lambda: estados(j) == [CANCELADO])
    assert time.monotonic() - t0 >= 0.35
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_fechar_com_trabalho_pede_confirmacao_e_mata_filho(cenario, monkeypatch):
    j = cenario.nova()
    j.prazo_kill_ms = 500
    j.show()
    j.adicionar([cenario.arq("surdo.mp4")])
    esperar(lambda: "ola mundo" in j._itens[0].texto)
    pid = j._proc.processId()
    monkeypatch.setattr(j, "_confirmar_saida", lambda: False)
    assert j.close() is False
    assert j._proc is not None
    os.kill(pid, 0)
    monkeypatch.setattr(j, "_confirmar_saida", lambda: True)
    assert j.close() is True
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cli_inexistente_vira_erro(cenario, monkeypatch):
    monkeypatch.setenv("TRANSCRITOR_CLI", "/nao/existe/transcrever")
    j = cenario.nova()
    j.adicionar([cenario.arq("ok.mp4"), cenario.arq("ok2.mp4")])
    esperar(lambda: estados(j) == [ERRO, ERRO])
    assert "iniciar" in j._itens[0].erro


def test_duplicado_na_fila_ignorado_e_pasta_ignorada(cenario):
    j = cenario.nova()
    a = cenario.arq("pausa.mp4")
    assert j.adicionar([a, a, str(cenario.entrada)]) == 1


def test_opcoes_lembradas(cenario):
    j = cenario.nova()
    j.cb_qualidade.setCurrentIndex(j.cb_qualidade.findData("large-v3"))
    j.cb_disp.setCurrentIndex(j.cb_disp.findData("cpu"))
    j._saida = "/tmp/xyz"
    j._salvar_opcoes()
    j2 = cenario.nova()
    assert j2.cb_qualidade.currentData() == "large-v3"
    assert j2.cb_disp.currentData() == "cpu"
    assert j2.cb_idioma.currentData() == "pt"
    assert j2._saida == "/tmp/xyz"


def test_filtro_inclui_maiusculas_e_todos():
    assert "*.MOV" in gui.FILTRO_MIDIA and "*.mov" in gui.FILTRO_MIDIA
    assert gui.FILTRO_TODOS.endswith("(*)")


def test_limpar_concluidos(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("ok.mp4"), cenario.arq("pausa.mp4")])
    esperar(lambda: j._itens[0].estado == PRONTO and "ola mundo" in j._itens[1].texto)
    j._limpar_concluidos()
    assert len(j._itens) == 1 and j._itens[0].nome == "pausa.mp4" and j._atual == 0
    j._parar_processo()
    esperar(lambda: j._proc is None)


# Capturas de tela (so quando TRANSCRITOR_PNG_DIR esta definida)
destino = os.environ.get("TRANSCRITOR_PNG_DIR")


from pathlib import Path


def foto(j, nome):
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    j.grab().save(str(Path(destino) / f"{nome}.png"))


@pytest.mark.skipif(not destino, reason="defina TRANSCRITOR_PNG_DIR")
def test_capturas(cenario):
    j = cenario.nova()
    j.resize(1000, 740)
    j.show()
    foto(j, "1-inicial")
    j.adicionar([cenario.arq("pausa video longo.mov"), cenario.arq("aviso.mp3"), cenario.arq("erro.mp4")])
    esperar(lambda: "ola mundo" in j._itens[0].texto)
    foto(j, "2-processando")
    j._parar_processo()
    esperar(lambda: j._itens[0].estado == "cancelado" and j._itens[2].estado == ERRO)
    j.lista.setCurrentRow(1)
    foto(j, "3-concluido")
    j.lista.setCurrentRow(2)
    foto(j, "4-erro")
    j._encerrando = True


def test_crash_depois_do_fim_nao_vira_erro(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("crashfinal.mp4")])
    esperar(lambda: estados(j) == [PRONTO])
    assert "ola mundo" in j.texto.toPlainText() and j.btn_txt.isEnabled()


def test_nome_de_arquivo_nao_vira_html(cenario):
    j = cenario.nova()
    nome = "<h1>grande.mp4"
    j.adicionar([cenario.arq("erro" + nome)])
    esperar(lambda: estados(j) == [ERRO])
    for rotulo in (j.lbl_titulo, j.lbl_etapa, j.lbl_info, j.lbl_aviso, j.lbl_saida):
        assert rotulo.textFormat() == gui.Qt.PlainText
    assert j.lbl_titulo.text() == "erro" + nome
    assert "<h1>" not in j.lbl_titulo.toolTip() and "&lt;h1&gt;" in j.lbl_titulo.toolTip()
    assert "<h1>" not in j._linhas[0].toolTip()


def test_segundo_dispositivo_descarta_o_texto_da_gpu(cenario):
    j = cenario.nova()
    j.adicionar([cenario.arq("recuo.mp4")])
    esperar(lambda: "refeito em cpu" in j.texto.toPlainText())
    assert "ola mundo" not in j.texto.toPlainText()
    assert j._itens[0].dispositivo.startswith("CPU")
    assert "A GPU falhou." in j.lbl_aviso.text()
    esperar(lambda: estados(j) == [ERRO])


def test_soltar_arquivo_na_janela(cenario):
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    j = cenario.nova()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(cenario.arq("video.mp4")), QUrl("https://exemplo.com/remoto.mp3")])
    entrada = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    j.dragEnterEvent(entrada)
    assert entrada.isAccepted()
    j.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    esperar(lambda: estados(j) == [PRONTO])
    assert [i.nome for i in j._itens] == ["video.mp4"]
