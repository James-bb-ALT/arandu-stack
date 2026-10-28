@echo off
cd /d "%~dp0"
set "ARANDU_TEST_PYTHON=%~dp0..\Arandu NLU\.venv-ai-lab-313\Scripts\python.exe"
if exist "%ARANDU_TEST_PYTHON%" (
  "%ARANDU_TEST_PYTHON%" -m unittest discover -s tests -v
) else (
  python -m unittest discover -s tests -v
)
pause
