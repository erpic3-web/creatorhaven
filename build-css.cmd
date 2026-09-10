@echo off
setlocal
cd /d "%~dp0tailwind"
if not exist node_modules\.bin\tailwindcss.cmd (
  echo Installing tailwindcss + cli locally - first run only
  call npm install --no-audit --no-fund || exit /b 1
)
call npm run --silent build
echo.
echo Wrote static\brain.css - restart the server if templates changed (Flask caches them).
endlocal
