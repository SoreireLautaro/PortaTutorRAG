"""
Tutor virtual de reingeniería de procesos: interfaz web local (Gradio).

Flujo de cada consulta:
pregunta → búsqueda en el índice → fragmentos + prompt → modelo local → respuesta + citas
"""
from __future__ import annotations

import os

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")  # nada de llamadas a Internet

import json
import re
import time
from datetime import datetime
from pathlib import Path

import gradio as gr
import httpx

import rag

CFG = rag.cargar_config()
PROMPT_SISTEMA = rag.ruta("app/prompt_sistema.txt").read_text(encoding="utf-8")
CARPETA_INDICE = rag.ruta(CFG["indice"]["carpeta"])
INDICE_LIBRO = rag.Indice.cargar(CARPETA_INDICE, "libro")
CARPETA_REGISTROS = rag.ruta("registros")

# ---------------------------------------------------------------- constantes

SEPARADOR_CITAS = "\n\n—\n"  # lo que sigue es agregado por el programa, no por el modelo

# Mensaje inicial que ve el tutor como contexto de apertura (no se muestra en la UI)
SALUDO = (
    "Hola, soy tu tutor virtual de reingeniería de procesos. "
    "Te propongo, fundamento cada propuesta en el libro de la cátedra y vos decidís. "
    "Trabajamos en 7 fases: delimitación, AS-IS, diagnóstico, TO-BE, "
    "sistemas de información e IA, indicadores y plan de implementación."
)

FASES = [
    "Delimitación del proceso",
    "Relevamiento AS-IS",
    "Diagnóstico",
    "Rediseño TO-BE",
    "Sistemas de información e IA",
    "Indicadores",
    "Plan de implementación",
]

SUGERENCIAS = [
    ("Analizar un proceso", "Delimitarlo y entenderlo",
     "Quiero analizar un proceso de mi organización. ¿Por dónde arrancamos?"),
    ("Relevar el AS-IS", "Cómo se trabaja hoy",
     "Necesito relevar el AS-IS de un proceso. Ayudame con las preguntas."),
    ("Proponer el TO-BE", "Un rediseño fundamentado",
     "Ya tengo el AS-IS relevado. Proponé un rediseño TO-BE fundamentado."),
    ("Definir indicadores", "Medir antes y después",
     "Definí indicadores para comparar el proceso antes y después del rediseño."),
]

def saludo_segun_hora() -> str:
    h = datetime.now().hour
    if 6 <= h < 13:
        return "Buen día"
    if 13 <= h < 20:
        return "Buenas tardes"
    return "Buenas noches"


# ---------------------------------------------------------------- utilidades

def texto_de(mensaje: dict) -> str:
    """Gradio 6 puede devolver el contenido como lista de bloques."""
    c = mensaje.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c)


def limpiar(texto: str) -> str:
    return texto.split(SEPARADOR_CITAS)[0]


def tokens_aprox(texto: str) -> int:
    """Estimación conservadora para español (~3,5 caracteres por token)."""
    return int(len(texto) / 3.5) + 4


def consulta_de_busqueda(historial: list[dict]) -> str:
    """Pregunta actual; si es corta ("sí", "¿y eso?"), se le suma la anterior."""
    usuarios = [texto_de(m) for m in historial if m["role"] == "user"]
    actual = usuarios[-1].strip()
    if len(actual.split()) < 12 and len(usuarios) > 1:
        return usuarios[-2][-500:] + "\n" + actual
    return actual


def bloque_fragmentos(resultados: list[tuple[rag.Fragmento, float]]) -> str:
    return "\n\n".join(f"[L{i}] {f.etiqueta}\n{f.texto}"
                       for i, (f, _) in enumerate(resultados, start=1))


def citas_dudosas(respuesta: str, libro: list[tuple[rag.Fragmento, float]]) -> list[str]:
    """Citas de la respuesta que no corresponden a los fragmentos entregados."""
    dudosas = []
    for n in sorted({int(x) for x in re.findall(r"\[L(\d+)\]", respuesta)}):
        if not 1 <= n <= len(libro):
            dudosas.append(f"[L{n}]")
    capitulos = {m for f, _ in libro for m in re.findall(r"Cap\. (\d+)", f.etiqueta)}
    for n in sorted(set(re.findall(r"Cap(?:\.|ítulo) (\d+)", respuesta))):
        if n not in capitulos:
            dudosas.append(f"Cap. {n}")
    return dudosas


def servidores_ok() -> str:
    faltan = []
    for nombre, url in (("modelo", CFG["servidores"]["llm_url"]),
                        ("embeddings", CFG["servidores"]["embeddings_url"])):
        try:
            httpx.get(url.rstrip("/") + "/health", timeout=3).raise_for_status()
        except Exception:
            faltan.append(nombre)
    avisos = []
    if faltan:
        avisos.append(f"No responde el servidor de {' y '.join(faltan)}. "
                      "Iniciá el asistente con iniciar.sh / iniciar.bat.")
    if INDICE_LIBRO is None:
        avisos.append("No hay índice del libro. Ejecutá python app/indexar.py.")
    return "\n\n".join(avisos)


# ---------------------------------------------------------------- lógica de chat

