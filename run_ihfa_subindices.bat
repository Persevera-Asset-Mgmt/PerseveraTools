@echo off
setlocal enabledelayedexpansion

:: Sub-índices do IHFA (persevera_anbima_ihfa_*). A carteira muda a cada
:: trimestre, mas as cotas são diárias: pode rodar no mesmo agendamento do
:: factor_zoo. Leva ~10 min (histórico de classificação de ~920 fundos).
:: Pass "nopause" when scheduled (Task Scheduler): pause would wait for a key forever.
set "PAUSE_CMD=pause"
if /i "%~1"=="nopause" set "PAUSE_CMD="

:: PerseveraTools project root
set "PROJECT_DIR=G:\Drives compartilhados\INVESTIMENTOS\Quant\PerseveraTools"

:: System Python (Microsoft Store 3.11) — persevera_tools is installed here
where python >nul 2>&1
if errorlevel 1 (
    echo python not found on PATH.
    %PAUSE_CMD%
    exit /b 1
)

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo Failed to change directory to %PROJECT_DIR%
    %PAUSE_CMD%
    exit /b 1
)

echo.
echo Running: python -m persevera_tools.custom_series.ihfa_subindices
python -m persevera_tools.custom_series.ihfa_subindices
if errorlevel 1 (
    echo Error occurred while running the IHFA sub-indices.
    %PAUSE_CMD%
    exit /b 1
)

echo.
echo IHFA sub-indices completed successfully.
%PAUSE_CMD%
exit /b 0
