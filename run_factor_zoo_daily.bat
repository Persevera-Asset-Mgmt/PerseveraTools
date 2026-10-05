@echo off
setlocal enabledelayedexpansion

:: Set the Anaconda path
set "ANACONDA_PATH=C:\Users\Turandot\anaconda3"

:: PerseveraTools project root (must be installed in the active conda env)
set "PROJECT_DIR=G:\Drives compartilhados\INVESTIMENTOS\Quant\PerseveraTools"

:: Activate Anaconda environment
echo Activating Anaconda environment...
call "%ANACONDA_PATH%\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate Anaconda environment.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo Failed to change directory to %PROJECT_DIR%
    pause
    exit /b 1
)

:: Step 1: Bloomberg company data (raw inputs) -> factor_zoo
call :run_script -m persevera_tools.data.factor_zoo.company_data
if errorlevel 1 goto :failed

:: Step 2: Derived factors — independent phase
call :run_script -m persevera_tools.data.factor_zoo --phase independent
if errorlevel 1 goto :failed

:: Step 3: Derived factors — dependent phase (requires step 2 uploaded)
call :run_script -m persevera_tools.data.factor_zoo --phase dependent
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
