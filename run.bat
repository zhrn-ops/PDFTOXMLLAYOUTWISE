@echo off
setlocal
set "PROJECT_ROOT=%~dp0"
set "PYTHON=C:\venvs\p313\Scripts\python.exe"
set "ENTRYPOINT=%PROJECT_ROOT%pdf_to_jats\main.py"

if not exist "%PYTHON%" (
  set "PYTHON=%PROJECT_ROOT%p313\Scripts\python.exe"
  if not exist "%PYTHON%" (
    echo Python interpreter not found: "%PYTHON%"
    exit /b 1
  )
)

if not exist "%ENTRYPOINT%" (
  echo Application entrypoint not found: "%ENTRYPOINT%"
  exit /b 1
)

"%PYTHON%" "%ENTRYPOINT%"
