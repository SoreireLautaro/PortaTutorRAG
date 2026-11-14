# Manual del Asistente de Reingeniería de Procesos

Asistente de IA local que orienta procesos de reingeniería a partir del libro *Reingeniería de Procesos para Sistemas de Información* (Escobar, Santillán Escudero y Silva, UNLaR, ed. 2026). Funciona **sin Internet** desde el pendrive.

## Requisitos

- Computadora con CPU de 64 bits (x86-64) y **8 GB de RAM** como mínimo.
- Windows 10/11 o Linux (distribución moderna, glibc 2.35 o superior).
- No hace falta instalar nada ni tener conexión.

## Cómo iniciar

- **Windows:** doble clic en `iniciar.bat`.
- **Linux:** abrir una terminal en la carpeta del pendrive y ejecutar `bash iniciar.sh`.

El navegador se abre solo en `http://127.0.0.1:7860` (si ese puerto está ocupado, el script usa otro y muestra cuál). Si no se abre, escribir la dirección que muestra la ventana a mano. La primera ejecución tarda más porque genera el índice del libro (si no viene ya generado).

Para salir: cerrar la ventana negra (Windows) o presionar `Ctrl+C` en la terminal (Linux).

## Uso

El asistente guía el trabajo en 7 fases y pide validación al cerrar cada una:

1. Delimitación del proceso, propósito y destinatarios.
2. Relevamiento AS-IS: actividades, responsables, entradas, salidas, recursos y tiempos.
3. Diagnóstico: problemas, demoras, duplicaciones, actividades que no agregan valor.
4. Rediseño TO-BE fundamentado en el libro.
5. Aporte de los sistemas de información y la IA.
6. Indicadores para comparar la situación inicial con la esperada.
7. Plan preliminar de implementación y seguimiento.

Cada respuesta separa **📖 Según el libro** (con cita) de **💡 Inferencia / recomendación**. Al pie, el programa agrega la lista de fragmentos del libro consultados, con capítulo, sección y página cuando se conoce. El panel derecho muestra esos fragmentos y su puntaje de similitud.

En la caja de mensaje, **Enter** envía y **Shift+Enter** agrega una nueva línea.

El botón **💾 Guardar conversación** crea un registro en `registros/`.

## Fuente de conocimiento

El asistente usa como **única fuente** el libro de la cátedra (`datos/ReingenieriaProcesos-Definitivo2026-EDITADO-Libro.md`, con el `.pdf` para ubicar las páginas). No consulta otros documentos ni Internet. Si un tema no está en el libro, lo dice en lugar de completar con otras fuentes.

## Limitaciones

- En equipos sin placa de video, cada respuesta tarda entre 10 y 30 segundos (o más en equipos lentos).
- El modelo es pequeño (4 mil millones de parámetros): puede equivocarse, malinterpretar o resumir mal un fragmento. **Verificar siempre contra el libro** las citas importantes.
- Si ningún fragmento supera el umbral de similitud, el asistente lo informa y no cita el libro.
- La decisión final siempre es del usuario.

## Estructura de la carpeta

```
./
├── iniciar.bat / iniciar.sh   levanta los servidores y la app
├── preparar.sh                (solo para armar el pendrive, requiere Internet)
├── bin/        llama-server para Windows y Linux
├── python/     Python portable con sus librerías
├── modelos/    llm.gguf (Qwen3 4B Instruct Q4_K_M), embeddings.gguf (bge-m3)
├── app/        app.py, indexar.py, rag.py, config.yaml, prompt_sistema.txt
├── datos/      libro (.md y .pdf), indice/
├── registros/  conversaciones guardadas
└── LICENCIAS/
```

## Para quienes mantienen el tutor

- **Armar el pendrive** (PC Linux con Internet y `uv`): `bash preparar.sh`. Descarga unos 3,5 GB.
- **Reindexar** después de cambiar el libro: con los servidores en marcha, `python app/indexar.py`.
- **Ajustar el umbral:** `python app/indexar.py --probar "pregunta"` muestra la similitud de los mejores fragmentos; el valor se cambia en `app/config.yaml` (`busqueda.umbral`).
- **Comportamiento:** se edita en `app/prompt_sistema.txt`.
