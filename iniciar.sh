#!/usr/bin/env bash
# Inicia el asistente en Linux. Uso: bash iniciar.sh
# Funciona sin Internet. Todas las rutas son relativas a esta carpeta.
cd "$(dirname "$0")"
RAIZ="$PWD"
BIN="$RAIZ/bin/linux"
PY="$RAIZ/python/linux/bin/python3"
HILOS="$(nproc 2>/dev/null || echo 4)"

[ -x "$PY" ] || PY="$(command -v python3)"   # respaldo: Python del sistema
export LD_LIBRARY_PATH="$BIN:${LD_LIBRARY_PATH:-}"
export OPENBLAS_NUM_THREADS=4
chmod +x "$BIN"/* "$RAIZ/python/linux/bin/"* 2>/dev/null   # por si el pendrive perdió permisos

for f in "$BIN/llama-server" "$RAIZ/modelos/llm.gguf" "$RAIZ/modelos/embeddings.gguf"; do
  [ -e "$f" ] || { echo "Falta $f. Revisá MANUAL.md."; exit 1; }
done

PIDS=()
terminar() { echo; echo "Cerrando…"; kill "${PIDS[@]}" 2>/dev/null; exit 0; }
trap terminar INT TERM EXIT

# Puertos dinámicos: se prefieren 8080/8081/7860; si están ocupados, se elige uno libre
PUERTOS="$("$PY" "$RAIZ/app/puerto_libre.py" 8080 8081 7860)"
read -r PUE_LLM PUE_EMB PUE_UI <<< "$PUERTOS"
export PORTATUTOR_LLM_URL="http://127.0.0.1:$PUE_LLM"
export PORTATUTOR_EMBEDDINGS_URL="http://127.0.0.1:$PUE_EMB"
export PORTATUTOR_PUERTO_UI="$PUE_UI"
[ "$PUE_LLM" = 8080 ] || echo "Aviso: 8080 ocupado; el modelo usa el puerto $PUE_LLM."
[ "$PUE_EMB" = 8081 ] || echo "Aviso: 8081 ocupado; embeddings usa el puerto $PUE_EMB."
[ "$PUE_UI" = 7860 ] || echo "Aviso: 7860 ocupado; la interfaz usa el puerto $PUE_UI."

echo "Iniciando el modelo de lenguaje (puede tardar un minuto)…"
# -c debe coincidir con generacion.contexto_tokens de app/config.yaml
"$BIN/llama-server" -m "$RAIZ/modelos/llm.gguf" --host 127.0.0.1 --port "$PUE_LLM" \
  -c 8192 -t "$HILOS" -np 1 --jinja > "$RAIZ/llm.log" 2>&1 &
PIDS+=($!)
"$BIN/llama-server" -m "$RAIZ/modelos/embeddings.gguf" --host 127.0.0.1 --port "$PUE_EMB" \
  --embedding --pooling cls -c 8192 -b 8192 -ub 8192 -t "$HILOS" > "$RAIZ/embeddings.log" 2>&1 &
PIDS+=($!)

esperar() {  # esperar PUERTO NOMBRE
  for _ in $(seq 1 180); do
    "$PY" -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:$1/health', timeout=2)" 2>/dev/null && return 0
    sleep 1
  done
  echo "El servidor de $2 no arrancó. Ver $2.log"; exit 1
}
esperar "$PUE_EMB" embeddings
esperar "$PUE_LLM" modelo

if [ ! -f "$RAIZ/datos/indice/libro.npy" ]; then
  echo "Primera ejecución: generando el índice del libro…"
  "$PY" "$RAIZ/app/indexar.py" || exit 1
fi

echo "Abriendo http://127.0.0.1:$PUE_UI (Ctrl+C para salir)"
"$PY" "$RAIZ/app/app.py" > "$RAIZ/app.log" 2>&1
