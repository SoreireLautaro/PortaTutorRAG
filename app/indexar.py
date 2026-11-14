"""Genera el índice del libro una sola vez.

Requiere el servidor de embeddings en marcha (iniciar.sh / iniciar.bat lo
levanta; su dirección se toma de config.yaml o de PORTATUTOR_EMBEDDINGS_URL).

Uso:
    python app/indexar.py                 # indexa el libro
    python app/indexar.py --solo-fragmentar   # muestra los fragmentos sin embeber
    python app/indexar.py --probar "¿Qué es la reingeniería?"   # prueba búsqueda
"""
from __future__ import annotations

import argparse
import sys

import rag


def fragmentos_libro(cfg: dict) -> list[rag.Fragmento]:
    texto = rag.ruta(cfg["libro"]["markdown"]).read_text(encoding="utf-8")
    frags = rag.fragmentar_markdown(texto, cfg)
    pdf = rag.ruta(cfg["libro"]["pdf"])
    if pdf.exists():
        n = rag.asignar_paginas(frags, rag.paginas_pdf(pdf))
        print(f"PDF encontrado: {n}/{len(frags)} fragmentos con número de página.")
    else:
        print(f"Sin PDF en {cfg['libro']['pdf']}: las citas indicarán solo la sección.")
    return frags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-fragmentar", action="store_true")
    ap.add_argument("--probar", metavar="PREGUNTA")
    args = ap.parse_args()
    cfg = rag.cargar_config()
    carpeta = rag.ruta(cfg["indice"]["carpeta"])

    if args.probar:
        idx = rag.Indice.cargar(carpeta, "libro")
        if idx is None:
            print("No hay índice. Ejecutá primero: python app/indexar.py")
            return 1
        v = rag.embeber([args.probar], cfg, cfg["embeddings"]["prefijo_consulta"])[0]
        print(f"Umbral actual: {cfg['busqueda']['umbral']}\n")
        for f, s in idx.buscar(v, top_k=8, umbral=-1, consulta=args.probar,
                               peso_lexico=cfg["busqueda"]["peso_lexico"]):
            marca = "✔" if s >= cfg["busqueda"]["umbral"] else "✘"
            print(f"{marca} {s:.3f}  {f.etiqueta}\n         {f.texto[:110]}…")
        return 0

    frags = fragmentos_libro(cfg)
    palabras = [len(f.texto.split()) for f in frags]
    print(f"Libro: {len(frags)} fragmentos (palabras: mín {min(palabras)}, "
          f"media {sum(palabras) // len(palabras)}, máx {max(palabras)}).")

    if args.solo_fragmentar:
        for f in frags:
            print(f"- [{len(f.texto.split()):>3} pal.] {f.etiqueta}")
        return 0

    print("Calculando embeddings del libro…", flush=True)
    vect = rag.embeber([rag.texto_para_embeber(f) for f in frags], cfg,
                       cfg["embeddings"]["prefijo_documento"])
    rag.Indice(frags, vect).guardar(carpeta, "libro")
    print(f"Índice guardado en {carpeta}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
