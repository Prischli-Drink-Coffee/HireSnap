@echo off

:: Установка переменной пути проекта
set PROJECT_PATH=%~dp0src
set PYTHONPATH=%PROJECT_PATH%;%PYTHONPATH%

:: Деактивация активной среды (если активна)
call .\venv\Scripts\deactivate.bat

:: Проверка на локальные модули
python.exe .\setup\check_local_modules.py --no_question

:: Активация виртуальной среды
call .\venv\Scripts\activate.bat
set PATH=%PATH%;%~dp0venv\Lib\site-packages\torch\lib

:: Валидация requirements
python.exe .\setup\validate_requirements.py

:: Очистка setup.log (если требуется)
python.exe .\src\utils\clear_setup_log.py

:: Меню выбора интерфейса
python.exe .\setup\gui_windows.py

:: Определяем путь к файлу .env
set ENV_FILE=.env

:: Используем findstr для поиска строки, начинающейся с "CHOICE=", и присваиваем её переменной var
for /f "tokens=2 delims==" %%a in ('findstr "^CHOICE=" "%ENV_FILE%"') do set var=%%a

:: Запуск выбранного пайплайна
if %errorlevel% equ 0 (
    REM Проверка, был ли батник запущен двойным кликом
    if /i "%comspec% /c %~0 " equ "%cmdcmdline:"=%" (
        REM echo Этот скрипт запущен с помощью двойного нажатия.
        if %var% == '1' (
            cmd /k python.exe ./src/pipeline/server.py
        ) else if %var% == '2' (
            cmd /k pytest ./src/pipeline/test.py
        ) else (
            echo Not found value: %var%
        )
    ) else (
        REM echo Этот скрипт был запущен с помощью командной строки.
        if %var% == '1' (
            python.exe ./src/pipeline/server.py
        ) else if %var% == '2' (
            pytest ./src/pipeline/test.py
        ) else (
            echo Not found value: %var%
        )
    )
)
