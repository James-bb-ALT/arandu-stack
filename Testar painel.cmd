@echo off
cd /d "%~dp0"
"..\Arandu NLU\.venv-ai-lab-313\Scripts\python.exe" -m unittest discover -s tests -v
pause
