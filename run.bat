@echo off
chcp 65001 >nul
title Subtitle Translator v1.2

echo.
echo ============================================================
echo           SUBTITLE TRANSLATOR - KHOI DONG
echo ============================================================
echo.

:: Check if virtual environment exists
if not exist ".venv" (
    echo [*] Tao moi virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [-] Loi tao virtual environment
        pause
        exit /b 1
    )
)

:: Activate virtual environment
echo [*] Kich hoat virtual environment...
call .venv\Scripts\activate.bat

:: Check if dependencies are installed
pip show faster-whisper >nul 2>&1
if errorlevel 1 (
    echo [*] Cai dat dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [-] Loi cai dat dependencies
        pause
        exit /b 1
    )
)

:: Check if Ollama is running
echo [*] Kiem tra Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [!] Ollama chua chay hoac chua cai dat!
    echo [!] Vui long khoi dong Ollama: ollama serve
    echo.
)

:: Check config.yaml exists
if not exist "config.yaml" (
    echo [*] Tao cau hinh mac dinh...
    (
        echo whisper:
        echo   model_size: large-v3-turbo
        echo   device: cuda
        echo   compute_type: float16
        echo   language: ja
        echo project:
        echo   name: my_project
        echo   source_lang: Japanese
        echo   target_lang: Vietnamese
        echo   input_srt:
        echo   output_srt:
        echo window:
        echo   size: 6
        echo   history: 12
        echo   future: 4
        echo translation:
        echo   mode: default
        echo models:
        echo   default:
        echo     name: qwen3.5:9b
        echo     ollama_url: http://localhost:11434/api/generate
        echo   uncen:
        echo     name: huihui_ai/qwen3-abliterated:8b-v2
        echo     ollama_url: http://localhost:11434/api/generate
    ) > config.yaml
)

echo.
echo ============================================================
echo         San sang! Khoi dong Subtitle Translator...
echo ============================================================
echo.
echo Huong dan:
echo   - Dam bao Ollama da chay (ollama serve)
echo   - Pull model neu can:
echo       ollama pull qwen3.5:9b
echo       ollama pull huihui_ai/qwen3-abliterated:8b-v2
echo   - Chinh sua config.yaml de cau hinh
echo.
echo ============================================================
echo.

:: Run the application
python main.py --interactive

:: Keep window open if exited
echo.
echo Nhan phim bat ky de dong...
pause >nul
