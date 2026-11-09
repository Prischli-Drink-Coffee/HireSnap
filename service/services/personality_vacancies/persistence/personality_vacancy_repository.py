from typing import Dict

from service.infrastructure.database.connection import db
from service.models import PersonalityVacancies
from service.shared.errors import InvalidPayloadError


def get_all_personality_vacancies():
    query = "SELECT * FROM personality_vacancies"
    return db.fetch_all(query)


def get_personality_vacancy_by_id(personality_vacancy_id: int):
    query = "SELECT * FROM personality_vacancies WHERE id=%s"
    return db.fetch_one(query, (personality_vacancy_id,))


def create_personality_vacancy(personality_vacancy: PersonalityVacancies):
    query = (
        "INSERT INTO personality_vacancies (personality_id, vacancy_id, distance) "
        "VALUES (%s, %s, %s)"
    )
    params = (
        personality_vacancy.PersonalityID,
        personality_vacancy.VacancyID,
        personality_vacancy.Distance,
    )
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_personality_vacancy(personality_vacancy_id: int, personality_vacancy: Dict):
    if not personality_vacancy:
        # InvalidPayloadError (400), not fastapi.HTTPException: a persistence module is
        # not a place the framework may reach, and the message is byte-identical. The
        # single ServiceError handler renders the same 400 the HTTPException carried.
        raise InvalidPayloadError("Not get personality_vacancy_dict")
    fields_to_update = [f"{key}=%s" for key in personality_vacancy.keys()]
    params = list(personality_vacancy.values())
    query = f"UPDATE personality_vacancies SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(personality_vacancy_id)
    db.execute_query(query, tuple(params))
    return {"message": "Personality vacancy updated successfully"}


def delete_personality_vacancy(personality_vacancy_id: int):
    query = "DELETE FROM personality_vacancies WHERE id=%s"
    db.execute_query(query, (personality_vacancy_id,))
