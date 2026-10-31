#!/usr/bin/env bash
# Instala o Transcritor. Pode ser rodado de qualquer pasta e repetido sem problema.
#   --sem-gpu   não baixa as bibliotecas da NVIDIA (cerca de 1,8 GB), roda só no processador
#   --dev       instala também o que é preciso para rodar os testes
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

COM_GPU=1
COM_DEV=0
for arg in "$@"; do
    case "$arg" in
        --sem-gpu) COM_GPU=0 ;;
        --dev) COM_DEV=1 ;;
        -h|--help) sed -n '2,4p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "ERRO: opção desconhecida: $arg (use --help)" >&2; exit 2 ;;
    esac
done

# Comando que instala o que falta, conforme o gerenciador de pacotes da distro.
comando_pacotes() {
    if command -v apt-get >/dev/null 2>&1; then
        echo "sudo apt install ffmpeg python3-venv libxcb-cursor0"
    elif command -v dnf >/dev/null 2>&1; then
        echo "sudo dnf install ffmpeg-free python3"
    elif command -v pacman >/dev/null 2>&1; then
        echo "sudo pacman -S --needed ffmpeg python"
    elif command -v zypper >/dev/null 2>&1; then
        echo "sudo zypper install ffmpeg python3"
    fi
}

mostrar_como_instalar() {
    local cmd
    cmd="$(comando_pacotes)"
    if [ -n "$cmd" ]; then
        echo "Instale com este comando e rode este script de novo:" >&2
        echo "  $cmd" >&2
    else
        echo "Instale esses pacotes pela loja ou gerenciador da sua distro e rode este script de novo." >&2
    fi
}

# 1. O que precisa existir no sistema
faltando=""
for ferramenta in ffmpeg ffprobe; do
    command -v "$ferramenta" >/dev/null 2>&1 || faltando="$faltando $ferramenta"
done
if ! command -v python3 >/dev/null 2>&1; then
    faltando="$faltando python3"
elif ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
    faltando="$faltando python3-venv"
fi
if [ -n "$faltando" ]; then
    echo "ERRO: falta no seu sistema:$faltando" >&2
    mostrar_como_instalar
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "ERRO: o Transcritor precisa do Python 3.10 ou mais novo. Você tem $(python3 -V)." >&2
    exit 1
fi

# 2. Ambiente virtual
if [ -x ".venv/bin/python" ] && .venv/bin/python -c "import sys" >/dev/null 2>&1; then
    echo "Ambiente .venv já existe, mantendo."
else
    echo "Criando o ambiente .venv ..."
    rm -rf .venv
    python3 -m venv .venv
fi

PY=".venv/bin/python"
"$PY" -m pip install --upgrade pip >/dev/null 2>&1 || echo "AVISO: não consegui atualizar o pip, seguindo com o atual."

# 3. Dependências
echo "Instalando as dependências, pode levar alguns minutos ..."
"$PY" -m pip install -r requirements.txt
if [ "$COM_DEV" -eq 1 ]; then
    "$PY" -m pip install -r requirements-dev.txt
fi

# 4. Placa NVIDIA (opcional)
if [ "$COM_GPU" -eq 1 ] && nvidia-smi >/dev/null 2>&1; then
    echo "Placa NVIDIA encontrada, baixando as bibliotecas dela (cerca de 1,8 GB) ..."
    "$PY" -m pip install -r requirements-gpu.txt
else
    echo "Sem placa NVIDIA em uso: o Transcritor vai rodar no processador, um pouco mais devagar."
fi

# 5. Confere se a janela vai conseguir abrir
if ! "$PY" -c "from PySide6 import QtWidgets" >/dev/null 2>&1; then
    echo "AVISO: a interface gráfica não carregou, falta alguma biblioteca do sistema." >&2
    mostrar_como_instalar
fi

# 6. Comandos
chmod +x "$DIR/bin/transcrever" "$DIR/bin/transcritor"
mkdir -p "$HOME/.local/bin"
ln -sfn "$DIR/bin/transcrever" "$HOME/.local/bin/transcrever"
ln -sfn "$DIR/bin/transcritor" "$HOME/.local/bin/transcritor"

# 7. Atalho no menu (sem MimeType: o app não vira padrão para abrir vídeo)
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/transcritor.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Transcritor
GenericName=Transcrição de áudio e vídeo
Comment=Transcreve vídeo ou áudio na sua máquina
Exec="$DIR/bin/transcritor" %F
Icon=$DIR/assets/transcritor.svg
Terminal=false
Categories=AudioVideo;Audio;
StartupWMClass=transcritor
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true

echo
echo "Pronto! Para abrir o Transcritor, procure por Transcritor no menu de aplicativos."
case ":$PATH:" in
    *":$HOME/.local/bin:"*) echo "Ou digite no terminal: transcritor" ;;
    *) echo "Ou digite no terminal: $DIR/bin/transcritor" ;;
esac
echo "Na primeira transcrição ele baixa o modelo (cerca de 1,6 GB). Depois funciona sem internet."
