from service.infrastructure.database.connection import Database
from service.domain.models import Magics
from service.infrastructure.database.connection import db
from typing import Dict


def get_all_magics():
    query = "SELECT * FROM magics"
    return db.fetch_all(query)


def get_magic_by_id(magic_id: int):
    query = "SELECT * FROM magics WHERE id=%s"
    return db.fetch_one(query, (magic_id,))


def create_magic(magic: Magics):
    query = ("INSERT INTO magics (name, candidate_explanation, vacancy_explanation,"
             " candidate_answer, vacancy_answer, file_id)"
             " VALUES (%s, %s, %s, %s, %s, %s)")
    params = (magic.Name, magic.CandidateExplanation, magic.VacancyExplanation,
              magic.CandidateAnswer, magic.VacancyAnswer, magic.FileID)
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_magic(magic_id: int, magic: Dict):
    fields_to_update = [f"{key}=%s" for key in magic.keys()]
    params = list(magic.values())
    query = f"UPDATE magics SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(magic_id)
    db.execute_query(query, tuple(params))


def delete_magic(magic_id: int):
    query = "DELETE FROM magics WHERE id=%s"
    db.execute_query(query, (magic_id,))
