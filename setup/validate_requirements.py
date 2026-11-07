import os
import re
import sys
import argparse
import setup_common

# Получить абсолютный путь к каталогу текущего файла (каталог проекта)
project_directory = os.path.dirname(os.path.abspath(__file__))

# Проверка, присутствует ли каталог «setup» в каталоге проекта.
if "setup" in project_directory:
    # Если каталог «setup» присутствует, переместитесь на один уровень выше в родительский каталог.
    project_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Добавление каталога проекта в начало пути поиска Python.
sys.path.insert(0, project_directory)

from src.utils.custom_logging import setup_logging

# Настройка ведения журнала
log = setup_logging()


def main():
    setup_common.check_repo_version()
    # Разобрать аргументы командной строки
    parser = argparse.ArgumentParser(
        description='Validate that requirements are satisfied.'
    )
    parser.add_argument(
        '-r',
        '--requirements',
        type=str,
        help='Path to the requirements file.',
    )
    parser.add_argument('--debug', action='store_true', help='Debug on')
    args = parser.parse_args()

    # GPU-стек больше не принадлежит backend: torch/tensorflow переехали в ml/
    # и ставятся образом sidecar-а, поэтому requirements_external.txt удалён.
    setup_common.install_requirements('requirements.txt', check_no_verify_flag=True)

    if args.requirements:
        setup_common.install_requirements(args.requirements, check_no_verify_flag=True)


if __name__ == '__main__':
    main()
