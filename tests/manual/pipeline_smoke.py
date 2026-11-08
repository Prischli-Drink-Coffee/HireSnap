from http.client import responses
from datetime import datetime
import pytest
import random
import string
from copy import deepcopy
from fastapi.testclient import TestClient
from service.api.app import app
from service.configuration.logging import setup_logging
from service.domain.models import GenderUser

log = setup_logging()
client = TestClient(app)

"""

Ошибка Not Found вероятно говорит о неправильно созданном роуте, или не правильно переданным параметрам в тесты

"""


# Вспомогательная функция для генерации случайных данных
def generate_random_data(data_type, length=8):
    if data_type == "string":
        return ''.join(random.choices(string.ascii_letters, k=length))
    elif data_type == "number":
        return random.randint(1, 1000000)
    elif data_type == "datetime":
        return datetime.now()
    elif data_type == "float":
        return round(random.uniform(0, 1), 6)
    return None


# Вспомогательная функция для выполнения запросов
def api_request(method, url, json_data=None, headers=None, data=None):
    response = client.request(method, url, json=json_data, data=data, headers=headers)
    return response


# Вспомогательная функция для проверки статуса и получения данных
def assert_response(response, expected_status, keys=None):
    log.info("-------------------------------------")
    assert response.status_code == expected_status, \
        f"Unexpected status code: {response.status_code}, Response: {response.text}"
    if keys:
        response_data = response.json()
        if isinstance(response_data, list):
            for item in response_data:
                for key in keys:
                    assert key in item
        else:
            for key in keys:
                assert key in response_data
        return response_data
    return None


# Генерация тестовых данных для различных сущностей
def generate_test_data(entity_type):
    data_map = {
        "user": {
            "email": generate_random_data("string"),
            "password": generate_random_data("string"),
            "type": "candidate",
            "info_id": None,
            "created_at": f"{generate_random_data('datetime')}",
            "role": "admin"
        },
        "info_candidate": {
            "name": generate_random_data("string"),
            "phone": generate_random_data("string"),
            "gender": 'male',
            "data_birth": None,
            "file_id": None,
            "embedding_id": None
        },
        "candidate_vacancy": {
            "user_id": None,
            "vacancy_id": None,
            "distance": 0.002345
        },
        "embedding": {
            "url": generate_random_data("string")
        },
        "file": {
            "url": generate_random_data("string")
        },
        "vacancy": {
            "name": generate_random_data("string"),
            "salary": "80000, 90000",
            "description": generate_random_data("string"),
            "skill": "SQL, Python",
            "embedding_id": None
        },
        "magic": {
            "name": generate_random_data("string"),
            "candidate_explanation": generate_random_data("string"),
            "vacancy_explanation": generate_random_data("string"),
            "candidate_answer": "Нет",
            "vacancy_answer": "Да",
            "file_id": None
        },
        "personality": {
            "name": generate_random_data("string"),
            "description": generate_random_data("string"),
            "embedding_id": None
        },
        "personality_vacancy": {
            "personality_id": None,
            "vacancy_id": None,
            "distance": 0.043467
        },
        "personality_candidate": {
            "personality_id": None,
            "user_id": None,
            "distance": 0.043467
        }
    }
    return data_map.get(entity_type)


def setup_entity(entity_type, endpoint, token):
    if entity_type == "user":
        info_candidate_id = setup_entity("info_candidate", "info_candidates",
                                         token)
        user_data = generate_test_data("user")
        entity_data = {**user_data,
                       "info_id": info_candidate_id}
    elif entity_type == "info_candidate":
        file_id = setup_entity("file", "files", token)
        embedding_id = setup_entity("embedding", "embeddings", token)
        info_candidate_data = generate_test_data("info_candidate")
        entity_data = {**info_candidate_data,
                       "file_id": file_id,
                       "embedding_id": embedding_id}
    elif entity_type == "candidate_vacancy":
        user_id = setup_entity("user", "users", token)
        vacancy_id = setup_entity("vacancy", "vacancies", token)
        candidate_vacancy_data = generate_test_data("candidate_vacancy")
        entity_data = {**candidate_vacancy_data,
                       "user_id": user_id,
                       "vacancy_id": vacancy_id}
    elif entity_type == "vacancy":
        embedding_id = setup_entity("embedding", "embeddings", token)
        vacancy_data = generate_test_data("vacancy")
        entity_data = {**vacancy_data,
                       "embedding_id": embedding_id}
    elif entity_type == "magic":
        file_id = setup_entity("file", "files", token)
        magic_data = generate_test_data("magic")
        entity_data = {**magic_data,
                       "file_id": file_id}
    elif entity_type == "personality":
        embedding_id = setup_entity("embedding", "embeddings", token)
        personality_data = generate_test_data("personality")
        entity_data = {**personality_data,
                       "embedding_id": embedding_id}
    elif entity_type == "personality_vacancy":
        personality_id = setup_entity("personality", "personalities", token)
        vacancy_id = setup_entity("vacancy", "vacancies", token)
        personality_vacancy_data = generate_test_data("personality_vacancy")
        entity_data = {**personality_vacancy_data,
                       "personality_id": personality_id,
                       "vacancy_id": vacancy_id}
    elif entity_type == "personality_candidate":
        personality_id = setup_entity("personality", "personalities", token)
        user_id = setup_entity("user", "users", token)
        personality_candidate_data = generate_test_data("personality_candidate")
        entity_data = {**personality_candidate_data,
                       "personality_id": personality_id,
                       "user_id": user_id}
    else:
        entity_data = generate_test_data(entity_type)
    log.info(f"Creating {entity_type} with data: {entity_data}")
    response = api_request("POST", f"server/{endpoint}/", json_data=entity_data,
                           headers={"Authorization": f"Bearer {token}"})
    log.info(f"POST {endpoint}/ response: {response.json()}")
    response_data = assert_response(response, 200, keys=["id"])
    return response_data["id"]


