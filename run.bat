@echo off
setlocal
cd /d "%~dp0"
REM Pick a Python that has Flask (this box has several interpreters with uneven packages).
set "PY="
for %%P in ("C:\Python314\python.exe" "C:\Users\erpic\AppData\Local\Programs\Python\Python312\python.exe" "python") do (
  if not defined PY (
    %%P -c "import flask, sqlite3" >nul 2>&1 && set "PY=%%~P"
  )
)
if not defined PY (
  echo No Python with Flask found. Install with:  python -m pip install flask pillow
  pause
  exit /b 1
)
echo Using %PY%
"%PY%" app.py
pause
