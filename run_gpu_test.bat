
@echo off
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1
python "%~dp0run_gpu_test.py" %*
