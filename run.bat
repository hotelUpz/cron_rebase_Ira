@echo off
chcp 65001 > nul
cls

echo [1/4] Проверка виртуального окружения...
if not exist .venv (
    echo [1/4] Создание виртуального окружения...
    python -m venv .venv
)

echo [2/4] Активация окружения и установка зависимостей...
call .venv\Scripts\activate
pip install -r requirements.txt

echo [3/4] Запуск main.py...
python main.py

echo [4/4] Работа завершена.
pause
