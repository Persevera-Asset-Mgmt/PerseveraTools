@echo off
setlocal enabledelayedexpansion

:: PerseveraTools project root
set "PROJECT_DIR=G:\Drives compartilhados\INVESTIMENTOS\Quant\PerseveraTools"

:: System Python (Microsoft Store 3.11) — persevera_tools is installed here
where python >nul 2>&1
if errorlevel 1 (
    echo python not found on PATH.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo Failed to change directory to %PROJECT_DIR%
    pause
    exit /b 1
)

:: Step 1: Bloomberg company data, incremental (recent window + restated histories)
call :run_script -m persevera_tools.data.factor_zoo.company_data
if errorlevel 1 goto :failed

:: Step 2: Ticker successions (seed + Fibery "Codigos Anteriores") -> merge old codes
call :run_script -m persevera_tools.data.factor_zoo.aliases --apply
if errorlevel 1 goto :failed

:: Step 3: Derived factors, incremental (recent dates, changed rows only; dependents after independents)
call :run_script -m persevera_tools.data.factor_zoo --incremental
if errorlevel 1 goto :failed

echo.
echo All factor_zoo scripts completed successfully.
pause
exit /b 0

:run_script
echo.
echo Running: python %*
python %*
if errorlevel 1 (
    echo Error occurred while running: python %*
    exit /b 1
)
exit /b 0

:failed
pause
exit /b 1
