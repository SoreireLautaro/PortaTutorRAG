#!/usr/bin/env bash
# Prepara el pendrive: descarga llama.cpp, Python portable, librerías y modelos.
# Se ejecuta UNA VEZ, en una PC Linux con Internet y `uv` instalado.
# Después, todo funciona sin conexión desde iniciar.sh / iniciar.bat.
#
#   bash preparar.sh            # Linux + Windows
#   bash preparar.sh linux      # solo Linux
#   bash preparar.sh windows    # solo Windows
set -euo pipefail
cd "$(dirname "$0")"
RAIZ="$PWD"
OBJETIVO="${1:-ambos}"

LLAMA_TAG="b12192"
PY_TAG="20261026"
PY_VER="3.12.16"
LLM_URL="https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
EMB_URL="https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
command -v uv >/dev/null || { echo "Falta uv: https://docs.astral.sh/uv/"; exit 1; }

bajar() {  # bajar URL DESTINO (reanuda descargas cortadas)
  echo "↓ $(basename "$2")"
  curl -fL --retry 3 -C - -o "$2" "$1"
}

# Copia dereferenciando enlaces simbólicos: exFAT no los admite.
copiar() { mkdir -p "$2"; cp -rL "$1"/. "$2"/; }

preparar_linux() {
  echo "== Linux"
  bajar "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_TAG/llama-$LLAMA_TAG-bin-ubuntu-x64.tar.gz" "$TMP/llama.tgz"
  mkdir -p "$TMP/llama" && tar -xzf "$TMP/llama.tgz" -C "$TMP/llama"
  local dir; dir="$(dirname "$(find "$TMP/llama" -name llama-server -type f | head -1)")"
  rm -rf bin/linux && copiar "$dir" bin/linux

  bajar "https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/cpython-$PY_VER%2B$PY_TAG-x86_64-unknown-linux-gnu-install_only.tar.gz" "$TMP/py.tgz"
  mkdir -p "$TMP/pyl" && tar -xzf "$TMP/py.tgz" -C "$TMP/pyl"
  rm -rf python/linux && copiar "$TMP/pyl/python" python/linux
  uv pip install --python python/linux/bin/python3 --break-system-packages -r app/requirements.txt
}

preparar_windows() {
  echo "== Windows"
  bajar "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_TAG/llama-$LLAMA_TAG-bin-win-cpu-x64.zip" "$TMP/llama.zip"
  rm -rf bin/windows && mkdir -p bin/windows && unzip -qo "$TMP/llama.zip" -d "$TMP/llamaw"
  local dir; dir="$(dirname "$(find "$TMP/llamaw" -name llama-server.exe | head -1)")"
  copiar "$dir" bin/windows

  bajar "https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/cpython-$PY_VER%2B$PY_TAG-x86_64-pc-windows-msvc-install_only.tar.gz" "$TMP/pyw.tgz"
  mkdir -p "$TMP/pyw" && tar -xzf "$TMP/pyw.tgz" -C "$TMP/pyw"
  rm -rf python/windows && copiar "$TMP/pyw/python" python/windows
  # Instala las ruedas de Windows desde Linux, directo en el site-packages portable
  uv pip install --python-platform x86_64-pc-windows-msvc --python-version 3.12 \
    --target python/windows/Lib/site-packages -r app/requirements.txt
}

case "$OBJETIVO" in
  linux) preparar_linux ;;
  windows) preparar_windows ;;
  *) preparar_linux; preparar_windows ;;
esac

echo "== Modelos"
mkdir -p modelos
[ -s modelos/llm.gguf ] || bajar "$LLM_URL" modelos/llm.gguf
[ -s modelos/embeddings.gguf ] || bajar "$EMB_URL" modelos/embeddings.gguf

echo "== Licencias"
cp -f bin/*/LICENSE* LICENCIAS/ 2>/dev/null || true
bajar "https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/raw/main/LICENSE" LICENCIAS/Qwen3-LICENSE.txt || true

echo
echo "Listo. Próximo paso: bash iniciar.sh  (la primera vez genera el índice del libro)."
