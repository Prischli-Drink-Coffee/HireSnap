from service.infrastructure.database.connection import db
# Imported here because update_*() below raises HTTPException; the module used it
# without importing it, so any call with an empty payload was a NameError (500)
# instead of the intended 400. Found by the repo's F821 gate, not by a test.
from fastapi import HTTPException, status
from service.domain.models import PersonalityVacancies
from typing import Dict


def get_all_personality_vacancies():
    query = "SELECT * FROM personality_vacancies"
    return db.fetch_all(query)


def get_personality_vacancy_by_id(personality_vacancy_id: int):
    query = "SELECT * FROM personality_vacancies WHERE id=%s"
    return db.fetch_one(query, (personality_vacancy_id,))


def create_personality_vacancy(personality_vacancy: PersonalityVacancies):
    query = "INSERT INTO personality_vacancies (personality_id, vacancy_id, distance) VALUES (%s, %s, %s)"
    params = personality_vacancy.PersonalityID, personality_vacancy.VacancyID, personality_vacancy.Distance
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_personality_vacancy(personality_vacancy_id: int, personality_vacancy: Dict):
    if not personality_vacancy:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Not get personality_vacancy_dict")
    fields_to_update = [f"{key}=%s" for key in personality_vacancy.keys()]
    params = list(personality_vacancy.values())
    query = f"UPDATE personality_vacancies SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(personality_vacancy_id)
    db.execute_query(query, tuple(params))
    return {"message": "Personality vacancy updated successfully"}


def delete_personality_vacancy(personality_vacancy_id: int):
    query = "DELETE FROM personality_vacancies WHERE id=%s"
    db.execute_query(query, (personality_vacancy_id,))
