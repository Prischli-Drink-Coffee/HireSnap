import subprocess
import os
import filecmp
import logging
import shutil
import sysconfig
import setup_common
import sys

errors = 0  # Определение переменной 'errors'
log = logging.getLogger('sd')

# ANSI escape-код для желтого цвета
YELLOW = '\033[93m'
RESET_COLOR = '\033[0m'


def install_venv():
    # Раньше здесь ставился requirements_external.txt - GPU-стек (torch+cu116,
    # tensorflow) внутрь интерпретатора backend. Этот стек переехал в ml/ (у
    # sidecar свой образ со своим torch), поэтому ставим только зависимости
    # самого backend.
    setup_common.check_repo_version()
    setup_common.check_python()

    # Обновление pip, если необходимо
    setup_common.install('--upgrade pip')

    setup_common.install_requirements('requirements.txt', check_no_verify_flag=True)


def sync_bits_and_bytes_files():
    import filecmp

    """
    Проверяйте файлы на наличие «разных» битов и байтов и копируйте только при необходимости.
    Эта функция специфична для ОС Windows.
    """

    # Выполнять только в Windows
    if os.name != 'nt':
        print('Эта функция применима только к ОС Windows...')
        return

    try:
        log.info(f'Копирование bitsandbytes файлов...')
        # Определить исходный и конечный каталоги
        source_dir = os.path.join(os.getcwd(), 'bitsandbytes_windows')

        dest_dir_base = os.path.join(
            sysconfig.get_paths()['purelib'], 'bitsandbytes'
        )

        # Очистить кеш сравнения файлов
        filecmp.clear_cache()

        # Перебрать каждый файл в исходном каталоге
        for file in os.listdir(source_dir):
            source_file_path = os.path.join(source_dir, file)

            # Определите каталог назначения на основе имени файла
            if file in ('main.py', 'paths.py'):
                dest_dir = os.path.join(dest_dir_base, 'cuda_setup')
            else:
                dest_dir = dest_dir_base

            dest_file_path = os.path.join(dest_dir, file)

            # Сравните исходный файл с файлом назначения
            if os.path.exists(dest_file_path) and filecmp.cmp(
                source_file_path, dest_file_path
            ):
                log.debug(
                    f'Пропуск {source_file_path}, так как он уже существует в {dest_dir}'
                )
            else:
                # Скопируйте файл из источника в место назначения, сохранив метаданные исходного файла.
                log.debug(f'Копирование {source_file_path} в {dest_dir}')
                shutil.copy2(source_file_path, dest_dir)

    except FileNotFoundError as fnf_error:
        log.error(f'Ошибка файл не найден: {fnf_error}')
    except PermissionError as perm_error:
        log.error(f'Ошибка доступа: {perm_error}')
    except Exception as e:
        log.error(f'Непредвиденная ошибка: {e}')


def cudnn_install():
    cudnn_src = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), '..\cudnn_windows'
    )
    cudnn_dest = os.path.join(sysconfig.get_paths()['purelib'], 'torch', 'lib')

    log.info(f'Проверка CUDNN файлов в {cudnn_dest}...')
    if os.path.exists(cudnn_src):
        if os.path.exists(cudnn_dest):
            # check for different files
            filecmp.clear_cache()
            for file in os.listdir(cudnn_src):
                src_file = os.path.join(cudnn_src, file)
                dest_file = os.path.join(cudnn_dest, file)
                # if dest file exists, check if it's different
                if os.path.exists(dest_file):
                    if not filecmp.cmp(src_file, dest_file, shallow=False):
                        shutil.copy2(src_file, cudnn_dest)
                else:
                    shutil.copy2(src_file, cudnn_dest)
            log.info('Копирование CUDNN файлов завершено...')
        else:
            log.warning(f'Директория {cudnn_dest} не существует')
    else:
        log.error(f'Ошибка установки: "{cudnn_src}" не может быть найден.')


def main_menu():
    setup_common.clear_screen()
    while True:
        print('\n Установка и настройка проекта:\n')
        print('1. Установка виртуальной среды *venv*')
        print('2. (Необязательно) Установка cudnn файлов')
        print('3. (Необязательно) Установка bitsandbytes-windows')
        print('4. Выход')

        choice = input('\nСделайте выбор: ')
        print('')

        if choice == '1':
            install_venv()
        elif choice == '2':
            cudnn_install()
        elif choice == '3':
            setup_common.install('--upgrade bitsandbytes-windows', reinstall=True)
        elif choice == '4':
            print('Выход из меню')
            sys.exit()
        else:
            print('Выберите между 1-4')


if __name__ == '__main__':
    setup_common.ensure_base_requirements()
    setup_common.setup_logging()
    main_menu()
