# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

Asistente de IA local, portable en un pendrive, que orienta procesos de reingeniería usando como fuente el libro de la cátedra (Etapa C del TP integrador de Reingeniería de Procesos, UNLaR). Es RAG sobre el libro con un LLM local: **todo debe funcionar sin Internet** y con rutas relativas, porque la raíz del repo es la raíz del pendrive. `etapa_c.md` es la guía de diseño (es una nota de Obsidian: usa wikilinks) y `MANUAL.md` es el manual para el usuario final. Ambos deben mantenerse coherentes con el código.

## Comandos

No hay tests ni linter. En desarrollo, sin el Python portable, usar `uv`:

```bash
# Ver la fragmentación y la asignación de páginas (no necesita servidores)
uv run --no-project --python 3.12 --with-requirements app/requirements.txt python app/indexar.py --solo-fragmentar

# Armar el pendrive: descarga llama.cpp, Python portable + librerías, modelos (~3,5 GB, requiere Internet)
bash preparar.sh [linux|windows]

# Ejecutar todo: levanta los dos llama-server, indexa si falta el índice y abre la UI en 127.0.0.1:7860
# (si 8080/8081/7860 están ocupados, iniciar.sh/iniciar.bat eligen puertos libres y exportan
#  PORTATUTOR_LLM_URL / PORTATUTOR_EMBEDDINGS_URL / PORTATUTOR_PUERTO_UI, que rag.cargar_config() aplica)
bash iniciar.sh            # Windows: iniciar.bat

# Con los servidores en marcha:
python app/indexar.py                      # regenerar el índice del libro
python app/indexar.py --probar "pregunta"  # similitudes de los mejores fragmentos, para calibrar busqueda.umbral
```

`bin/`, `python/`, `modelos/`, el índice generado (`datos/indice/*.npy|*.json`) y `registros/` están en `.gitignore`.

## Arquitectura

Tres procesos locales:

- `llama-server` en **:8080** (por defecto; el inicio elige un puerto libre si está ocupado): modelo de chat (`modelos/llm.gguf`, Qwen3-4B-Instruct-2507 Q4_K_M), API OpenAI `/v1/chat/completions` con streaming.
- `llama-server` en **:8081** (por defecto; ídem): embeddings (`modelos/embeddings.gguf`, bge-m3) con `--embedding --pooling cls`, API `/v1/embeddings`. Los embeddings no se calculan en Python, así se evita incluir PyTorch.
- `app/app.py`: interfaz Gradio que orquesta todo.

Los flags de ambos servidores están duplicados en `iniciar.sh` e `iniciar.bat`. Al cambiar el modelo de embeddings hay que cambiar el `--pooling` en los dos scripts, los prefijos de `config.yaml` (e5 usa `query: ` / `passage: `) y regenerar el índice.

**`app/rag.py`** (núcleo compartido): `RAIZ` = carpeta padre de `app/`, y toda ruta del `config.yaml` se resuelve contra ella con `ruta()`.
- `fragmentar_markdown`: divide por encabezados `#`–`###`, en ventanas de ~400 palabras con 60 de superposición. Las secciones de menos de `min_palabras` se unen a la siguiente. La etiqueta queda como `Cap. N > Sección > Subsección`; el H1 del título del libro se omite y "Capítulo N…"/"Anexo…" se abrevian.
- `asignar_paginas`: el `.md` es una versión **editada** del PDF, con otro orden y otro texto en partes. Por eso busca frases de 6 palabras de cada fragmento en el texto de cada página y vota, prefiriendo páginas cercanas a la anterior. Si no encuentra coincidencia, usa los fragmentos vecinos y cita un rango `pp. X–Y`. Cita el **número impreso** de la página (en este PDF, la página física menos 10); las páginas sin número impreso salen como `p. N del PDF`.
- `Indice`: guarda vectores normalizados en `.npy` y fragmentos en `.json`, y busca por producto punto con `top_k` y umbral. Hay un solo índice, `libro`: el libro es la única fuente de conocimiento (se quitaron las fuentes adicionales, `/autorizo-fuentes` y `/web`).

**Flujo de un turno en `app.py` (`responder`):**
1. La consulta de búsqueda es el último mensaje del usuario; si tiene menos de 12 palabras, se le suma el mensaje anterior.
2. Se recuperan fragmentos del libro, rotulados `[L1]…` **dentro del último mensaje de usuario** que se envía al modelo; el historial que se muestra guarda solo el texto real.
3. Si ningún fragmento supera el umbral, **igual se llama al modelo**, con la instrucción de no citar el libro. Es una decisión deliberada para no trabar el relevamiento, donde el usuario describe su proceso.
4. Antes de enviar, se descartan los mensajes más viejos del historial hasta que el turno entre en `generacion.contexto_tokens` (estimación de ~3,5 caracteres por token, función `tokens_aprox`). Ese valor debe coincidir con `-c` en los dos scripts de inicio.
5. Las citas del pie las agrega **el programa**, no el modelo, después de `SEPARADOR_CITAS`. `limpiar()` corta ese pie antes de reenviar el historial al modelo.

El comportamiento del asistente (las 7 fases, los bloques 📖/💡, la validación al cerrar cada fase) está en `app/prompt_sistema.txt`. El borrador de la §6 del etapa_c es solo una referencia.

## Restricciones a respetar

- **Offline:** `GRADIO_ANALYTICS_ENABLED=False` se fija antes de importar Gradio. No agregar dependencias que llamen a Internet ni CDNs.
- **Gradio 6:** el Chatbot usa mensajes `{"role", "content"}` y `content` puede llegar como lista de bloques; usar siempre `texto_de()` para leerlo.
- **Pendrive exFAT:** no admite enlaces simbólicos, por eso `preparar.sh` copia con `cp -rL`. Los permisos de ejecución pueden perderse, por eso `iniciar.sh` hace `chmod`. Las instrucciones dicen ejecutar con `bash iniciar.sh`.
- **`iniciar.bat` debe tener CRLF**, lo que asegura `.gitattributes`. Si se edita desde Linux, verificar con `file iniciar.bat`.
- **Versiones fijadas:** llama.cpp, Python standalone y las URLs de los modelos están en `preparar.sh`; las librerías, en `app/requirements.txt`. El Python de Windows se instala desde Linux con `uv pip install --python-platform x86_64-pc-windows-msvc --target …`.
- **Libro:** la ruta del libro se configura en `config.yaml` (`libro.markdown` y `libro.pdf`). `datos/indice/` contiene también una nota de tabla de contenidos (`Índice - ….md`), que no se indexa ni debe borrarse.
- El repo está dentro de un vault de Obsidian: valen las reglas del `CLAUDE.md`/`AGENTS.md` del vault (responder en español, no borrar notas).
