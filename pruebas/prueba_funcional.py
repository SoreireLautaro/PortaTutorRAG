"""Prueba funcional del asistente sin navegador (casos de la §7 del README).

Uso (desde la raíz del proyecto, con los servidores en marcha):
    python/linux/bin/python3 pruebas/prueba_funcional.py

Grupos: A = caso completo (9 turnos, ~17 min en CPU), B = pregunta conceptual,
C = temas apenas mencionados / ausentes.
Por defecto corre todos y escribe registros/prueba_funcional_<fecha>.md con
cada respuesta y su tiempo (se guarda después de cada turno).
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

raiz = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("salida", nargs="?")
ap.add_argument("--casos", default="A,B,C", help="grupos a correr, separados por comas")
args = ap.parse_args()
casos = {c.strip().upper() for c in args.casos.split(",")}
salida = (Path(args.salida) if args.salida else
          raiz / "registros" / f"prueba_funcional_{datetime.now():%Y%m%d_%H%M}.md")
salida.parent.mkdir(exist_ok=True)
sys.path.insert(0, str(raiz / "app"))
import app  # noqa: E402

registro = [f"# Prueba funcional (umbral {app.CFG['busqueda']['umbral']})\n"]


def turno(caso, historial, mensaje):
    historial = historial + [{"role": "user", "content": mensaje}]
    t0 = time.time()
    final = None
    for final in app.responder(historial):
        pass
    hist, _ = final
    seg = time.time() - t0
    resp = app.texto_de(hist[-1])
    error = "Error al consultar" in resp
    registro.append(f"## {caso}\n\n**Usuario:** {mensaje}\n\n**Tiempo:** {seg:.1f} s"
                    f"{'  ⚠️ ERROR' if error else ''}\n\n{resp}\n")
    salida.write_text("\n".join(registro), encoding="utf-8")
    print(f"[{seg:5.1f}s] {caso}{' ERROR' if error else ''}", flush=True)
    return hist


saludo = [{"role": "assistant", "content": app.SALUDO}]

# A + E: caso completo / conversación larga
conversacion = [
    "Quiero analizar el proceso de inscripción a materias de la facultad.",
    "El propósito es que los alumnos se inscriban a las materias del cuatrimestre. "
    "Los destinatarios son los alumnos regulares; el proceso empieza cuando se abre el período "
    "de inscripción y termina cuando el alumno tiene su comprobante.",
    "Validado. Las actividades: el alumno completa un formulario en papel, Bedelía controla "
    "correlatividades a mano con la libreta, el docente firma, Bedelía carga en una planilla Excel "
    "y entrega el comprobante. Tarda entre 3 y 7 días.",
    "Responsables: alumno, personal de Bedelía (2 personas) y docentes. Recursos: formularios, "
    "planilla Excel, una PC. Hay unas 1.500 inscripciones por cuatrimestre.",
    "Sí, validado. Pasemos al diagnóstico.",
    "Validado. ¿Qué rediseño proponés?",
    "De acuerdo. ¿Qué sistemas de información o IA podrían ayudar?",
    "Validado. Proponé indicadores para comparar antes y después.",
    "Validado. Armá un plan preliminar de implementación y seguimiento.",
]
if "A" in casos:
    h = saludo
    for i, m in enumerate(conversacion, start=1):
        h = turno(f"A/E. Caso completo — turno {i}", h, m)

if "B" in casos:  # pregunta conceptual
    turno("B. Pregunta conceptual", saludo,
          "¿Qué plantea el caso IBM Credit y qué principio de reingeniería ilustra?")

if "C" in casos:  # tema apenas mencionado (Six Sigma: una oración, sin DMAIC) y tema ausente (ISO 9001)
    turno("C1. Tema apenas mencionado en el libro", saludo, "Explicame Six Sigma y el ciclo DMAIC en detalle.")
    turno("C2. Tema ausente del libro", saludo, "¿Cómo se aplica la norma ISO 9001 en una auditoría de calidad?")

print("Listo:", salida)