def agregar_usuario(mensaje: str, historial: list[dict], estado: str | None = None):
    """(entrada vacía, chat actualizado y visible, hero oculto, panel fases→fuentes,
    chips ocultos, enviar→stop, archivo activo actualizado)."""
    if not mensaje.strip():
        return ("", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update())
    hist = list(historial) + [{"role": "user", "content": mensaje.strip()}]
    estado = _guardar(hist, estado)
    return ("", gr.update(value=hist, visible=True), gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=False), gr.update(visible=True), estado)


def restaurar_envio():
    """Vuelve a mostrar el botón de enviar y oculta el de detener."""
    return gr.update(visible=True), gr.update(visible=False)


def usar_sugerencia(texto: str, historial: list[dict], estado: str | None = None):
    return agregar_usuario(texto, historial, estado)


def _msg_thinking(texto: str, status: str = "pending", duracion: float | None = None) -> dict:
    """Mensaje 'Thinking': proceso real del asistente, colapsable. status=
    'pending' (expandido, en curso) | 'done' (colapsado al terminar)."""
    m = {"role": "assistant", "content": texto,
         "metadata": {"title": "Thinking", "status": status}}
    if duracion is not None:
        m["metadata"]["duration"] = round(duracion, 1)
    return m


def responder(historial: list[dict], estado: str | None = None):
    if not historial or historial[-1]["role"] != "user":
        yield historial, gr.update(), gr.update()
        return
    inicio = time.time()
    historial = list(historial)
    panel_inicial = "*Las fuentes aparecen acá después de tu primera consulta.*"
    pregunta = texto_de(historial[-1]).strip()
    historial.append(_msg_thinking("Buscando en el libro…"))
    yield historial, panel_inicial, gr.update()

    # --- Recuperación
    bcfg = CFG["busqueda"]
    libro: list[tuple[rag.Fragmento, float]] = []
    if INDICE_LIBRO is not None:
        try:
            consulta_busqueda = consulta_de_busqueda(historial)
            qv = rag.embeber([consulta_busqueda], CFG, CFG["embeddings"]["prefijo_consulta"])[0]
        except Exception as e:
            historial[-1] = _msg_thinking(f"No pude consultar el servidor de embeddings ({type(e).__name__}).",
                                          "done", time.time() - inicio)
            historial.append({"role": "assistant", "content":
                              f"No pude consultar el servidor de embeddings ({type(e).__name__})."})
            estado = _guardar(historial, estado)
            yield historial, gr.update(), estado
            return
        libro = INDICE_LIBRO.buscar(qv, bcfg["top_k"], bcfg["umbral"],
                                    consulta_busqueda, bcfg["peso_lexico"])
    n_fuentes = len(libro)
    pens = "Buscando en el libro…\n" + (
        f"{n_fuentes} fuente{'s' if n_fuentes != 1 else ''} sobre el umbral · generando respuesta…"
        if n_fuentes else "Ninguna fuente sobre el umbral · generando respuesta…")
    historial[-1] = _msg_thinking(pens)
    estado = _guardar(historial, estado)
    yield historial, panel_inicial, estado

    # --- Mensaje para el modelo
    partes = []
    if libro:
        partes.append("FRAGMENTOS DEL LIBRO:\n\n" + bloque_fragmentos(libro))
    else:
        partes.append("FRAGMENTOS DEL LIBRO: ninguno supera el umbral de relevancia. "
                      "Si el usuario pide un concepto o fundamento, indica que el libro no aporta "
                      "información suficiente. Si solo está describiendo su proceso, continúa "
                      "el relevamiento sin citar el libro.")
    partes.append(f"MENSAJE DEL USUARIO:\n{pregunta}")

    previos = [{"role": m["role"], "content": limpiar(texto_de(m))}
               for m in historial
               if m["role"] in ("user", "assistant") and not m.get("metadata")]
    previos = previos[:-1]  # la última del usuario va en 'actual', con los fragmentos
    previos = previos[-CFG["generacion"]["mensajes_historial"]:]
    actual = {"role": "user", "content": "\n\n".join(partes)}
    # Recortar el historial para que el turno entre en el contexto del modelo
    gcfg = CFG["generacion"]
    disponible = (gcfg["contexto_tokens"] - gcfg["max_tokens"] - 300
                  - tokens_aprox(PROMPT_SISTEMA) - tokens_aprox(actual["content"]))
    while previos and sum(tokens_aprox(m["content"]) for m in previos) > disponible:
        previos.pop(0)
    mensajes = [{"role": "system", "content": PROMPT_SISTEMA}] + previos + [actual]

    # --- Panel de fuentes (transparencia para quien usa el asistente)
    panel = []
    for i, (f, s) in enumerate(libro, start=1):
        panel.append(f"**[L{i}] {f.etiqueta}**")
        snippet = " ".join(f.texto[:160].split()).replace("*", "")
        panel.append(f"*{s:.2f} · {snippet}…*")
        panel.append("")
    panel_txt = "\n".join(panel).strip() or \
        "*Ninguna fuente supera el umbral. Reformulá la pregunta o describí el proceso.*"

    # --- Generación con streaming
    url = CFG["servidores"]["llm_url"].rstrip("/") + "/v1/chat/completions"
    cuerpo = {"messages": mensajes, "stream": True,
              "temperature": gcfg["temperatura"], "max_tokens": gcfg["max_tokens"]}
    primer_token = True

    def _cerrar_thinking():
        for k in range(len(historial) - 1, -1, -1):
            md = historial[k].get("metadata")
            if md and md.get("status") == "pending":
                historial[k] = _msg_thinking(historial[k]["content"], "done", time.time() - inicio)
                break

    try:
        with httpx.stream("POST", url, json=cuerpo, timeout=httpx.Timeout(600, connect=5)) as r:
            r.raise_for_status()
            for linea in r.iter_lines():
                if not linea.startswith("data: ") or linea == "data: [DONE]":
                    continue
                delta = json.loads(linea[6:])["choices"][0].get("delta", {}).get("content") or ""
                if delta:
                    if primer_token:
                        _cerrar_thinking()
                        historial.append({"role": "assistant", "content": ""})
                        primer_token = False
                    historial[-1]["content"] += delta
                    yield historial, panel_txt, estado
    except Exception as e:
        if primer_token:
            _cerrar_thinking()
            historial.append({"role": "assistant", "content": ""})
            primer_token = False
        historial[-1]["content"] += f"\n\nError al consultar el modelo local ({type(e).__name__})."

    if primer_token:  # el modelo no devolvió ni un token
        _cerrar_thinking()
        historial.append({"role": "assistant", "content": ""})
    yield historial, panel_txt, estado

    # --- Citas agregadas por el programa (no dependen del modelo)
    if libro:
        citas = "Fuentes del libro: " + "; ".join(
            f"[L{i}] {f.etiqueta}" for i, (f, _) in enumerate(libro, start=1))
    else:
        citas = "Ninguna fuente del libro supera el umbral para este mensaje."
    dudosas = citas_dudosas(historial[-1]["content"], libro)
    if dudosas:
        citas += ("\nAviso — la respuesta menciona " + ", ".join(dudosas) +
                  ", que no están entre las fuentes consultadas. Verificalo contra el libro.")
    historial[-1]["content"] += SEPARADOR_CITAS + citas
    estado = _guardar(historial, estado)
    yield historial, panel_txt, estado


