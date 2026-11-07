from src.database.my_connector import db
# Imported here because update_*() below raises HTTPException; the module used it
# without importing it, so any call with an empty payload was a NameError (500)
# instead of the intended 400. Found by the repo's F821 gate, not by a test.
from fastapi import HTTPException, status
from src.database.models import CandidateVacancies
from typing import Dict


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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Not get candidate_vacancy_dict")
    fields_to_update = [f"{key}=%s" for key in candidate_vacancy.keys()]
    params = list(candidate_vacancy.values())
    query = f"UPDATE candidate_vacancies SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(candidate_vacancy_id)
    db.execute_query(query, tuple(params))
    return {"message": "Candidate vacancy updated successfully"}


def delete_candidate_vacancy(candidate_vacancy_id: int):
    query = "DELETE FROM candidate_vacancies WHERE id=%s"
    db.execute_query(query, (candidate_vacancy_id,))
