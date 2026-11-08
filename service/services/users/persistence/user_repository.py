from typing import Dict

from service.infrastructure.database.connection import db
from service.models import Users


def get_all_users():
    query = "SELECT * FROM users"
    return db.fetch_all(query)


def get_user_by_id(user_id: int):
    query = "SELECT * FROM users WHERE id=%s"
    return db.fetch_one(query, (user_id,))


def get_user_by_email(email: str):
    query = "SELECT * FROM users WHERE email=%s"
    return db.fetch_one(query, (email,))


def create_user(user: Users):
    query = (
        "INSERT INTO users (email, password, type, info_id, created_at, role)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
    )
    params = (user.Email, user.Password, user.Type.value, user.InfoID, user.CreatedAt, user.Role)
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_user(user_id: int, user: Dict):
    fields_to_update = [f"{key}=%s" for key in user.keys()]
    params = list(user.values())
    query = f"UPDATE users SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(user_id)
    db.execute_query(query, tuple(params))


def delete_user(user_id: int):
    query = "DELETE FROM users WHERE id=%s"
    db.execute_query(query, (user_id,))
