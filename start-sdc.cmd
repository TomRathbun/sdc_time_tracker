@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if exist "%~dp0smtp.local.cmd" call "%~dp0smtp.local.cmd" >nul
if defined SMTP_HOST (
    echo Mail: on
) else (
    echo Mail: off
)
uv run python run.py --no-ssl
