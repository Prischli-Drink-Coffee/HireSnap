from service.infrastructure.database.connection import db
from service.domain.models import Files


def get_all_files():
    query = "SELECT * FROM files"
    return db.fetch_all(query)


def get_file_by_id(file_id: int):
    query = "SELECT * FROM files WHERE id=%s"
    return db.fetch_one(query, (file_id,))


def create_file(file: Files):
    query = "INSERT INTO files (url) VALUES (%s)"
    params = file.Url
    cursor = db.execute_query(query, params)
    return cursor.lastrowid


def update_file(file_id: int, file: Files):
    fields_to_update = [f"{key}=%s" for key in file.keys()]
    params = list(file.values())
    query = f"UPDATE files SET {', '.join(fields_to_update)} WHERE id=%s"
    params.append(file_id)
    db.execute_query(query, tuple(params))


def delete_file(file_id: int):
    query = "DELETE FROM files WHERE id=%s"
    db.execute_query(query, (file_id,))
