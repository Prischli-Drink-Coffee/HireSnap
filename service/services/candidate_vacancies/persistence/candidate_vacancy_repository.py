from typing import Dict

from service.infrastructure.database.connection import db
from service.models import CandidateVacancies
from service.shared.errors import InvalidPayloadError


def get_all_candidate_vacancies():
    query = "SELECT * FROM candidate_vacancies"
    return db.fetch_all(query)


def get_candidate_vacancy_by_id(candidate_vacancy_id: int):
    query = "SELECT * FROM candidate_vacancies WHERE id=%s"
    return db.fetch_one(query, (candidate_vacancy_id,))


def create_candidate_vacancy(candidate_vacancy: CandidateVacancies):
    query = "INSERT INTO candidate_vacancies (user_id, vacancy_id, distance) VALUES (%s, %s, %s)"
    params = candidate_vacancy.UserID, candidate_vacancy.VacancyID, candidate_vacancy.Distance
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_candidate_vacancy(candidate_vacancy_id: int, candidate_vacancy: Dict):
    if not candidate_vacancy:
        # InvalidPayloadError (400), not fastapi.HTTPException: a persistence module is
        # not a place the framework may reach, and the message is byte-identical. The
        # single ServiceError handler renders the same 400 the HTTPException carried.
        raise InvalidPayloadError("Not get candidate_vacancy_dict")
    fields_to_update = [f"{key}=%s" for key in candidate_vacancy.keys()]
    params = list(candidate_vacancy.values())
    query = f"UPDATE candidate_vacancies SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(candidate_vacancy_id)
    db.execute_query(query, tuple(params))
    return {"message": "Candidate vacancy updated successfully"}


def delete_candidate_vacancy(candidate_vacancy_id: int):
    query = "DELETE FROM candidate_vacancies WHERE id=%s"
    db.execute_query(query, (candidate_vacancy_id,))
