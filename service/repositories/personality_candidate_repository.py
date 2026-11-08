from service.infrastructure.database.connection import db
# Imported here because update_*() below raises HTTPException; the module used it
# without importing it, so any call with an empty payload was a NameError (500)
# instead of the intended 400. Found by the repo's F821 gate, not by a test.
from fastapi import HTTPException, status
from service.domain.models import PersonalityCandidates
from typing import Dict


def get_all_personality_candidates():
    query = "SELECT * FROM personality_candidates"
    return db.fetch_all(query)


def get_personality_candidate_by_id(personality_candidate_id: int):
    query = "SELECT * FROM personality_candidates WHERE id=%s"
    return db.fetch_one(query, (personality_candidate_id,))


def create_personality_candidate(personality_candidate: PersonalityCandidates):
    query = "INSERT INTO personality_candidates (personality_id, user_id, distance) VALUES (%s, %s, %s)"
    params = personality_candidate.PersonalityID, personality_candidate.UserID, personality_candidate.Distance
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_personality_candidate(personality_candidate_id: int, personality_candidate: Dict):
    if not personality_candidate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Not get personality_candidate_dict")
    fields_to_update = [f"{key}=%s" for key in personality_candidate.keys()]
    params = list(personality_candidate.values())
    query = f"UPDATE personality_candidates SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(personality_candidate_id)
    db.execute_query(query, tuple(params))
    return {"message": "Personality candidate updated successfully"}


def delete_personality_candidate(personality_candidate_id: int):
    query = "DELETE FROM personality_candidates WHERE id=%s"
    db.execute_query(query, (personality_candidate_id,))
