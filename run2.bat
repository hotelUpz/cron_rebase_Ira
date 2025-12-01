@echo off
chcp 65001 > nul
cls

REM !!! Укажи здесь путь к Python 3.12 !!!
set PYTHON_PATH=C:\Python312\python.exe

echo [1/7] Проверка виртуального окружения...
if not exist .venv (
    echo [1/7] Создание виртуального окружения с Python 3.12...
    "%PYTHON_PATH%" -m venv .venv
)

echo [2/7] Активация окружения...
call .venv\Scripts\activate

echo [3/7] Обновление pip и setuptools...
python -m pip install --upgrade pip setuptools wheel

echo [4/7] Установка зависимостей из requirements.txt...
pip install -r requirements2.txt

echo [5/7] Удаление несовместимого aiodns (если есть)...
pip uninstall -y aiodns

echo [6/7] Установка pandas_ta...
pip install pandas-ta==0.3.14b0

echo [7/7] Запуск main.py...
python main.py

echo.
echo Работа завершена.
pause