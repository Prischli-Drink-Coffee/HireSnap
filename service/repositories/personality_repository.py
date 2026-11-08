from service.infrastructure.database.connection import Database
from service.domain.models import Personalities
from service.infrastructure.database.connection import db
from typing import Dict


def get_all_personalities():
    query = "SELECT * FROM personalities"
    return db.fetch_all(query)


def get_personality_by_id(personality_id: int):
    query = "SELECT * FROM personalities WHERE id=%s"
    return db.fetch_one(query, (personality_id,))


def create_personality(personality: Personalities):
    query = ("INSERT INTO personalities (name, description, embedding_id)"
             " VALUES (%s, %s, %s)")
    params = (personality.Name, personality.Description, personality.EmbeddingID)
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_personality(personality_id: int, personality: Dict):
    fields_to_update = [f"{key}=%s" for key in personality.keys()]
    params = list(personality.values())
    query = f"UPDATE personalities SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(personality_id)
    db.execute_query(query, tuple(params))


def delete_personality(personality_id: int):
    query = "DELETE FROM personalities WHERE id=%s"
    db.execute_query(query, (personality_id,))