# Функция для удаления сущности
def teardown_entity(endpoint, entity_id, token):
    response = api_request("DELETE", f"server/{endpoint}/{entity_id}",
                           headers={"Authorization": f"Bearer {token}"})
    assert_response(response, 200)


# Токен доступа администратора
access_token = None
admin_data = [{
    "email": "admin@mail.ru",
    "password": "admin",
    "type_user": "candidate",
    "role": "admin"
}]


def create_admin():
    global access_token
    admin_data.append(generate_test_data("user"))
    try:
        response = api_request("POST", "server/signup/", data=admin_data[0])
        access_token = response.json()["access_token"]
        response = api_request("POST", "server/signin/", data={
            "email": admin_data[0]["email"], "password": admin_data[0]["password"]
        })
    except Exception as e:
        log.error(f"{response.json()}")
        raise e


# Инициализация администратора
create_admin()


@pytest.mark.parametrize("entity_type, endpoint, expected_keys", [
    ("user", "users", ["email"]),
    ("info_candidate", "info_candidates", ["name"]),
    ("candidate_vacancy", "candidate_vacancies", ["distance"]),
    ("vacancy", "vacancies", ["name"]),
    ("magic", "magics", ["name"]),
    ("personality", "personalities", ["name"]),
    ("personality_vacancy", "personality_vacancies", ["distance"]),
    ("personality_candidate", "personality_candidates", ["distance"])
])
def test_create_and_get_entity(entity_type, endpoint, expected_keys):
    log.info("-------------------------------------")
    log.info(f"entity_type: {entity_type}, endpoint: {endpoint}, expected_keys: {expected_keys}")
    entity_id = setup_entity(entity_type, endpoint, access_token)
    response = api_request("GET", f"server/{endpoint}/")
    assert_response(response, 200, keys=["id"] + expected_keys)
    response = api_request("GET", f"server/{endpoint}/{entity_type}_id/{entity_id}")
    assert_response(response, 200, keys=["id"] + expected_keys)
    teardown_entity(endpoint, entity_id, access_token)


@pytest.mark.parametrize("entity_type, endpoint, update_data", [
    ("user", "users", {"email": generate_random_data("string")}),
    ("info_candidate", "info_candidates", {"name": generate_random_data("string")}),
    ("candidate_vacancy", "candidate_vacancies", {"distance": generate_random_data("float")}),
    ("vacancy", "vacancies", {"name": generate_random_data("string")}),
    ("magic", "magics", {"name": generate_random_data("string")}),
    ("personality", "personalities", {"name": generate_random_data("string")}),
    ("personality_vacancy", "personality_vacancies", {"distance": generate_random_data("float")}),
    ("personality_candidate", "personality_candidates", {"distance": generate_random_data("float")})
])
def test_update_entity(entity_type, endpoint, update_data):
    log.info("-------------------------------------")
    log.info(f"entity_type: {entity_type}, endpoint: {endpoint}, update_data: {update_data}")
    entity_id = setup_entity(entity_type, endpoint, access_token)
    response = api_request("GET", f"server/{endpoint}/{entity_type}_id/{entity_id}")
    test_data = response.json()
    response = api_request("PUT", f"server/{endpoint}/{entity_id}", json_data=test_data,
                           headers={"Authorization": f"Bearer {access_token}"})
    assert_response(response, 200)
    updated_data = deepcopy(test_data)
    updated_data.update(update_data)
    response = api_request("PUT", f"server/{endpoint}/{entity_id}", json_data=updated_data,
                           headers={"Authorization": f"Bearer {access_token}"})
    assert_response(response, 200)
    if update_data.get("id"):
        entity_id = update_data.get("id")
    teardown_entity(endpoint, entity_id, access_token)


# Удаление администратора
def del_admin():
    email = admin_data[0].get("email")
    response = api_request("GET", f"server/users/email/{email}",
                           headers={"Authorization": f"Bearer {access_token}"})
    admin = response.json()
    teardown_entity("users", admin["id"], access_token)


del_admin()
