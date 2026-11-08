from service.infrastructure.database.connection import Database
from service.domain.models import InfoCandidates
from service.infrastructure.database.connection import db
from typing import Dict
from service.shared.debug import debug_ex, debug_err, debug_info



def get_all_info_candidates():
    query = "SELECT * FROM info_candidates"
    return db.fetch_all(query)


def get_info_candidate_by_id(info_candidate_id: int):
    query = "SELECT * FROM info_candidates WHERE id=%s"
    return db.fetch_one(query, (info_candidate_id,))


def create_info_candidate(info_candidate: InfoCandidates):
    query = ("INSERT INTO info_candidates (name, phone, gender,"
             " date_birth, file_id, embedding_id)"
             " VALUES (%s, %s, %s, %s, %s, %s)")
    params = (info_candidate.Name, info_candidate.Phone, info_candidate.Gender,
              info_candidate.DateBirth, info_candidate.FileID, info_candidate.EmbeddingID)
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_info_candidate(info_candidate_id: int, info_candidate: Dict):
    fields_to_update = [f"{key}=%s" for key in info_candidate.keys()]
    params = list(info_candidate.values())
    query = f"UPDATE info_candidates SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(info_candidate_id)
    db.execute_query(query, tuple(params))


def delete_info_candidate(info_candidate_id: int):
    query = "DELETE FROM info_candidates WHERE id=%s"
    db.execute_query(query, (info_candidate_id,))