def _titulo_actual(path: Path) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            primera = f.readline().strip()
    except OSError:
        return None
    if primera.startswith("# "):
        t = primera[2:].strip()
        if t and not t.startswith("Registro de conversación"):
            return t
    return None


def _escribir(path: Path, historial: list[dict]) -> None:
    titulo = _titulo_actual(path) or titulo_para(historial)
    CARPETA_REGISTROS.mkdir(exist_ok=True)
    lineas = [f"# {titulo}\n",
              f"_Guardada el {datetime.now():%d-%m-%Y a las %H:%M}_\n"]
    for m in historial:
        if m.get("metadata"):           # bloque Thinking: se guarda
            quien = "Thinking"
        else:
            quien = "Usuario" if m["role"] == "user" else "Asistente"
        contenido = texto_de(m).strip()
        if contenido:
            lineas.append(f"## {quien}\n\n{contenido}\n")
    path.write_text("\n".join(lineas), encoding="utf-8")


def _guardar(historial: list[dict], estado: str | None) -> str:
    """Persiste la conversación en el archivo activo (o crea uno). Devuelve la ruta."""
    if estado and Path(estado).exists():
        archivo = Path(estado)
    else:
        archivo = CARPETA_REGISTROS / f"conversacion_{datetime.now():%Y%m%d_%H%M%S}.md"
    _escribir(archivo, historial)
    return str(archivo)


def guardar(historial: list[dict], estado: str | None = None):
    """Guarda (o actualiza) la conversación con un título de máx. 5 palabras."""
    if not historial:
        return (gr.update(value="No hay conversación para guardar.", visible=True),
                *actualizar_lista(),
                gr.update())
    archivo = _guardar(historial, estado)
    titulo = _titulo_actual(Path(archivo))
    return (gr.update(value=f"Guardada como «{titulo}».", visible=True),
            *actualizar_lista(),
            archivo)


# ---------------------------------------------------------------- conversaciones

MAX_CONVS = 30


