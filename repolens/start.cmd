@echo off
setlocal
cd /d "%~dp0"
set "REPOLENS_PYTHON=python"
if exist "..\venv\Scripts\python.exe" set "REPOLENS_PYTHON=..\venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "REPOLENS_PYTHON=.venv\Scripts\python.exe"
"%REPOLENS_PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo Python / Streamlit not found. Please follow README.md to install requirements.
  pause
  exit /b 1
)
echo RepoLens: http://127.0.0.1:8501
echo Press Ctrl+C to stop.
"%REPOLENS_PYTHON%" -m streamlit run web.py --server.address 127.0.0.1 --server.port 8501 --server.headless false --browser.gatherUsageStats false
pause
