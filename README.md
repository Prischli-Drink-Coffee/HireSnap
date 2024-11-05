
# 

| <img src="./src/images/logo.svg" width="100" height="100" alt="logo"> | <a href="https://git.io/typing-svg"><img src="https://readme-typing-svg.herokuapp.com?font=Fira+Code&weight=100&size=30&pause=1000&center=true&vCenter=true&multiline=true&repeat=false&random=false&width=950&lines=HireSnap" alt="Typing SVG" /></a> |
|-----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|


---

Описание проекта................................................................................................................................................................................................................................................................................................................................

---

> Каждый репозиторий на GitHub — это не просто код, это отражение чьего-то видения,
> идеи и стремления к совершенству. Проект на GitHub — это живой организм,
> который эволюционирует, учится и растет.

---


### Внешний вид графического интерфейса на React:

| ![Registration](./src/images/registration.svg) | ![Authorization](./src/images/authorization.svg) |
|------------------------------------------------|--------------------------------------------------|


Все страницы сайта можно посмотреть [**здесь**](./src/images/HireSnap.pdf) <br/>
Презентация проекта находится [**здесь**](./src/images/HireSnap_presentation.pdf)



- [Структура репозитория](#структура-репозитория)
- [Необходимые требования](#требования)
- [Установка и настройка](#установка-и-настройка)
  - [Установка виртуальной среды и зависимостей](#1-установка-виртуальной-среды-и-зависимостей)
    - [Linux server](#linuxmacos)
    - [Windows server](#windows)
  - [Запуск тестов](#2-запуск-тестов)
    - [Linux server](#linuxmacos-1)
    - [Windows server](#windows-1)




 ## Структура репозитория

```
    HireSnap/
    ├── logs # - папка храенения логов
    |   ├── setup #
    |   |   ├── *.log #
    |   └── app.log #
    ├── setup - кастомный установщик среды
    |   ├── check_local_modules.py - проверка локальных модулей
    |   ├── debug_info.py - вывод информации
    |   ├── setup_common.py
    |   ├── setup_windows.py - установочное меню
    |   └── validate_requirements.py - валидация модулей
    ├── src
    |   ├── database - база данных
    |   |   ├── models.py - модели
    |   |   └── my_connector.py - класс для подключения к базе данных
    |   ├── EDA - исследование данных
    |   ├── images - статические файлы
    |   ├── metrics # - метрики
    |   ├── modelling - модульные скрипты
    |   ├── pipeline - пайплайны
    |   |   ├── data_downloader.py - парсер набора данных
    |   |   ├── server.py - uvicorn сервер
    |   |   ├── test.py - тесты CRUD операций
    |   |   └── train.py - тренировка модели
    |   ├── repository - репозитории
    |   |   ├── file_repository.py
    |   |   └── ...
    |   ├── scripts - скрипты
    |   ├── service - сервисы
    |   |   ├── file_services.py
    |   |   └── ...
    |   ├── stuff - дополнительные файлы
    |   ├── utils
    |   |   ├── custom_logging.py - кастомный лог
    |   |   ├── exam_services.py - проверка на дубликаты
    |   |   ├── hashing.py - хеширование паролей
    |   |   ├── return_url_object.py - скрипт для формирования ссылок на изображения
    |   |   ├── write_file_into_server.py - запись файлов на сервер
    |   |   └── ...
    ├── venv # - виртуальная среда
    ├── .env # - переменные среды
    ├── .gitignore
    ├── category.yaml - категории для классификации
    ├── clear_setup_log - очистка логов
    ├── config.yaml - скрипт для работы с переменными средами
    ├── create_sql.py - скрипт для авто создания и заполнения базы данных
    ├── env.py - скрипт для работы с переменными средами
    ├── logging.yaml - конфигурационный файл для лога
    ├── main.bat - запуск сервера на windows
    ├── main.sh - запуск сервера на linux
    ├── HireSnap.sql - sql скрипт для создания базы данных
    ├── requirements.txt - список зависимостей
    ├── requirements_windows_tensorflow.txt - дополнительныйсписок зависимостей
    ├── server.sh - скрипт для запуска сервера на linux через pm2
    ├── setup.bat - установка среды на windows
    ├── setup.log # - файл лога
    └── setup.sh - установка среды на linux
```


### Реляционная база данных:

![image](./src/images/HireSnap.svg)



  ## Требования
- Python 3.9+ 
- `pm2` (для управления процессами сервера на Linux/MacOS)





 ## Установка и настройка

### 1. Установка виртуальной среды и зависимостей

#### Linux/MacOS

* Клонируйте репозиторий на сервер:
    ```bash
    git clone https://github.com/Prischli-Drink-Coffee/NaRuTagAI.git
    cd repo
    ```
* Запустите скрипт для установки виртуальной среды и всех необходимых зависимостей:
    ```bash
    bash setup.sh
    ```

* Следуйте инструкциям в консоли для установки пакетов

#### Windows

* Клонируйте репозиторий на сервер:
    ```cmd
    git clone https://github.com/Prischli-Drink-Coffee/HireSnap.git
    cd repo
    ```

* Запустите скрипт для установки виртуальной среды и всех необходимых зависимостей:
    ```cmd
    setup.bat
    ```
* Следуйте инструкциям в консоли для установки пакетов


### 2. Запуск сервера

* Запустите скрипт через pm2 для Linux/MacOS
    ```bash
    pm2 start 'bash server.sh'
    ```
* Запустите через меню выбора для Linux/MacOS
    ```bash
    main.sh # пункт 3 в меню
    ```
* Запустите скрипт для запуска сервера на Windows
    ```cmd
    main.bat # пункт 3 в меню
    ```


### 3. Запуск тестов CRUD операций базы данных

Для запуска автоматической проверки тестов с использованием `pytest`, выполните:
```bash
bash test_main.sh
   ```

Для запуска автоматической проверки тестов с использованием `pytest`, выполните:
```cmd
 test_main.bat
   ```

### Как запустить основные скрипты?

1. Чтобы обучить модель необходимо указать желаемые аргументы в файле `config.yaml`.

Для запуска скрипта для обучения просто запустите *main.sh* или *main.bat* в корневой директории проекта в зависимости от вашей ОС.
Выберите пукнт 2 в меню для запуска скрипта для обучения модели.
```
bash main.sh # пункт 3 в меню
```


2. Для того чтобы воспользоваться парсером набора данных рутуб видео, для этого также настройте
config.yaml и запустите main.sh или main.bat в корневой директории проекта и выберите пункт 1 в меню.

3. Сервер можно запустить локально или в веб-пространстве, тогда вы получите доступ к пользовательскому интерфейсу, для настройки сервера достаточно указать переменные среды в таком формате:

DATA_PATH=./data; <br/>
WEIGHTS_PATH=./src/weights; <br/>
METRICS_PATH=./src/metrics; <br/>
HOST=HOST; <br/>
SERVER_PORT=PORT; <br/>
DB_HOST=HOST <br/>
DB=DATABASE; <br/>
DB_PORT=PORT; <br/>
DB_USER=USER; <br/>
DB_PASSWORD=PASSWORD; <br/>
SECRET_KEY=SECRET_KEY <br/>


### Argparse

Для разбора аргументов командной строки используется библиотека Argparse.

Пример использования:
```bash
python3 source ./venv/bin/activate
python3 ./src/script/predict.py --title "Пример заголовка" --description "Пример описания"
  --use_lemmatization
  --max_length_token 512
  --max_length_generation 10
  --num_beams 5
  --num_return_sequences 5
```
Выходные данные
После выполнения скрипта в командной строке будет выведен список сгенерированных тегов:
Generated tags: ['тег1', 'тег2', 'тег3', ...]
