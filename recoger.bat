@echo off
rem Recogida diaria de precios. Pensado para el Programador de tareas de Windows.
cd /d "%~dp0"
echo ===== %date% %time% >> recogida.log
python -m vuelos recoger >> recogida.log 2>&1
python -m vuelos informe >> recogida.log 2>&1
