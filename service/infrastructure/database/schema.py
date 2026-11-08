import os

import numpy as np
import pandas as pd
import pymysql
from pandas import read_csv

from service.configuration.env import Env
from service.configuration.logging import setup_logging
from service.configuration.paths import DATABASE_DIR, schema_file

env = Env()
log = setup_logging()


class CreateSQL:
    def __init__(self):
        # Определение пути к SQL-файлу
        # Resolved through service.configuration.paths so the dump is found in
        # backend/database/ regardless of the working directory and regardless of
        # the file name case (HireSnap.sql vs a lowercase database name).
        self.path = str(DATABASE_DIR)
        self.path_to_sql = str(schema_file(env.__getattr__("DB")))
        self.path_to_data = os.path.join(env.__getattr__("DATA_PATH"), "tables")

        # Установка соединения с базой данных
        self.connection = pymysql.connect(
            host=env.__getattr__("DB_HOST"),
            port=int(env.__getattr__("DB_PORT")),
            user=env.__getattr__("DB_USER"),
            password=env.__getattr__("DB_PASSWORD"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )

    @staticmethod
    def remove_column_from_csv(df):
        # Проходим по всем файлам в указанной директории
        try:
            # Чтение CSV файла в DataFrame
            df = df.replace({pd.NA: None, np.nan: None})
            column_name1 = "Unnamed: 0.1"
            column_name2 = "Unnamed: 0"
            column_name3 = "Unnamed: 0.2"
            # Проверяем, существует ли столбец с нужным названием
            if column_name1 in df.columns:
                if column_name2 in df.columns:
                    if column_name3 in df.columns:
                        # Удаляем столбец
                        df = df.drop(columns=[column_name1])
                        df = df.drop(columns=[column_name2])
                        df = df.drop(columns=[column_name3])
                # Сохраняем обновленный DataFrame обратно в CSV
                print("Столбец удален из датафрейма")
                return df
            else:
                log.error("Столбец не найден в датафрейме")
                return df
        except Exception as e:
            log.exception(f"Ошибка при обработке датафрейме: {e}")

    def read_sql(self):
        try:
            tables = {}
            # Проверяем наличие таблицы mbti, taro
            for file in os.listdir(self.path_to_data):
                if file.endswith(".csv"):
                    table_name = file.split(".")[0]
                    if table_name not in [
                        "embeddings",
                        "files",
                        "personalities",
                        "users",
                        "info_candidates",
                        "vacancies",
                    ]:
                        log.warning(f"Не найдена таблица {table_name}")
                        continue
                    else:
                        # Открываем и записываем таблицы в переменные
                        table = read_csv(os.path.join(self.path_to_data, file))
                        tables[table_name] = table
                        log.info(f"Найдена таблица {table_name}")

            with self.connection.cursor() as cursor:
                # Создание базы данных, если она не существует
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{env.__getattr__('DB')}`")
                cursor.execute(f"USE `{env.__getattr__('DB')}`")

                # Открываем и выполняем SQL-скрипт
                with open(self.path_to_sql, "r", encoding="utf-8") as f:
                    sql_script = f.read()

                    # Разделение SQL-скрипта на отдельные запросы
                    statements = [stmt.strip() for stmt in sql_script.split(";") if stmt.strip()]

                    # Выполнение всех запросов в файле
                    for statement in statements:
                        try:
                            cursor.execute(statement)
                            log.info("Executed SQL statement: %s", statement)
                        except pymysql.MySQLError as e:
                            log.warning("SQL Warning: %s", exc_info=e)
                            # Логирование неудачных запросов может помочь в отладке

                # Вставка данных из загруженных CSV таблиц
                for table_name, table in tables.items():
                    table = self.remove_column_from_csv(table)
                    # Получаем имена столбцов и формируем плейсхолдеры
                    columns = ", ".join(table.columns)
                    placeholders = ", ".join(["%s"] * len(table.columns))
                    sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"

                    # Преобразуем DataFrame в список кортежей
                    data_tuples = [tuple(row) for row in table.to_numpy()]

                    # Выполняем вставку
                    try:
                        with self.connection.cursor() as cursor:
                            cursor.executemany(sql, data_tuples)
                        self.connection.commit()
                    except Exception as e:
                        self.connection.rollback()
                        log.warning("Ошибка при вставке данных в таблицу %s: %s", table_name, e)

                self.connection.commit()
                log.info("Database was created and SQL script executed successfully")
        except Exception as ex:
            log.warning("Error during SQL script execution", exc_info=ex)
        finally:
            self.connection.close()


if __name__ == "__main__":
    create_sql = CreateSQL()
    create_sql.read_sql()
