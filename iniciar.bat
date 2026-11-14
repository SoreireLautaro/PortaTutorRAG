@echo off
rem Inicia el asistente en Windows. Doble clic para ejecutar. Funciona sin Internet.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "RAIZ=%CD%"
set "BIN=%RAIZ%\bin\windows"
set "PY=%RAIZ%\python\windows\python.exe"
set "PYTHONUTF8=1"
set "OPENBLAS_NUM_THREADS=4"

for %%F in ("%BIN%\llama-server.exe" "%PY%" "%RAIZ%\modelos\llm.gguf" "%RAIZ%\modelos\embeddings.gguf") do (
  if not exist %%F ( echo Falta %%F. Revisa MANUAL.md. & pause & exit /b 1 )
)

rem --- Puertos dinamicos: se prefieren 8080/8081/7860; si estan ocupados, se elige uno libre
for /f "tokens=1,2,3" %%A in ('python\windows\python.exe app\puerto_libre.py 8080 8081 7860') do (
  set "PUERTO_LLM=%%A"
  set "PUERTO_EMB=%%B"
  set "PUERTO_UI=%%C"
)
set "PORTATUTOR_LLM_URL=http://127.0.0.1:%PUERTO_LLM%"
set "PORTATUTOR_EMBEDDINGS_URL=http://127.0.0.1:%PUERTO_EMB%"
set "PORTATUTOR_PUERTO_UI=%PUERTO_UI%"
if not "%PUERTO_LLM%"=="8080" echo Aviso: 8080 ocupado; el modelo usa el puerto %PUERTO_LLM%.
if not "%PUERTO_EMB%"=="8081" echo Aviso: 8081 ocupado; embeddings usa el puerto %PUERTO_EMB%.
if not "%PUERTO_UI%"=="7860" echo Aviso: 7860 ocupado; la interfaz usa el puerto %PUERTO_UI%.

echo Iniciando el modelo de lenguaje (puede tardar un minuto)...
rem -c debe coincidir con generacion.contexto_tokens de app\config.yaml
start "llm" /min "%BIN%\llama-server.exe" -m "%RAIZ%\modelos\llm.gguf" --host 127.0.0.1 --port %PUERTO_LLM% -c 8192 -np 1 --jinja
start "embeddings" /min "%BIN%\llama-server.exe" -m "%RAIZ%\modelos\embeddings.gguf" --host 127.0.0.1 --port %PUERTO_EMB% --embedding --pooling cls -c 8192 -b 8192 -ub 8192

call :esperar %PUERTO_EMB% embeddings || goto :fin
call :esperar %PUERTO_LLM% modelo || goto :fin

if not exist "%RAIZ%\datos\indice\libro.npy" (
  echo Primera ejecucion: generando el indice del libro...
  "%PY%" "%RAIZ%\app\indexar.py" || goto :fin
)

echo Abriendo http://127.0.0.1:%PUERTO_UI%  (cerrar esta ventana para salir)
"%PY%" "%RAIZ%\app\app.py" > "%RAIZ%\app.log" 2>&1

:fin
taskkill /f /im llama-server.exe >nul 2>&1
pause
exit /b

:esperar
for /l %%i in (1,1,180) do (
  "%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:%1/health', timeout=2)" >nul 2>&1 && exit /b 0
  timeout /t 1 /nobreak >nul
)
echo El servidor de %2 no arranco.
exit /b 1
