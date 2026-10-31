"""Interface grafica do Transcritor (PySide6).

A janela nao carrega o modelo: roda a CLI (bin/transcrever --json-eventos) como
subprocesso, um por arquivo, e le os eventos NDJSON. Cancelar mata o processo de
verdade e crash nativo da GPU nao derruba a janela.

O texto transcrito e dado, nunca instrucao: so e exibido como texto simples.
"""

from __future__ import annotations

import html
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

RAIZ = Path(__file__).resolve().parent.parent

NA_FILA, PROCESSANDO, PRONTO, ERRO, CANCELADO = "fila", "processando", "pronto", "erro", "cancelado"
NOME_ESTADO = {
    NA_FILA: "Na fila",
    PROCESSANDO: "Processando",
    PRONTO: "Pronto",
    ERRO: "Erro",
    CANCELADO: "Cancelado",
}

ETAPAS = {
    "analisando": "Analisando o arquivo",
    "extraindo": "Extraindo o áudio",
    "carregando_modelo": "Carregando o modelo",
    "baixando_modelo": "Baixando o modelo (só na primeira vez, pode demorar)",
    "transcrevendo": "Transcrevendo",
    "salvando": "Salvando",
}
ETAPAS_SEM_PERCENTUAL = {"analisando", "extraindo", "carregando_modelo", "baixando_modelo", "salvando"}

QUALIDADES = [
    ("Automática", "auto", "Usa o modelo large-v3-turbo, na placa de vídeo se houver, senão no processador."),
    ("Rápida", "small", "Modelo small: mais rápido e mais leve, erra mais."),
    ("Equilibrada", "large-v3-turbo", "Modelo large-v3-turbo: bom equilíbrio entre velocidade e acerto."),
    ("Máxima", "large-v3", "Modelo large-v3: o mais pesado e o mais lento."),
]
IDIOMAS = [
    ("Português", "pt"),
    ("Detectar automaticamente", "auto"),
    ("Inglês", "en"),
    ("Espanhol", "es"),
]
DISPOSITIVOS = [("Automático", "auto"), ("GPU", "cuda"), ("CPU", "cpu")]

EXT_MIDIA = (
    "mp4 m4v mov mkv avi webm wmv flv mpg mpeg ts mts m2ts 3gp ogv "
    "mp3 m4a aac wav flac ogg oga opus wma aiff aif amr mka"
).split()
FILTRO_MIDIA = "Vídeo e áudio (" + " ".join(f"*.{e} *.{e.upper()}" for e in EXT_MIDIA) + ")"
FILTRO_TODOS = "Todos os arquivos (*)"

TAMANHO_STDERR = 8000
LIMITE_TXT_LIDO = 20_000_000


def _caminho_cli() -> str:
    return os.environ.get("TRANSCRITOR_CLI") or str(RAIZ / "bin" / "transcrever")


def dica_literal(texto: str) -> str:
    """Dica de ferramenta adivinha HTML no texto. Nome de arquivo e so texto."""
    return f"<span>{html.escape(texto)}</span>"


