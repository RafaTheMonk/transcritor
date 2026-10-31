#!/usr/bin/env bash
# Remove os comandos, o atalho do menu e o ambiente .venv do Transcritor.
# Não apaga suas transcrições nem os modelos baixados.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Só remove o que aponta para esta pasta, para não mexer em outra instalação.
for nome in transcrever transcritor; do
    alvo="$HOME/.local/bin/$nome"
    if [ -L "$alvo" ] && [ "$(readlink -f "$alvo")" = "$DIR/bin/$nome" ]; then
        rm -f "$alvo"
    fi
done

APPS="$HOME/.local/share/applications"
if [ -f "$APPS/transcritor.desktop" ] && grep -qF "$DIR/bin/transcritor" "$APPS/transcritor.desktop"; then
    rm -f "$APPS/transcritor.desktop"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi

rm -rf "$DIR/.venv"

echo "Transcritor removido: comandos, atalho do menu e ambiente .venv."
echo
echo "Ficou no seu computador:"
echo "  - suas transcrições, na pasta Transcricoes dentro de Documentos"
echo "  - os modelos, em ~/.cache/huggingface (até 5 GB). Se nenhum outro programa seu"
echo "    usa modelos do Hugging Face, pode apagar essa pasta para liberar espaço"
echo "  - esta pasta do programa, que você já pode apagar: $DIR"
