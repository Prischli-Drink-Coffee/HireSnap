from service.infrastructure.database.connection import Database
from service.domain.models import Vacancies
from service.infrastructure.database.connection import db
from typing import Dict


def get_all_vacancies():
    query = "SELECT * FROM vacancies"
    return db.fetch_all(query)


def get_vacancy_by_id(vacancy_id: int):
    query = "SELECT * FROM vacancies WHERE id=%s"
    return db.fetch_one(query, (vacancy_id,))


def create_vacancy(vacancy: Vacancies):
    query = ("INSERT INTO vacancies (name, description, salary, skill, embedding_id)"
             " VALUES (%s, %s, %s, %s, %s)")
    params = (vacancy.Name, vacancy.Description, vacancy.Salary, vacancy.Skill, vacancy.EmbeddingID)
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_vacancy(vacancy_id: int, vacancy: Dict):
    fields_to_update = [f"{key}=%s" for key in vacancy.keys()]
    params = list(vacancy.values())
    query = f"UPDATE vacancies SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(vacancy_id)
    db.execute_query(query, tuple(params))


def delete_vacancy(vacancy_id: int):
    query = "DELETE FROM vacancies WHERE id=%s"
    db.execute_query(query, (vacancy_id,))