def formatar_tempo(segundos: float) -> str:
    s = max(0, int(segundos))
    h, resto = divmod(s, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class LeitorLinhas:
    """Junta pedacos de bytes e devolve so linhas completas, sem perder linha partida."""

    def __init__(self) -> None:
        self._resto = b""

    def alimentar(self, dados: bytes) -> list[str]:
        self._resto += dados
        *linhas, self._resto = self._resto.split(b"\n")
        return [l.decode("utf-8", "replace").rstrip("\r") for l in linhas]

    def descarregar(self) -> list[str]:
        resto, self._resto = self._resto, b""
        return [resto.decode("utf-8", "replace")] if resto.strip() else []


@dataclass
class Item:
    caminho: str
    estado: str = NA_FILA
    etapa: str = ""
    progresso: float | None = None  # None = indeterminado
    dispositivo: str = ""
    idioma: str = ""
    midia: str = ""
    avisos: list[str] = field(default_factory=list)
    texto: str = ""
    erro: str = ""
    pasta: str = ""
    txt: str = ""
    vazio: bool = False
    inicio: float = 0.0
    fim: float = 0.0
    duracao_total: float = 0.0
    visto_dispositivo: bool = False

    @property
    def nome(self) -> str:
        return Path(self.caminho).name


class AreaSoltar(QFrame):
    """Area grande de soltar arquivos. Enter ou espaco abre o dialogo."""

    def __init__(self, ao_ativar) -> None:
        super().__init__()
        self._ao_ativar = ao_ativar
        self.setObjectName("areaSoltar")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Area para soltar arquivos")
        self.setMinimumHeight(110)
        self.setStyleSheet(
            "#areaSoltar { border: 2px dashed palette(mid); border-radius: 10px; }"
            "#areaSoltar[arrastando=true] { border-color: palette(highlight); }"
            "#areaSoltar:focus { border-color: palette(highlight); }"
        )
        lay = QVBoxLayout(self)
        t1 = QLabel("Solte aqui vídeos ou áudios")
        f = t1.font()
        f.setPointSizeF(f.pointSizeF() * 1.4)
        f.setBold(True)
        t1.setFont(f)
        t1.setAlignment(Qt.AlignCenter)
        t2 = QLabel("ou clique em Escolher arquivos. Pode soltar vários de uma vez.")
        t2.setAlignment(Qt.AlignCenter)
        t2.setWordWrap(True)
        for t in (t1, t2):
            t.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(t1)
        lay.addWidget(t2)

    def marcar_arrasto(self, ativo: bool) -> None:
        self.setProperty("arrastando", ativo)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        if ev.button() == Qt.LeftButton:
            self._ao_ativar()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._ao_ativar()
        else:
            super().keyPressEvent(ev)


class Janela(QWidget):
    prazo_kill_ms = 5000

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Transcritor")
        self.setAcceptDrops(True)
        self._settings = settings or QSettings("Transcritor", "Transcritor")
        self._itens: list[Item] = []
        self._linhas: list[QListWidgetItem] = []
        self._atual: int | None = None
        self._exibido: int | None = None
        self._proc: QProcess | None = None
        self._leitor = LeitorLinhas()
        self._stderr = b""
        self._recebeu_fim = False
        self._recebeu_erro: dict | None = None
        self._cancelando = False
        self._encerrando = False
        self._saida: str = self._settings.value("saida", "", str) or ""
        self._saida_padrao = self._descobrir_saida_padrao()
        self._montar()
        self._carregar_opcoes()
        self._cronometro = QTimer(self)
        self._cronometro.setInterval(500)
        self._cronometro.timeout.connect(self._atualizar_painel)
        self._timer_kill = QTimer(self)
        self._timer_kill.setSingleShot(True)
        self._timer_kill.timeout.connect(self._matar_forcado)
        self._atualizar_painel()
        self.setMinimumSize(max(720, self.minimumSizeHint().width()), max(560, self.minimumSizeHint().height()))
        self.resize(1000, 740)

    # ---------------------------------------------------------------- montagem

    @staticmethod
    def _descobrir_saida_padrao() -> str:
        try:
            from .pipeline import pasta_saida_padrao

            return str(pasta_saida_padrao())
        except Exception:
            return str(Path.home() / "Documents" / "Transcricoes")

    def _montar(self) -> None:
        raiz = QVBoxLayout(self)

        self.area = AreaSoltar(self._escolher_arquivos)
        raiz.addWidget(self.area)

        linha_btn = QHBoxLayout()
        self.btn_escolher = QPushButton("&Escolher arquivos...")
        self.btn_escolher.clicked.connect(self._escolher_arquivos)
        self.btn_limpar = QPushButton("&Limpar concluídos")
        self.btn_limpar.clicked.connect(self._limpar_concluidos)
        linha_btn.addWidget(self.btn_escolher)
        linha_btn.addWidget(self.btn_limpar)
        linha_btn.addStretch(1)
        raiz.addLayout(linha_btn)

        # opcoes
        self.cb_qualidade = QComboBox()
        for rotulo, valor, dica in QUALIDADES:
            self.cb_qualidade.addItem(rotulo, valor)
            self.cb_qualidade.setItemData(self.cb_qualidade.count() - 1, dica, Qt.ToolTipRole)
        self.cb_idioma = QComboBox()
        for rotulo, valor in IDIOMAS:
            self.cb_idioma.addItem(rotulo, valor)
        self.cb_disp = QComboBox()
        for rotulo, valor in DISPOSITIVOS:
            self.cb_disp.addItem(rotulo, valor)
        self.lbl_saida = QLabel()
        self.lbl_saida.setTextFormat(Qt.PlainText)
        self.lbl_saida.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_saida.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.btn_saida = QPushButton("Trocar pasta...")
        self.btn_saida.clicked.connect(self._trocar_saida)
        self.cb_qualidade.currentIndexChanged.connect(self._salvar_opcoes)
        self.cb_idioma.currentIndexChanged.connect(self._salvar_opcoes)
        self.cb_disp.currentIndexChanged.connect(self._salvar_opcoes)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        for rotulo, w in (("&Qualidade:", self.cb_qualidade), ("&Idioma:", self.cb_idioma), ("&Dispositivo:", self.cb_disp)):
            form.addRow(rotulo, w)
        saida_l = QHBoxLayout()
        saida_l.addWidget(self.lbl_saida, 1)
        saida_l.addWidget(self.btn_saida)
        form.addRow("Salvar em:", saida_l)
        raiz.addLayout(form)

        # fila + detalhe
        self.lista = QListWidget()
        self.lista.setMinimumWidth(220)
        self.lista.setAccessibleName("Fila de arquivos")
        self.lista.currentRowChanged.connect(self._selecionou)

        painel = QWidget()
        pl = QVBoxLayout(painel)
        pl.setContentsMargins(0, 0, 0, 0)
        self.lbl_titulo = QLabel()
        f = self.lbl_titulo.font()
        f.setBold(True)
        self.lbl_titulo.setFont(f)
        self.lbl_titulo.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_titulo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_etapa = QLabel()
        self.lbl_etapa.setWordWrap(True)
        self.barra = QProgressBar()
        self.barra.setRange(0, 1000)
        self.barra.setValue(0)
        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_aviso = QLabel()
        self.lbl_aviso.setWordWrap(True)
        self.lbl_aviso.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # nome de arquivo e mensagem sao dados: o QLabel nao pode interpretar como HTML
        for rotulo in (self.lbl_titulo, self.lbl_etapa, self.lbl_info, self.lbl_aviso):
            rotulo.setTextFormat(Qt.PlainText)
        self.texto = QPlainTextEdit()
        self.texto.setReadOnly(True)
        self.texto.setPlaceholderText("A transcrição aparece aqui, trecho por trecho.")
        self.texto.setAccessibleName("Texto transcrito")
        for w in (self.lbl_titulo, self.lbl_etapa, self.barra, self.lbl_info, self.lbl_aviso):
            pl.addWidget(w)
        pl.addWidget(self.texto, 1)

        acoes = QHBoxLayout()
        self.btn_copiar = QPushButton("C&opiar texto")
        self.btn_copiar.clicked.connect(self._copiar)
        self.btn_pasta = QPushButton("Abrir &pasta")
        self.btn_pasta.clicked.connect(self._abrir_pasta)
        self.btn_txt = QPushButton("Abrir .&txt")
        self.btn_txt.clicked.connect(self._abrir_txt)
        self.btn_cancelar = QPushButton("&Cancelar")
        self.btn_cancelar.clicked.connect(self._cancelar)
        for b in (self.btn_copiar, self.btn_pasta, self.btn_txt):
            acoes.addWidget(b)
        acoes.addStretch(1)
        acoes.addWidget(self.btn_cancelar)
        pl.addLayout(acoes)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.lista)
        split.addWidget(painel)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setChildrenCollapsible(False)
        raiz.addWidget(split, 1)

        QShortcut(QKeySequence.Open, self, activated=self._escolher_arquivos)
        self.setTabOrder(self.area, self.btn_escolher)

    # ---------------------------------------------------------------- opcoes

    def _carregar_opcoes(self) -> None:
        for cb, chave in ((self.cb_qualidade, "qualidade"), (self.cb_idioma, "idioma"), (self.cb_disp, "dispositivo")):
            i = cb.findData(self._settings.value(chave, "", str))
            if i >= 0:
                cb.blockSignals(True)  # senao o primeiro combo regrava os outros ainda nao lidos
                cb.setCurrentIndex(i)
                cb.blockSignals(False)
        self._mostrar_saida()

    def _salvar_opcoes(self) -> None:
        self._settings.setValue("qualidade", self.cb_qualidade.currentData())
        self._settings.setValue("idioma", self.cb_idioma.currentData())
        self._settings.setValue("dispositivo", self.cb_disp.currentData())
        self._settings.setValue("saida", self._saida)

    def _mostrar_saida(self) -> None:
        if self._saida:
            self.lbl_saida.setText(self._saida)
        else:
            self.lbl_saida.setText(f"{self._saida_padrao} (padrão)")
        self.lbl_saida.setToolTip(dica_literal(self.lbl_saida.text()))

    def _trocar_saida(self) -> None:
        inicio = self._saida or str(Path(self._saida_padrao).parent)
        pasta = QFileDialog.getExistingDirectory(self, "Pasta das transcrições", inicio)
        if pasta:
            self._saida = pasta
            self._mostrar_saida()
            self._salvar_opcoes()

    # ---------------------------------------------------------------- entrada de arquivos

    def _escolher_arquivos(self) -> None:
        caminhos, _ = QFileDialog.getOpenFileNames(
            self, "Escolher vídeos ou áudios", str(Path.home()), f"{FILTRO_MIDIA};;{FILTRO_TODOS}"
        )
        self.adicionar(caminhos)

    def adicionar(self, caminhos) -> int:
        novos = 0
        ativos = {i.caminho for i in self._itens if i.estado in (NA_FILA, PROCESSANDO)}
        for c in caminhos:
            c = os.path.abspath(os.path.expanduser(str(c)))
            if c in ativos or os.path.isdir(c):
                continue
            ativos.add(c)
            item = Item(c)
            self._itens.append(item)
            linha = QListWidgetItem()
            linha.setToolTip(dica_literal(c))
            self._linhas.append(linha)
            self.lista.addItem(linha)
            self._atualizar_linha(len(self._itens) - 1)
            novos += 1
        if novos and self._exibido is None:
            self.lista.setCurrentRow(len(self._itens) - novos)
        self._proximo()
        self._atualizar_painel()
        return novos

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls() and any(u.isLocalFile() for u in ev.mimeData().urls()):
            ev.acceptProposedAction()
            self.area.marcar_arrasto(True)

    def dragLeaveEvent(self, ev) -> None:
        self.area.marcar_arrasto(False)

    def dropEvent(self, ev) -> None:
        self.area.marcar_arrasto(False)
        self.adicionar([u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()])
        ev.acceptProposedAction()

    # ---------------------------------------------------------------- fila

    def _atualizar_linha(self, i: int) -> None:
        it = self._itens[i]
        self._linhas[i].setText(f"{NOME_ESTADO[it.estado]}: {it.nome}")

    def _limpar_concluidos(self) -> None:
        manter = [i for i, it in enumerate(self._itens) if it.estado in (NA_FILA, PROCESSANDO)]
        if len(manter) == len(self._itens):
            return
        atual = self._atual
        self.lista.blockSignals(True)
        self.lista.clear()
        itens = [self._itens[i] for i in manter]
        self._linhas = []
        for it in itens:
            li = QListWidgetItem()
            li.setToolTip(dica_literal(it.caminho))
            self._linhas.append(li)
            self.lista.addItem(li)
        self._atual = manter.index(atual) if atual is not None and atual in manter else None
        self._itens = itens
        for i in range(len(itens)):
            self._atualizar_linha(i)
        self.lista.blockSignals(False)
        self._exibido = None
        if itens:
            self.lista.setCurrentRow(self._atual if self._atual is not None else 0)
        self._atualizar_painel()

    def _selecionou(self, linha: int) -> None:
        self._exibido = linha if 0 <= linha < len(self._itens) else None
        self._recarregar_texto()
        self._atualizar_painel()

    def _proximo(self) -> None:
        if self._proc is not None or self._encerrando:
            return
        for i, it in enumerate(self._itens):
            if it.estado == NA_FILA:
                self._iniciar(i)
                return

    # ---------------------------------------------------------------- processo

    def _argumentos(self, caminho: str) -> list[str]:
        args = [
            "--json-eventos",
            "--modelo", self.cb_qualidade.currentData(),
            "--idioma", self.cb_idioma.currentData(),
            "--dispositivo", self.cb_disp.currentData(),
        ]
        if self._saida:
            args += ["--saida", self._saida]
        return args + ["--", caminho]

    def _iniciar(self, i: int) -> None:
        it = self._itens[i]
        it.estado = PROCESSANDO
        it.etapa = "analisando"
        it.progresso = None
        it.inicio = time.monotonic()
        self._atual = i
        self._cancelando = False
        self._recebeu_fim = False
        self._recebeu_erro = None
        self._stderr = b""
        self._leitor = LeitorLinhas()
        self._atualizar_linha(i)
        if self._exibido in (None, i - 1) or self._exibido == i:
            self.lista.setCurrentRow(i)
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.SeparateChannels)
        p.readyReadStandardOutput.connect(self._ler_stdout)
        p.readyReadStandardError.connect(self._ler_stderr)
        p.finished.connect(self._terminou)
        p.errorOccurred.connect(self._erro_processo)
        self._proc = p
        p.setProgram(_caminho_cli())
        p.setArguments(self._argumentos(it.caminho))
        p.start()
        self._cronometro.start()
        self._atualizar_painel()

    def _ler_stdout(self) -> None:
        if self._proc is None:
            return
        for linha in self._leitor.alimentar(bytes(self._proc.readAllStandardOutput())):
            self._tratar_linha(linha)

    def _ler_stderr(self) -> None:
        if self._proc is None:
            return
        self._stderr = (self._stderr + bytes(self._proc.readAllStandardError()))[-TAMANHO_STDERR:]

    def _tratar_linha(self, linha: str) -> None:
        linha = linha.strip()
        if not linha or self._atual is None:
            return
        try:
            ev = json.loads(linha)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        self._tratar_evento(ev)

    def _tratar_evento(self, ev: dict) -> None:
        it = self._itens[self._atual]
        visivel = self._exibido == self._atual
        tipo = ev.get("tipo")
        try:
            if tipo == "etapa":
                it.etapa = str(ev.get("etapa", ""))
                it.progresso = None if it.etapa in ETAPAS_SEM_PERCENTUAL else (it.progresso or 0.0)
            elif tipo == "midia":
                dur = float(ev.get("duracao") or 0)
                it.duracao_total = dur
                canais = int(ev.get("canais") or 0)
                kind = "Vídeo" if ev.get("eh_video") else "Áudio"
                it.midia = f"{kind}, {formatar_tempo(dur)}, {ev.get('codec_audio', '?')}, {canais} canal(is)"
            elif tipo == "dispositivo":
                if it.visto_dispositivo:
                    # a GPU falhou e a transcricao recomecou: descarta o que veio antes
                    it.texto = ""
                    it.progresso = 0.0
                    if visivel:
                        self.texto.clear()
                it.visto_dispositivo = True
                disp = "GPU" if ev.get("dispositivo") == "cuda" else "CPU"
                it.dispositivo = f"{disp}, modelo {ev.get('modelo', '?')} ({ev.get('compute_type', '?')})"
            elif tipo == "idioma":
                it.idioma = f"{ev.get('idioma', '?')} ({float(ev.get('probabilidade') or 0) * 100:.0f}%)"
            elif tipo == "aviso":
                msg = str(ev.get("mensagem", "")).strip()
                if msg:
                    it.avisos.append(msg)
            elif tipo == "segmento":
                trecho = str(ev.get("texto", "")).strip()
                it.progresso = min(1.0, max(0.0, float(ev.get("progresso") or 0)))
                if trecho:
                    it.texto += trecho + "\n"
                    if visivel:
                        self._anexar(trecho + "\n")
            elif tipo == "fim":
                self._recebeu_fim = True
                arqs = ev.get("arquivos") or {}
                it.pasta = str(ev.get("pasta") or "")
                it.txt = str(arqs.get("txt") or "") if isinstance(arqs, dict) else ""
                it.vazio = bool(ev.get("vazio"))
            elif tipo == "erro":
                self._recebeu_erro = ev
        except (TypeError, ValueError):
            return
        self._atualizar_painel()

    def _anexar(self, texto: str) -> None:
        sb = self.texto.verticalScrollBar()
        no_fim = sb.value() >= sb.maximum() - 4
        valor = sb.value()
        cur = QTextCursor(self.texto.document())
        cur.movePosition(QTextCursor.End)
        cur.insertText(texto)
        sb.setValue(sb.maximum() if no_fim else valor)

    def _erro_processo(self, erro) -> None:
        if erro == QProcess.FailedToStart and self._proc is not None:
            self._finalizar(-1, falha_inicio=True)

    def _terminou(self, codigo: int, _status=None) -> None:
        self._finalizar(codigo)

    def _finalizar(self, codigo: int, falha_inicio: bool = False) -> None:
        p, i = self._proc, self._atual
        if p is None or i is None:
            return
        if not falha_inicio:
            self._ler_stdout()
            self._ler_stderr()
            for linha in self._leitor.descarregar():
                self._tratar_linha(linha)
        self._timer_kill.stop()
        self._proc = None
        self._atual = None
        p.deleteLater()
        it = self._itens[i]
        if self._recebeu_fim and not self._recebeu_erro:
            # a saida ja foi publicada: um crash ao encerrar a GPU nao desfaz o trabalho
            it.estado = PRONTO
            it.progresso = 1.0
            it.etapa = ""
            self._carregar_do_txt(it)
        elif self._cancelando or codigo == 130 or (self._recebeu_erro or {}).get("codigo") == "cancelado":
            it.estado = CANCELADO
            it.etapa = ""
        else:
            it.estado = ERRO
            it.etapa = ""
            it.erro = self._montar_erro(it, codigo, falha_inicio)
        it.fim = time.monotonic()
        self._atualizar_linha(i)
        self._cancelando = False
        if not any(x.estado == PROCESSANDO for x in self._itens):
            self._cronometro.stop()
        if self._exibido == i:
            self._recarregar_texto()
        self._atualizar_painel()
        if not self._encerrando:
            self._proximo()

    def _montar_erro(self, it: Item, codigo: int, falha_inicio: bool) -> str:
        if self._recebeu_erro:
            return str(self._recebeu_erro.get("mensagem") or "Erro sem detalhe.")
        if falha_inicio:
            return f"Não foi possível iniciar o programa de transcrição ({_caminho_cli()})."
        cauda = self._stderr.decode("utf-8", "replace").strip()
        cauda = "\n".join(cauda.splitlines()[-6:])
        msg = f"O processo terminou de forma inesperada (código de saída {codigo})."
        return f"{msg}\n{cauda}" if cauda else msg

    def _carregar_do_txt(self, it: Item) -> None:
        if it.txt:
            lido = self._ler_txt(it.txt)
            if lido is not None:
                it.texto = lido

    @staticmethod
    def _ler_txt(caminho: str) -> str | None:
        try:
            p = Path(caminho)
            if p.stat().st_size > LIMITE_TXT_LIDO:
                return None
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # ---------------------------------------------------------------- cancelar e encerrar

    def _perguntar_limpar_fila(self) -> str:
        """Devolve 'so_este', 'limpar' ou 'voltar'."""
        caixa = QMessageBox(self)
        caixa.setIcon(QMessageBox.Question)
        caixa.setWindowTitle("Cancelar")
        caixa.setText("Há mais arquivos esperando na fila.")
        caixa.setInformativeText("Cancelar só o arquivo atual e seguir com os outros, ou cancelar tudo?")
        b_este = caixa.addButton("Só este arquivo", QMessageBox.AcceptRole)
        b_tudo = caixa.addButton("Cancelar tudo", QMessageBox.DestructiveRole)
        caixa.addButton("Voltar", QMessageBox.RejectRole)
        caixa.exec()
        clicado = caixa.clickedButton()
        return "so_este" if clicado is b_este else "limpar" if clicado is b_tudo else "voltar"

    def _cancelar(self) -> None:
        if self._proc is None:
            return
        restantes = [i for i, it in enumerate(self._itens) if it.estado == NA_FILA]
        escolha = "so_este"
        if restantes:
            escolha = self._perguntar_limpar_fila()
            if escolha == "voltar":
                return
        if escolha == "limpar":
            for i in restantes:
                self._itens[i].estado = CANCELADO
                self._atualizar_linha(i)
        self._parar_processo()
        self._atualizar_painel()

    def _parar_processo(self) -> None:
        p = self._proc
        if p is None:
            return
        self._cancelando = True
        if p.state() != QProcess.NotRunning:
            p.terminate()
            self._timer_kill.start(self.prazo_kill_ms)

    def _matar_forcado(self) -> None:
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    def _matar_sincrono(self) -> None:
        """Usado ao sair: nao volta antes de o filho morrer."""
        p = self._proc
        if p is None or p.state() == QProcess.NotRunning:
            return
        self._encerrando = True
        self._timer_kill.stop()
        p.terminate()
        if not p.waitForFinished(self.prazo_kill_ms):
            p.kill()
            p.waitForFinished(3000)

    def _confirmar_saida(self) -> bool:
        r = QMessageBox.question(
            self,
            "Sair do Transcritor",
            "Há uma transcrição em andamento. Sair agora cancela o que está sendo feito. Sair mesmo?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return r == QMessageBox.Yes

    def trabalhando(self) -> bool:
        return self._proc is not None or any(it.estado == NA_FILA for it in self._itens)

    def closeEvent(self, ev) -> None:
        if self.trabalhando() and not self._encerrando and not self._confirmar_saida():
            ev.ignore()
            return
        self._encerrando = True
        self._matar_sincrono()
        ev.accept()

    # ---------------------------------------------------------------- exibicao

    def _item_exibido(self) -> Item | None:
        if self._exibido is not None and 0 <= self._exibido < len(self._itens):
            return self._itens[self._exibido]
        return None

    def _recarregar_texto(self) -> None:
        it = self._item_exibido()
        self.texto.clear()
        if it is None:
            return
        if it.estado == PRONTO and it.txt:
            lido = self._ler_txt(it.txt)
            if lido is not None:
                it.texto = lido
        self.texto.setPlainText(it.texto)

    def _atualizar_painel(self) -> None:
        it = self._item_exibido()
        rodando = self._proc is not None
        self.btn_cancelar.setEnabled(rodando and not self._cancelando)
        self.btn_limpar.setEnabled(any(x.estado in (PRONTO, ERRO, CANCELADO) for x in self._itens))
        if it is None:
            self.lbl_titulo.setText("Nenhum arquivo ainda")
            self.lbl_etapa.setText("Solte um vídeo ou áudio acima para começar.")
            self.lbl_info.clear()
            self.lbl_aviso.clear()
            self.barra.setRange(0, 1000)
            self.barra.setValue(0)
            self.barra.setFormat("")
            for b in (self.btn_copiar, self.btn_pasta, self.btn_txt):
                b.setEnabled(False)
            return

        self.lbl_titulo.setText(it.nome)
        self.lbl_titulo.setToolTip(dica_literal(it.caminho))
        decorrido = ""
        if it.estado == PROCESSANDO:
            decorrido = formatar_tempo(time.monotonic() - it.inicio)
        elif it.fim and it.inicio:
            decorrido = formatar_tempo(it.fim - it.inicio)

        if it.estado == NA_FILA:
            etapa = "Na fila, esperando a vez."
        elif it.estado == PROCESSANDO:
            etapa = ETAPAS.get(it.etapa, "Trabalhando")
            if self._cancelando:
                etapa = "Cancelando..."
        elif it.estado == PRONTO:
            etapa = "Pronto. Sem fala detectada neste arquivo." if it.vazio else "Pronto."
        elif it.estado == CANCELADO:
            etapa = "Cancelado."
        else:
            etapa = "Não foi possível transcrever este arquivo."
        self.lbl_etapa.setText(etapa)

        # barra
        if it.estado == PROCESSANDO and it.progresso is None:
            self.barra.setRange(0, 0)
        else:
            self.barra.setRange(0, 1000)
            val = 1.0 if it.estado == PRONTO else (it.progresso or 0.0)
            self.barra.setValue(int(val * 1000))
            self.barra.setFormat(f"{int(val * 100)}%" if it.estado in (PROCESSANDO, PRONTO) else "")

        partes = []
        if it.midia:
            partes.append(it.midia)
        if it.dispositivo:
            partes.append(f"Usando {it.dispositivo}")
        if it.idioma:
            partes.append(f"Idioma: {it.idioma}")
        if decorrido:
            partes.append(f"Tempo: {decorrido}")
        if it.estado == ERRO and it.erro:
            partes.append(f"Erro: {it.erro}")
        self.lbl_info.setText("\n".join(partes))
        self.lbl_aviso.setText("\n".join(f"Aviso: {a}" for a in it.avisos))

        self.btn_copiar.setEnabled(bool(it.texto.strip()))
        self.btn_pasta.setEnabled(it.estado == PRONTO and bool(it.pasta))
        self.btn_txt.setEnabled(it.estado == PRONTO and bool(it.txt))

    # ---------------------------------------------------------------- acoes

    def _copiar(self) -> None:
        it = self._item_exibido()
        if it and it.texto:
            QGuiApplication.clipboard().setText(it.texto.strip())

    def _abrir_pasta(self) -> None:
        it = self._item_exibido()
        if it and it.pasta:
            QDesktopServices.openUrl(QUrl.fromLocalFile(it.pasta))

    def _abrir_txt(self) -> None:
        it = self._item_exibido()
        if it and it.txt:
            QDesktopServices.openUrl(QUrl.fromLocalFile(it.txt))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("Transcritor")
    app.setDesktopFileName("transcritor")
    janela = Janela()
    app.aboutToQuit.connect(janela._matar_sincrono)

    def _sair(*_):
        janela._encerrando = True
        app.quit()

    signal.signal(signal.SIGINT, _sair)
    signal.signal(signal.SIGTERM, _sair)
    pulso = QTimer()
    pulso.start(250)
    pulso.timeout.connect(lambda: None)  # deixa o Python tratar sinais

    janela.show()
    janela.adicionar(argv[1:])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
