"""Núcleo del asistente: configuración, fragmentación, embeddings y búsqueda.

Todas las rutas se resuelven respecto de la raíz del proyecto (la raíz del
pendrive), así el programa funciona sin importar la letra de unidad.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx
import numpy as np
import yaml

RAIZ = Path(__file__).resolve().parent.parent


def ruta(rel: str) -> Path:
    return RAIZ / rel


def cargar_config() -> dict:
    with open(ruta("app/config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Los scripts de inicio pueden forzar puertos/URLs si los habituales están
    # ocupados (PORTATUTOR_PUERTO_UI es el puerto de la interfaz Gradio).
    serv = cfg.setdefault("servidores", {})
    if os.environ.get("PORTATUTOR_LLM_URL"):
        serv["llm_url"] = os.environ["PORTATUTOR_LLM_URL"]
    if os.environ.get("PORTATUTOR_EMBEDDINGS_URL"):
        serv["embeddings_url"] = os.environ["PORTATUTOR_EMBEDDINGS_URL"]
    if os.environ.get("PORTATUTOR_PUERTO_UI"):
        cfg.setdefault("interfaz", {})["puerto"] = int(os.environ["PORTATUTOR_PUERTO_UI"])
    return cfg


# ---------------------------------------------------------------- fragmentos

@dataclass
class Fragmento:
    etiqueta: str          # "Cap. 2 > Casos emblemáticos > IBM Credit Corporation (p. 41)"
    texto: str
    fuente: str = "libro"
    pagina: int | None = None


def texto_para_embeber(f: "Fragmento") -> str:
    """Etiqueta (sin la página) + texto: así el título de la sección también
    cuenta en la búsqueda (p. ej. "IBM Credit Corporation")."""
    etiqueta = re.sub(r"\s*\(pp?\. [^)]*\)$", "", f.etiqueta)
    return f"{etiqueta}\n{f.texto}"


def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _abreviar_capitulo(titulo: str) -> str:
    m = re.match(r"cap[íi]tulo\s+(\d+)\.?\s*(.*)", titulo, re.I)
    if m:
        return f"Cap. {m.group(1)}"
    return "Anexo" if re.match(r"anexo\b", titulo, re.I) else titulo


def _quitar_frontmatter(texto: str) -> str:
    if texto.startswith("---"):
        fin = texto.find("\n---", 3)
        if fin != -1:
            return texto[fin + 4:]
    return texto


def _partir_en_ventanas(texto: str, n: int, solape: int) -> list[str]:
    """Divide un texto largo en ventanas de ~n palabras respetando párrafos."""
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    ventanas, actual = [], []
    for p in parrafos:
        palabras = p.split()
        if actual and len(actual) + len(palabras) > n:
            ventanas.append(" ".join(actual))
            actual = actual[-solape:] if solape else []
        actual.extend(palabras)
        while len(actual) > n * 1.5:  # párrafo gigante: cortar por palabras
            ventanas.append(" ".join(actual[:n]))
            actual = actual[n - solape:]
    if actual:
        ventanas.append(" ".join(actual))
    return ventanas


def fragmentar_markdown(texto: str, cfg: dict) -> list[Fragmento]:
    """Fragmenta por encabezados (#, ##, ###) y subdivide secciones largas."""
    icfg = cfg["indice"]
    excluir = {_normalizar(s) for s in cfg["libro"].get("excluir_secciones", [])}
    texto = _quitar_frontmatter(texto)

    secciones: list[tuple[list[str], list[str]]] = []  # (ruta de títulos, líneas)
    ruta_titulos: list[str] = []
    lineas: list[str] = []
    for linea in texto.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", linea)
        if m:
            secciones.append((ruta_titulos.copy(), lineas))
            nivel, titulo = len(m.group(1)), m.group(2).strip()
            ruta_titulos = ruta_titulos[: nivel - 1] + [titulo]
            lineas = []
        else:
            lineas.append(linea)
    secciones.append((ruta_titulos.copy(), lineas))

    fragmentos: list[Fragmento] = []
    pendiente = ""  # texto de secciones muy cortas que se une a la siguiente
    for titulos, lns in secciones:
        cuerpo = "\n".join(lns).strip()
        if not titulos or any(_normalizar(t) in excluir for t in titulos):
            continue
        # El título del libro (# inicial) no aporta a la etiqueta
        es_titulo_libro = _normalizar(titulos[0]) == _normalizar(cfg["libro"]["titulo"])
        partes = titulos[1:] if len(titulos) > 1 and es_titulo_libro else titulos
        etiqueta = " > ".join([_abreviar_capitulo(partes[0])] + partes[1:])
        cuerpo = (pendiente + "\n\n" + cuerpo).strip() if pendiente else cuerpo
        if len(cuerpo.split()) < icfg["min_palabras"]:
            pendiente = cuerpo
            continue
        pendiente = ""
        for ventana in _partir_en_ventanas(cuerpo, icfg["palabras_por_fragmento"], icfg["superposicion"]):
            fragmentos.append(Fragmento(etiqueta=etiqueta, texto=ventana))
    return fragmentos


def paginas_pdf(ruta_pdf: Path) -> list[str]:
    import pymupdf
    with pymupdf.open(ruta_pdf) as doc:
        return [p.get_text() for p in doc]


def asignar_paginas(fragmentos: list[Fragmento], paginas: list[str]) -> int:
    """Asigna a cada fragmento la página del PDF donde aparece su texto.

    Busca varias frases de 6 palabras del fragmento en el texto normalizado de
    cada página (uniendo los cortes de palabra con guion) y elige la página
    con más coincidencias, prefiriendo las cercanas a la del fragmento anterior
    (el libro se recorre en orden). Si ninguna aparece
    (texto editado respecto del PDF), usa las páginas de los fragmentos
    vecinos: "p. N" si coinciden, "pp. N–M" si no. Devuelve cuántos
    fragmentos se encontraron de forma exacta.
    """
    norm = [_normalizar(re.sub(r"-\s*\n\s*", "", p)) for p in paginas]
    halladas: list[int | None] = []
    desde = 0
    for f in fragmentos:
        palabras = _normalizar(f.texto).split()
        anclas = [" ".join(palabras[i:i + 6]) for i in range(0, max(1, len(palabras) - 6), 10)]
        votos = [sum(a in p for a in anclas) for p in norm]
        cerca = [i for i in range(max(0, desde - 3), min(len(norm), desde + 25)) if votos[i]]
        if cerca:  # mejor puntaje cerca; en empate, la página más próxima
            hallada = max(cerca, key=lambda i: (votos[i], -abs(i - desde)))
        else:      # salto lejano: exigir al menos 2 frases coincidentes
            mejor = max(range(len(norm)), key=lambda i: votos[i])
            hallada = mejor if votos[mejor] >= 2 else None
        if hallada is not None:
            desde = hallada
        halladas.append(hallada)

    # Número impreso en la página (el que ve el lector); si no hay, la página del PDF
    impresas: list[str | None] = []
    for p in paginas:
        m = re.match(r"\s*(\d{1,4})\s", p)
        impresas.append(m.group(1) if m else None)

    def rotulo(a: int, b: int) -> str:
        if a == b:
            return f" (p. {impresas[a]})" if impresas[a] else f" (p. {a + 1} del PDF)"
        if impresas[a] and impresas[b]:
            return f" (pp. {impresas[a]}–{impresas[b]})"
        return f" (pp. {a + 1}–{b + 1} del PDF)"

    for k, f in enumerate(fragmentos):
        if halladas[k] is not None:
            f.pagina = halladas[k] + 1
            f.etiqueta += rotulo(halladas[k], halladas[k])
            continue
        antes = next((halladas[j] for j in range(k - 1, -1, -1) if halladas[j] is not None), None)
        despues = next((halladas[j] for j in range(k + 1, len(halladas)) if halladas[j] is not None), None)
        a, b = (antes if antes is not None else despues), (despues if despues is not None else antes)
        if a is None:
            continue
        a, b = sorted((a, b))
        f.pagina = a + 1
        f.etiqueta += rotulo(a, b)
    return sum(h is not None for h in halladas)


# ---------------------------------------------------------------- embeddings

def embeber(textos: list[str], cfg: dict, prefijo: str = "") -> np.ndarray:
    url = cfg["servidores"]["embeddings_url"].rstrip("/") + "/v1/embeddings"
    lote = cfg["embeddings"]["lote"]
    vectores = []
    with httpx.Client(timeout=300) as cliente:
        for i in range(0, len(textos), lote):
            entrada = [prefijo + t for t in textos[i:i + lote]]
            r = cliente.post(url, json={"input": entrada})
            r.raise_for_status()
            datos = sorted(r.json()["data"], key=lambda d: d["index"])
            vectores.extend(d["embedding"] for d in datos)
    m = np.asarray(vectores, dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
    return m


# ---------------------------------------------------------------- índice

PALABRAS_VACIAS = set("""
que qué cual cuál como cómo cuando cuándo donde dónde quien quién para por con sin sobre entre
los las del una uno unos unas este esta estos estas ese esa eso esos esas aquel son ser fue han
hay más mas muy pero sus nos les lo la el en de al se su es y o u a e ni no si sí ya también
plantea explica explicame explicá dame decime cuales cuáles según libro caso detalle ilustra
""".split())


def _palabras(texto: str) -> set[str]:
    return {w for w in _normalizar(texto).split() if len(w) >= 3 and w not in PALABRAS_VACIAS}


class Indice:
    def __init__(self, fragmentos: list[Fragmento], vectores: np.ndarray):
        self.fragmentos = fragmentos
        self.vectores = vectores
        # Para el refuerzo léxico: palabras de cada fragmento e IDF de cada palabra
        self._palabras = [_palabras(texto_para_embeber(f)) for f in fragmentos]
        n = max(1, len(fragmentos))
        df: dict[str, int] = {}
        for ps in self._palabras:
            for w in ps:
                df[w] = df.get(w, 0) + 1
        self._idf = {w: float(np.log(n / c)) for w, c in df.items()}

    def refuerzo_lexico(self, consulta: str) -> np.ndarray:
        """Por fragmento, fracción (ponderada por IDF) de las palabras raras de la
        consulta que contiene. Palabras presentes en más del 10 % de los
        fragmentos no cuentan: el refuerzo apunta a nombres propios y términos
        específicos ("IBM", "Ford", "homeostasis")."""
        corte = float(np.log(10))
        raras = {w: self._idf[w] for w in _palabras(consulta) if self._idf.get(w, 0) > corte}
        r = np.zeros(len(self.fragmentos), dtype=np.float32)
        total = sum(raras.values())
        if total:
            for i, ps in enumerate(self._palabras):
                r[i] = sum(v for w, v in raras.items() if w in ps) / total
        return r

    def __len__(self) -> int:
        return len(self.fragmentos)

    def guardar(self, carpeta: Path, nombre: str) -> None:
        carpeta.mkdir(parents=True, exist_ok=True)
        np.save(carpeta / f"{nombre}.npy", self.vectores)
        with open(carpeta / f"{nombre}.json", "w", encoding="utf-8") as f:
            json.dump([asdict(x) for x in self.fragmentos], f, ensure_ascii=False, indent=1)

    @classmethod
    def cargar(cls, carpeta: Path, nombre: str) -> "Indice | None":
        js, npy = carpeta / f"{nombre}.json", carpeta / f"{nombre}.npy"
        if not (js.exists() and npy.exists()):
            return None
        with open(js, encoding="utf-8") as f:
            fragmentos = [Fragmento(**d) for d in json.load(f)]
        return cls(fragmentos, np.load(npy))

    def buscar(self, vector: np.ndarray, top_k: int, umbral: float,
               consulta: str = "", peso_lexico: float = 0.0) -> list[tuple[Fragmento, float]]:
        """Similitud coseno + peso_lexico × refuerzo léxico (si se pasa la consulta)."""
        if not len(self):
            return []
        sims = self.vectores @ vector
        if consulta and peso_lexico:
            sims = sims + peso_lexico * self.refuerzo_lexico(consulta)
        orden = np.argsort(-sims)[:top_k]
        return [(self.fragmentos[i], float(sims[i])) for i in orden if sims[i] >= umbral]