def listar_conversaciones(maximo: int = MAX_CONVS) -> list[tuple[Path, str]]:
    """Conversaciones guardadas (registros/), de la más reciente a la más vieja."""
    if not CARPETA_REGISTROS.exists():
        return []
    archivos = sorted(CARPETA_REGISTROS.glob("*.md"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    items: list[tuple[Path, str]] = []
    for a in archivos:
        try:
            texto = a.read_text(encoding="utf-8")
        except OSError:
            continue
        lineas = texto.splitlines()
        primera = lineas[0].strip() if lineas else ""
        titulo = primera.removeprefix("# ").strip() if primera.startswith("#") else ""
        if titulo.startswith("Registro de conversación"):  # formato antiguo: usar las primeras palabras del usuario
            extraido = _primer_mensaje_usuario(lineas)
            if extraido:
                titulo = " ".join(extraido.split()[:5])
        items.append((a, titulo or a.stem.replace("_", " ")))
    return items[:maximo]


def _primer_mensaje_usuario(lineas: list[str]) -> str:
    for i, linea in enumerate(lineas):
        if linea.startswith("## Usuario"):
            for siguiente in lineas[i + 1:]:
                if siguiente.strip():
                    return siguiente.strip()
    return ""


def cargar_conversacion(path: Path) -> list[dict]:
    """Parsea el .md guardado de vuelta a mensajes, incluidos los Thinking."""
    mensajes: list[dict] = []
    rol: str | None = None   # "user" | "assistant" | "thinking"
    buffer: list[str] = []

    def volcar():
        if not rol or not buffer:
            return
        contenido = "\n".join(buffer).strip()
        if not contenido:
            return
        if rol == "thinking":
            mensajes.append({"role": "assistant", "content": contenido,
                             "metadata": {"title": "Thinking", "status": "done"}})
        else:
            mensajes.append({"role": rol, "content": contenido})

    with open(path, encoding="utf-8") as f:
        for linea in f:
            if linea.startswith("## Usuario"):
                volcar(); rol, buffer = "user", []
            elif linea.startswith("## Thinking"):
                volcar(); rol, buffer = "thinking", []
            elif linea.startswith("## Asistente"):
                volcar(); rol, buffer = "assistant", []
            elif rol:
                buffer.append(linea.rstrip("\n"))
    volcar()
    return mensajes


def titulo_para(historial: list[dict]) -> str:
    """Título de máx. 5 palabras generado por el modelo local; si falla, uno heurístico."""
    usuarios = [texto_de(m) for m in historial if m["role"] == "user"]
    contexto = "\n".join(usuarios)[:1500]
    titulo = ""
    try:
        url = CFG["servidores"]["llm_url"].rstrip("/") + "/v1/chat/completions"
        r = httpx.post(url, json={
            "messages": [
                {"role": "system", "content":
                 "Sos un generador de títulos. Respondé SOLO con el título: máximo 5 palabras, "
                 "en español, sin comillas, sin punto final."},
                {"role": "user", "content":
                 f"Título de máximo 5 palabras para esta conversación:\n\n{contexto}"},
            ],
            "temperature": 0.2, "max_tokens": 24, "stream": False,
        }, timeout=60)
        r.raise_for_status()
        titulo = r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        titulo = ""
    titulo = titulo.strip().strip('"«»“”').split("\n")[0].rstrip(".").replace("#", "").strip()
    if not titulo and usuarios:
        titulo = " ".join(usuarios[0].split()[:5])
    palabras = titulo.split()
    return " ".join(palabras[:5]) if len(palabras) > 5 else (titulo or "Conversación")


def actualizar_lista():
    """Refresca los botones de la barra lateral con las conversaciones guardadas."""
    items = listar_conversaciones()
    btn_updates = [gr.update(value=titulo, visible=True) for _, titulo in items]
    btn_updates += [gr.update(visible=False) for _ in range(MAX_CONVS - len(items))]
    del_updates = [gr.update(visible=True) for _ in items]
    del_updates += [gr.update(visible=False) for _ in range(MAX_CONVS - len(items))]
    return (gr.update(visible=not items), *btn_updates, *del_updates)


PAPELERA = CARPETA_REGISTROS / ".papelera"


def borrar_conversacion(indice: int):
    """Mueve la conversación guardada N.ª (0-based) a registros/.papelera/ (recuperable)."""
    items = listar_conversaciones()
    aviso = "No se encontró la conversación."
    if 0 <= indice < len(items):
        try:
            PAPELERA.mkdir(exist_ok=True)
            destino = PAPELERA / items[indice][0].name
            n = 1
            while destino.exists():  # no pisar nada ya presente
                destino = PAPELERA / f"{items[indice][0].stem}_{n}{items[indice][0].suffix}"
                n += 1
            items[indice][0].rename(destino)
            aviso = "Conversación borrada (recuperable en registros/.papelera/)."
        except OSError:
            aviso = "No se pudo borrar el archivo."
    return (gr.update(value=aviso, visible=True), *actualizar_lista())


def abrir_conversacion(indice: int):
    """Carga una conversación guardada en el chat; la fija como archivo activo."""
    items = listar_conversaciones()
    if indice >= len(items):
        return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
                gr.update(), gr.update(), gr.update())
    archivo = items[indice][0]
    mensajes = cargar_conversacion(archivo)
    return (gr.update(value=mensajes, visible=True), gr.update(visible=False),
            "*Cargaste una conversación guardada. Las fuentes aparecen con la próxima respuesta.*",
            gr.update(visible=False), gr.update(visible=True), gr.update(visible=False),
            gr.update(visible=True), gr.update(visible=False), str(archivo))




ESTILOS = """
:root, .dark {
  --body-background-fill: #000000;
  --background-fill-primary: #000000;
  --background-fill-secondary: #000000;
  --block-background-fill: #000000;
  --block-border-width: 0px;
  --block-border-color: transparent;
  --block-shadow: none;
  --input-background-fill: #17171a;
  --input-background-fill-focus: #1d1d21;
  --input-background-fill-hover: #1b1b1f;
  --input-border-color: transparent;
  --input-border-width: 0px;
  --input-shadow: none;
  --body-text-color: #f2f2f3;
  --body-text-color-subdued: #9a9a9f;
  --block-label-text-color: #9a9a9f;
  --button-primary-background-fill: #ffffff;
  --button-primary-background-fill-hover: #e9e9ea;
  --button-primary-text-color: #000000;
  --button-primary-border-width: 0px;
  --button-secondary-background-fill: transparent;
  --button-secondary-background-fill-hover: #141417;
  --button-secondary-text-color: #e9e9ea;
  --button-secondary-text-color-hover: #ffffff;
  --button-secondary-border-width: 0px;
  --button-cancel-background-fill: transparent;
  --button-cancel-border-width: 0px;
  --button-cancel-text-color: #e9e9ea;
  --color-accent: #ffffff;
  --color-accent-soft: #1d1d21;
  --link-text-color: #e9e9ea;
  --link-text-color-hover: #ffffff;
  --shadow-drop: none;
}

html, body, .gradio-container { background: #000 !important; height: 100%; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important; }
body { overflow: hidden; height: 100vh !important; }
.gradio-container { max-width: none !important; margin: 0 !important; padding: 0 !important; height: 100vh !important; overflow: hidden !important; }
.gradio-container > .main, .gradio-container > .main > .wrap, .gradio-container .wrap, .gradio-container .contain { height: 100% !important; }
.gradio-container .contain .column { height: 100% !important; }
.gradio-container .shell { gap: 0 !important; flex-wrap: nowrap !important; height: 100% !important; }
.col-main, .col-sidebar, .col-fuentes { height: 100% !important; }
/* ---------- chat: fondo puro negro en toda la superficie ---------- */
#chat, #chat > *, #chat .container, #chat .table-wrap, #chat .viewport, #chat .block { background: #000 !important; border: none !important; }
#chat .icon-button, .scroll-down-button-container { display: none !important; }
[data-testid="status-tracker"] { display: none !important; }
/* Gradio pinta un "translucent"/"generating" encima al enviar: lo desactivamos */
#chat .wrap.translucent, #chat .wrap.generating { visibility: hidden !important; background: transparent !important; opacity: 0 !important; border: none !important; }

/* ---------- fuentes: sin scroll horizontal ni bordes ---------- */
.col-fuentes { overflow-x: hidden !important; }
.col-fuentes .block, .fuentes, .panel-fuentes .block { border: none !important; box-shadow: none !important; overflow: visible !important; height: auto !important; background: transparent !important; }
.panel-fuentes { border: none !important; box-shadow: none !important; }
.fuentes p, .fuentes strong, .fuentes em { overflow-wrap: anywhere !important; word-break: break-word !important; }

/* ---------- disclaimer y avisos: sin scroll vertical ---------- */
.disclaimer, .disclaimer .block, .aviso, .aviso .block, .foot, .foot .block {
  overflow: visible !important; height: auto !important; }

.col-main .prose span.md.prose, .col-sidebar .prose span.md.prose, .col-fuentes .prose span.md.prose {
  font-size: inherit !important; color: inherit !important; line-height: inherit !important; }
.hero .block, .col-main .aviso.block, .disclaimer.block { overflow: visible !important; height: auto !important; }
/* tipografía explícita: la de Gradio impone 14px con "var(--text-md)" */
.col-main .prose.hero-hi p, .col-main .prose.hero-title p, .col-main .prose.hero-sub p,
.col-main .prose.aviso p, .col-main .prose.disclaimer p,
.col-sidebar .prose.fases p, .col-sidebar .prose.fases li,
.col-sidebar .prose.foot p, .col-sidebar .prose.side-title p,
.col-fuentes .prose.fuentes p, .col-fuentes .prose.side-title p {
  font-size: inherit !important; color: inherit !important; margin: 0 !important; line-height: inherit !important; }

footer, .gradio-footer, .footer { display: none !important; }

::selection { background: #2e2e34; color: #fff; }
*::-webkit-scrollbar { width: 9px; height: 9px; }
*::-webkit-scrollbar-thumb { background: #26262b; border-radius: 8px; }
*::-webkit-scrollbar-track { background: transparent; }

/* ---------- barra lateral ---------- */
.col-sidebar {
  flex: 0 0 264px !important; min-width: 264px !important;
  background: #0b0b0d; padding: 26px 16px 18px !important;
  display: flex !important; flex-direction: column !important; gap: 2px !important;
}
.wordmark { color: #f5f5f6; font-size: 16.5px; font-weight: 650; letter-spacing: .01em; padding: 2px 12px; }
.tagline { color: #83838a; font-size: 13px; padding: 2px 12px; margin-bottom: 22px; }

.side {
  justify-content: flex-start !important; text-align: left !important;
  background: transparent !important; color: #d5d5d9 !important; border: none !important;
  border-radius: 12px !important; padding: 11px 12px !important;
  font-size: 14.5px !important; font-weight: 500 !important;
  transition: background .18s ease, color .18s ease, transform .18s ease !important;
  width: 100% !important; min-height: 0 !important;
}
.side:hover { background: #131316 !important; color: #ffffff !important; }

.side-title {
  color: #6f6f76 !important; font-size: 12px !important; font-weight: 600 !important;
  letter-spacing: .04em; padding: 4px 12px !important; margin: 26px 0 6px !important;
  text-transform: none !important;
}
.fases { color: #77777d !important; font-size: 13px !important; line-height: 2.15 !important; padding: 0 12px !important; }
.fases p { margin: 0 !important; }
.fases ol { padding-left: 17px !important; margin: 0 !important; }

.foot { color: #55555c !important; font-size: 12px !important; line-height: 1.6 !important; padding: 0 12px !important; margin-top: auto !important; }

.convs { gap: 1px !important; padding: 0 4px !important; max-height: 300px !important; overflow-y: auto !important; }
.conv-vacio { color: #55555c !important; font-size: 12.5px !important; padding: 2px 12px 6px !important; }
.conv { justify-content: flex-start !important; text-align: left !important; background: transparent !important;
  color: #c9c9ce !important; border: none !important; border-radius: 10px !important; padding: 9px 12px !important;
  font-size: 13.5px !important; font-weight: 400 !important; width: 100% !important; display: block !important;
  white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
  transition: background .16s ease, color .16s ease !important; min-height: 0 !important; }
.conv:hover { background: #131316 !important; color: #ffffff !important; }
.conv-row { gap: 2px !important; align-items: center !important; flex-wrap: nowrap !important; }
.conv-del { flex: 0 0 30px !important; width: 30px !important; min-width: 30px !important; color: #5c5c63 !important;
  background: transparent !important; border: none !important; border-radius: 10px !important; font-size: 16px !important;
  padding: 4px 0 !important; opacity: 0 !important; font-weight: 500 !important;
  transition: opacity .15s ease, background .15s ease, color .15s ease !important; }
.conv-row:hover .conv-del { opacity: 1 !important; }
.conv-del:hover { background: #1b1b1f !important; color: #ffffff !important; }

/* ---------- columna principal ---------- */
.col-main { display: flex !important; flex-direction: column !important; flex-wrap: nowrap !important; background: #000 !important; padding: 0 !important; position: relative; justify-content: center !important; }
.col-main:not(:has(#hero:not([style*="display: none"]))) { justify-content: flex-start !important; }
body.modo-chat .col-main { justify-content: flex-start !important; }

.aviso { color: #9a9a9f !important; font-size: 13px !important; text-align: center; padding: 12px 20px 0 !important; }

/* héroe / estado vacío */
.hero { flex: 0 0 auto !important; height: fit-content !important; display: flex !important;
  flex-direction: column !important; justify-content: center !important; align-items: center !important;
  padding: 0 32px !important; min-height: 0; }
.hero-hi { color: #e2e2e5 !important; font-size: clamp(19px, 2vw, 25px) !important; font-weight: 550 !important;
  margin: 0 0 14px !important; text-align: center !important; animation: fadeUp .55s cubic-bezier(.22,1,.36,1) .02s both; }
.hero-title { color: #ffffff !important; font-family: Georgia, 'Times New Roman', serif !important;
  font-size: clamp(40px, 5.2vw, 60px) !important; line-height: 1.08 !important; letter-spacing: -.015em !important;
  text-align: center !important; margin: 0 !important; animation: fadeUp .6s cubic-bezier(.22,1,.36,1) .1s both; }
.hero-title p { margin: 0 !important; }
.hero-sub { color: #9a9a9f !important; font-size: 15.5px !important; text-align: center !important;
  max-width: 48ch; line-height: 1.6 !important; margin: 18px 0 0 !important; animation: fadeUp .6s cubic-bezier(.22,1,.36,1) .18s both; }
.hero-sub p { margin: 0 !important; }

.chips { display: grid !important; grid-template-columns: repeat(2, minmax(190px, 236px)) !important;
  gap: 12px !important; justify-content: center !important; margin: 16px 0 12px !important;
  animation: fadeUp .6s cubic-bezier(.22,1,.36,1) .26s both; }
.chip { display: flex !important; flex-direction: column !important; align-items: flex-start !important;
  justify-content: flex-start !important; gap: 8px !important; text-align: left !important;
  min-height: 96px !important; background: #141417 !important; color: #f0f0f1 !important;
  border: none !important; border-radius: 18px !important; font-size: 15px !important; font-weight: 600 !important;
  line-height: 1.25 !important; padding: 18px !important; white-space: pre-line !important;
  transition: background .18s ease, transform .18s ease !important; }
.chip:hover { background: #1f1f24 !important; transform: translateY(-2px) !important; }
.chip:active { transform: scale(.97) !important; }

/* chat */
#chat { flex: 1 1 auto !important; min-height: 0 !important; width: 100% !important; max-width: 820px !important;
  margin: 0 auto !important; padding: 26px 36px 6px !important; background: transparent !important; }
#chat .message { animation: msgIn .32s cubic-bezier(.22,1,.36,1) both; font-size: 15.5px !important; line-height: 1.68 !important; }
#chat .message.user { background: #1b1b1f !important; color: #f5f5f6 !important; border-radius: 20px !important;
  padding: 12px 17px !important; margin-left: auto !important; border: none !important;
  box-shadow: none !important; }
#chat .message.bot, #chat .message.assistant { background: transparent !important; color: #ececed !important;
  max-width: 100% !important; border: none !important; box-shadow: none !important; padding: 6px 2px !important; }
#chat .message a { color: #fff !important; text-decoration: underline; text-underline-offset: 3px; text-decoration-color: #3a3a40; }
#chat .prose, #chat .prose p { font-size: 15.5px !important; line-height: 1.68 !important; }

/* entrada */
.input-row { width: 100% !important; max-width: 820px !important; margin: 0 auto !important;
  padding: 4px 28px 0 !important; gap: 12px !important; align-items: center !important; flex-wrap: nowrap !important; }
#entrada { flex: 1 1 auto !important; }
#entrada textarea { background: #17171a !important; color: #f5f5f6 !important; border: none !important; outline: none !important;
  border-radius: 22px !important; padding: 17px 22px !important; font-size: 16px !important; line-height: 1.5 !important;
  box-shadow: none !important; transition: background .18s ease !important; caret-color: #ffffff; }
#entrada textarea::placeholder { color: #77777d !important; }
#entrada textarea:focus { background: #1d1d21 !important; }

#btn-enviar { flex: none !important; width: 52px !important; height: 52px !important; min-width: 52px !important;
  border-radius: 999px !important; background: #ffffff !important; color: #000 !important; border: none !important;
  font-size: 21px !important; font-weight: 600 !important; padding: 0 !important;
  transition: transform .18s cubic-bezier(.22,1,.36,1), background .18s ease !important; }
#btn-enviar:hover { transform: scale(1.08) !important; background: #ffffff !important; }
#btn-enviar:active { transform: scale(.92) !important; }

#btn-stop { flex: none !important; width: 52px !important; height: 52px !important; min-width: 52px !important;
  border-radius: 999px !important; background: #ffffff !important; color: #000 !important; border: none !important;
  font-size: 15px !important; font-weight: 700 !important; line-height: 1 !important; padding: 0 !important;
  transition: transform .18s cubic-bezier(.22,1,.36,1), background .18s ease !important; }
#btn-stop:hover { transform: scale(1.08) !important; background: #ffffff !important; }
#btn-stop:active { transform: scale(.92) !important; }
#btn-stop span { font-size: 15px !important; line-height: 1 !important; }

.disclaimer { color: #5f5f66 !important; font-size: 12.5px !important; text-align: center !important;
  margin: 2px 0 2px !important; line-height: 1.3 !important; }
.disclaimer p { margin: 0 !important; }

/* ---------- fuentes ---------- */
.col-fuentes { flex: 0 0 312px !important; min-width: 312px !important; padding: 22px 26px 22px 8px !important;
  display: flex !important; flex-direction: column !important; gap: 2px !important; overflow-y: auto; }
.fuentes { font-size: 13.5px !important; line-height: 1.55 !important; color: #d9d9dd !important; padding: 0 12px !important; }
.fuentes p { margin: 0 0 3px !important; }
.fuentes strong { color: #ffffff !important; font-weight: 600 !important; font-size: 13px !important; }
.fuentes em { color: #83838a !important; font-style: normal !important; }

/* ---------- animaciones ---------- */
@keyframes fadeUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }
@keyframes msgIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }

*:focus-visible { outline: none !important; box-shadow: 0 0 0 2px #2c2c33 !important; }

/* ---------- responsive ---------- */
@media (max-width: 1150px) { .col-fuentes { display: none !important; } }
@media (max-width: 920px) { .col-sidebar { display: none !important; } }
@media (max-width: 640px) {
  .hero-title { font-size: 32px !important; }
  .input-row { padding: 4px 14px 0 !important; }
  #chat { padding: 16px 14px 4px !important; }
}
"""

# Shift+Enter: salto de línea nativo. Se frena el evento (fase de captura) antes de
# que el manejador de Gradio lo tome como envío; Enter solo sigue enviando.
ATAJOS_TECLADO = """<script>
window.addEventListener("keypress", (e) => {
  if (e.key === "Enter" && e.shiftKey && e.target.closest?.("#entrada")) e.stopImmediatePropagation();
}, true);
window.addEventListener("load", () => setTimeout(() => {
  document.querySelector("#entrada textarea")?.focus({ preventScroll: true });
}, 400));
// modo-chat: el input se ancla abajo cuando el héroe desaparece (primer mensaje enviado)
const syncModo = () => {
  const hero = document.querySelector("#hero");
  const visible = hero && getComputedStyle(hero).display !== "none";
  document.body.classList.toggle("modo-chat", !visible);
};
window.addEventListener("load", () => {
  syncModo();
  new MutationObserver(syncModo).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["style", "class"] });
});
</script>"""

_aviso = servidores_ok()

with gr.Blocks(title="PortaTutor", fill_height=True, elem_id="app") as demo:
    estado = gr.State(None)
    with gr.Row(elem_classes=["shell"]):
        # ---------- barra lateral
        with gr.Column(scale=0, min_width=264, elem_classes=["col-sidebar"]):
            gr.HTML('<div class="wordmark">PortaTutor</div>'
                    '<div class="tagline">Reingeniería de procesos</div>')
            btn_nueva = gr.Button("➕ Nueva conversación", elem_classes=["side"], elem_id="btn-nueva")
            btn_guardar = gr.Button("💾 Guardar conversación", elem_classes=["side"], elem_id="btn-guardar")
            gr.Markdown("Conversaciones", elem_classes=["side-title"])
            conv_vacio = gr.Markdown("Las conversaciones guardadas aparecen acá.",
                                     elem_classes=["conv-vacio"])
            with gr.Column(elem_classes=["convs"]):
                conv_btns, conv_del = [], []
                for i in range(MAX_CONVS):
                    with gr.Row(elem_classes=["conv-row"]):
                        conv_btns.append(gr.Button("", elem_classes=["conv"], visible=False,
                                                   elem_id=f"conv-{i}"))
                        conv_del.append(gr.Button("×", elem_classes=["conv-del"], visible=False,
                                                  elem_id=f"conv-del-{i}", min_width=34))
            gr.Markdown("Offline · tus datos no salen del pendrive", elem_classes=["foot"])

        # ---------- columna principal
        with gr.Column(scale=1, elem_classes=["col-main"]):
            aviso = gr.Markdown(_aviso, elem_classes=["aviso"], visible=bool(_aviso))
            with gr.Column(elem_classes=["hero"], elem_id="hero", visible=True) as hero:
                gr.Markdown(saludo_segun_hora(), elem_classes=["hero-hi"])
                gr.Markdown("¿Por dónde empezamos?", elem_classes=["hero-title"])
                gr.Markdown("Tu tutor de reingeniería de procesos, "
                            "fundamentado en el libro de la cátedra.",
                            elem_classes=["hero-sub"])
            chat = gr.Chatbot(value=[], elem_id="chat", elem_classes=["chat"],
                              show_label=False, visible=False, height="100%",
                              buttons=[], layout="bubble")
            with gr.Row(elem_classes=["input-row"]):
                entrada = gr.Textbox(placeholder="Escribí tu mensaje…", show_label=False,
                                     lines=1, max_lines=6, elem_id="entrada",
                                     container=False, scale=1)
                btn_enviar = gr.Button("↑", elem_classes=["send"], elem_id="btn-enviar",
                                       min_width=52)
                btn_stop = gr.Button("■", elem_classes=["send stop"], elem_id="btn-stop",
                                     min_width=52, visible=False)
            gr.Markdown("PortaTutor puede equivocarse. Verificá las citas contra el libro.",
                        elem_classes=["disclaimer"])
            with gr.Row(elem_classes=["chips"], visible=True) as chips_col:
                chips = [gr.Button(f"{titulo}\n{sub}", elem_classes=["chip"])
                         for titulo, sub, _ in SUGERENCIAS]

        # ---------- panel derecho: fases (héroe) o fuentes (chat)
        with gr.Column(scale=0, min_width=312, elem_classes=["col-fuentes"]):
            with gr.Column(elem_classes=["panel-fases"], visible=True) as panel_fases:
                gr.Markdown("Las 7 fases", elem_classes=["side-title"])
                gr.Markdown("\n".join(f"{i}. {f}" for i, f in enumerate(FASES, 1)),
                            elem_classes=["fases"])
            with gr.Column(elem_classes=["panel-fuentes"], visible=False) as panel_fuentes:
                gr.Markdown("Fuentes", elem_classes=["side-title"])
                panel = gr.Markdown("*Las fuentes aparecen acá después de tu primera consulta.*",
                                    elem_classes=["fuentes"])

    flujos = []
    flujos.append(entrada.submit(agregar_usuario, [entrada, chat, estado],
                                 [entrada, chat, hero, panel_fases, panel_fuentes, chips_col,
                                  btn_enviar, btn_stop, estado])
                  .then(responder, [chat, estado], [chat, panel, estado])
                  .then(restaurar_envio, None, [btn_enviar, btn_stop])
                  .then(actualizar_lista, None, [conv_vacio, *conv_btns, *conv_del]))
    flujos.append(btn_enviar.click(agregar_usuario, [entrada, chat, estado],
                                   [entrada, chat, hero, panel_fases, panel_fuentes,
                                    chips_col, btn_enviar, btn_stop, estado])
                  .then(responder, [chat, estado], [chat, panel, estado])
                  .then(restaurar_envio, None, [btn_enviar, btn_stop])
                  .then(actualizar_lista, None, [conv_vacio, *conv_btns, *conv_del]))
    for chip, (_, _, mensaje) in zip(chips, SUGERENCIAS):
        flujos.append(chip.click(lambda h, e, m=mensaje: usar_sugerencia(m, h, e), [chat, estado],
                                 [entrada, chat, hero, panel_fases, panel_fuentes, chips_col,
                                  btn_enviar, btn_stop, estado])
                      .then(responder, [chat, estado], [chat, panel, estado])
                      .then(restaurar_envio, None, [btn_enviar, btn_stop])
                      .then(actualizar_lista, None, [conv_vacio, *conv_btns, *conv_del]))
    btn_stop.click(restaurar_envio, None, [btn_enviar, btn_stop],
                   cancels=flujos)
    btn_guardar.click(guardar, [chat, estado], [aviso, conv_vacio, *conv_btns, *conv_del, estado])
    btn_nueva.click(lambda: ("", gr.update(value=[], visible=False), gr.update(visible=True),
                             gr.update(visible=True), gr.update(visible=False),
                             "*Las fuentes aparecen acá después de tu primera consulta.*",
                             gr.update(visible=True), gr.update(visible=True), gr.update(visible=False),
                             None),
                    None, [entrada, chat, hero, panel_fases, panel_fuentes, panel,
                           chips_col, btn_enviar, btn_stop, estado], queue=False)
    for i, conv in enumerate(conv_btns):
        conv.click(lambda i=i: abrir_conversacion(i), None,
                   [chat, hero, panel, panel_fases, panel_fuentes, chips_col,
                    btn_enviar, btn_stop, estado], queue=False)
    for i, borra in enumerate(conv_del):
        borra.click(lambda i=i: borrar_conversacion(i), None,
                    [aviso, conv_vacio, *conv_btns, *conv_del], queue=False)
    demo.load(actualizar_lista, None, [conv_vacio, *conv_btns, *conv_del])


if __name__ == "__main__":
    icfg = CFG["interfaz"]
    tema = gr.themes.Base(primary_hue="neutral", secondary_hue="neutral",
                          neutral_hue="neutral")
    demo.queue().launch(server_name=icfg["host"], server_port=icfg["puerto"],
                        inbrowser=icfg["abrir_navegador"], share=False,
                        head=ATAJOS_TECLADO, css=ESTILOS, theme=tema, footer_links=[])
