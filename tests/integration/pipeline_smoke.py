"""End-to-end CRUD walk against a live server: the check the unit suite cannot make.

WHY this is not in the default run: it needs a real uvicorn process (or an
importable app) AND a real MySQL with the schema loaded, and it CREATES and
DELETES rows for every entity type. A test suite that cannot run on a laptop
without Docker is a test suite people learn to skip, so this module skips itself
unless HIRESNAP_INTEGRATION=1 is set explicitly.

WHAT IT PROVES that the hermetic tests cannot: each router is wired to the
service, repository and table it claims to be, with the field names the frontend
actually sends. A rename in one layer passes every unit test and fails here on the
first 422.

RUN IT (from backend/, with the stack up and .env pointing at it):

    HIRESNAP_INTEGRATION=1 python -m pytest tests/integration/pipeline_smoke.py -q

This file was moved from tests/manual/pipeline_smoke.py and converted from
import-time side effects to fixtures: the old module created an admin user while
being *imported*, so merely collecting it mutated the database - collection must
stay read-only even when the flag is set.

NOTE ON NON-ENGLISH DATA: the magic rows send "Да"/"Нет" as the stored answers
because those are domain values the product reads back, not prose. They are
payload data and are deliberately left as they were.

A 404 from one of these endpoints usually means a router prefix or a path-parameter
name drifted; a 422 means the request schema and the test payload disagree.
"""

from __future__ import annotations

import os
import random
import string
from copy import deepcopy
from datetime import datetime

import pytest

if os.environ.get("HIRESNAP_INTEGRATION") != "1":
    # allow_module_level, not a skipif marker: this module imports the application
    # and would build a TestClient at collection time, so the guard has to fire
    # before those imports run.
    pytest.skip(
        "integration test: set HIRESNAP_INTEGRATION=1 (needs a live MySQL and schema)",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

from service.api.app import app
from service.configuration.logging import get_logger

log = get_logger(__name__)

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "admin@mail.ru"
ADMIN_PASSWORD = "admin"


# ---------------------------------------------------------------------------
# data generation
# ---------------------------------------------------------------------------
def generate_random_data(data_type, length=8):
    if data_type == "string":
        return "".join(random.choices(string.ascii_letters, k=length))
    if data_type == "number":
        return random.randint(1, 1000000)
    if data_type == "datetime":
        return datetime.now()
    if data_type == "float":
        return round(random.uniform(0, 1), 6)
    return None


def generate_test_data(entity_type):
    """One representative payload per table, using the field names the API accepts."""
    data_map = {
        "user": {
            "email": generate_random_data("string"),
            "password": generate_random_data("string"),
            "type": "candidate",
            "info_id": None,
            "created_at": "%s" % (generate_random_data("datetime"),),
            "role": "admin",
        },
        "info_candidate": {
            "name": generate_random_data("string"),
            "phone": generate_random_data("string"),
            "gender": "male",
            "data_birth": None,
            "file_id": None,
            "embedding_id": None,
        },
        "candidate_vacancy": {
            "user_id": None,
            "vacancy_id": None,
            "distance": 0.002345,
        },
        "embedding": {"url": generate_random_data("string")},
        "file": {"url": generate_random_data("string")},
        "vacancy": {
            "name": generate_random_data("string"),
            "salary": "80000, 90000",
            "description": generate_random_data("string"),
            "skill": "SQL, Python",
            "embedding_id": None,
        },
        "magic": {
            "name": generate_random_data("string"),
            "candidate_explanation": generate_random_data("string"),
            "vacancy_explanation": generate_random_data("string"),
            "candidate_answer": "Нет",
            "vacancy_answer": "Да",
            "file_id": None,
        },
        "personality": {
            "name": generate_random_data("string"),
            "description": generate_random_data("string"),
            "embedding_id": None,
        },
        "personality_vacancy": {
            "personality_id": None,
            "vacancy_id": None,
            "distance": 0.043467,
        },
        "personality_candidate": {
            "personality_id": None,
            "user_id": None,
            "distance": 0.043467,
        },
    }
    return data_map.get(entity_type)


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    """Build the client lazily so collection alone never constructs the app."""
    return TestClient(app)


@pytest.fixture(scope="module")
def access_token(client):
    """Create the admin user once per module and delete it afterwards.

    The old module did this at import time (and again at import time to clean
    up), which meant a collection error left a stray admin row behind. A fixture
    with yield always runs its teardown, including after a failed test.
    """
    payload = {
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "type_user": "candidate",
        "role": "admin",
    }
    response = client.request("POST", "server/signup/", data=payload)
    assert response.status_code == 200, "admin signup failed: %s" % (response.text,)
    token = response.json()["access_token"]
    response = client.request(
        "POST", "server/signin/", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, "admin signin failed: %s" % (response.text,)
    yield token

    found = client.request(
        "GET",
        "server/users/email/%s" % (ADMIN_EMAIL,),
        headers={"Authorization": "Bearer %s" % (token,)},
    )
    if found.status_code == 200:
        client.request(
            "DELETE",
            "server/users/%s" % (found.json()["id"],),
            headers={"Authorization": "Bearer %s" % (token,)},
        )


def api_request(client, method, url, json_data=None, headers=None, data=None):
    return client.request(method, url, json=json_data, data=data, headers=headers)


def assert_response(response, expected_status, keys=None):
    log.info("-------------------------------------")
    assert response.status_code == expected_status, "Unexpected status code: %s, Response: %s" % (
        response.status_code,
        response.text,
    )
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


def setup_entity(client, entity_type, endpoint, token):
    """Create the row plus every row it depends on, recursively."""
    if entity_type == "user":
        info_candidate_id = setup_entity(client, "info_candidate", "info_candidates", token)
        entity_data = {**generate_test_data("user"), "info_id": info_candidate_id}
    elif entity_type == "info_candidate":
        file_id = setup_entity(client, "file", "files", token)
        embedding_id = setup_entity(client, "embedding", "embeddings", token)
        entity_data = {
            **generate_test_data("info_candidate"),
            "file_id": file_id,
            "embedding_id": embedding_id,
        }
    elif entity_type == "candidate_vacancy":
        user_id = setup_entity(client, "user", "users", token)
        vacancy_id = setup_entity(client, "vacancy", "vacancies", token)
        entity_data = {
            **generate_test_data("candidate_vacancy"),
            "user_id": user_id,
            "vacancy_id": vacancy_id,
        }
    elif entity_type == "vacancy":
        embedding_id = setup_entity(client, "embedding", "embeddings", token)
        entity_data = {**generate_test_data("vacancy"), "embedding_id": embedding_id}
    elif entity_type == "magic":
        file_id = setup_entity(client, "file", "files", token)
        entity_data = {**generate_test_data("magic"), "file_id": file_id}
    elif entity_type == "personality":
        embedding_id = setup_entity(client, "embedding", "embeddings", token)
        entity_data = {**generate_test_data("personality"), "embedding_id": embedding_id}
    elif entity_type == "personality_vacancy":
        personality_id = setup_entity(client, "personality", "personalities", token)
        vacancy_id = setup_entity(client, "vacancy", "vacancies", token)
        entity_data = {
            **generate_test_data("personality_vacancy"),
            "personality_id": personality_id,
            "vacancy_id": vacancy_id,
        }
    elif entity_type == "personality_candidate":
        personality_id = setup_entity(client, "personality", "personalities", token)
        user_id = setup_entity(client, "user", "users", token)
        entity_data = {
            **generate_test_data("personality_candidate"),
            "personality_id": personality_id,
            "user_id": user_id,
        }
    else:
        entity_data = generate_test_data(entity_type)

    log.info("Creating %s with data: %s", entity_type, entity_data)
    response = api_request(
        client,
        "POST",
        "server/%s/" % (endpoint,),
        json_data=entity_data,
        headers={"Authorization": "Bearer %s" % (token,)},
    )
    log.info("POST %s/ response: %s", endpoint, response.json())
    return assert_response(response, 200, keys=["id"])["id"]


def teardown_entity(client, endpoint, entity_id, token):
    response = api_request(
        client,
        "DELETE",
        "server/%s/%s" % (endpoint, entity_id),
        headers={"Authorization": "Bearer %s" % (token,)},
    )
    assert_response(response, 200)


# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------
ENTITIES = [
    ("user", "users", ["email"]),
    ("info_candidate", "info_candidates", ["name"]),
    ("candidate_vacancy", "candidate_vacancies", ["distance"]),
    ("vacancy", "vacancies", ["name"]),
    ("magic", "magics", ["name"]),
    ("personality", "personalities", ["name"]),
    ("personality_vacancy", "personality_vacancies", ["distance"]),
    ("personality_candidate", "personality_candidates", ["distance"]),
]


@pytest.mark.parametrize("entity_type, endpoint, expected_keys", ENTITIES)
def test_create_and_get_entity(client, access_token, entity_type, endpoint, expected_keys):
    log.info(
        "entity_type: %s, endpoint: %s, expected_keys: %s", entity_type, endpoint, expected_keys
    )
    entity_id = setup_entity(client, entity_type, endpoint, access_token)

    response = api_request(client, "GET", "server/%s/" % (endpoint,))
    assert_response(response, 200, keys=["id"] + expected_keys)

    response = api_request(client, "GET", "server/%s/%s_id/%s" % (endpoint, entity_type, entity_id))
    assert_response(response, 200, keys=["id"] + expected_keys)

    teardown_entity(client, endpoint, entity_id, access_token)


@pytest.mark.parametrize(
    "entity_type, endpoint, update_field",
    [
        ("user", "users", "email"),
        ("info_candidate", "info_candidates", "name"),
        ("candidate_vacancy", "candidate_vacancies", "distance"),
        ("vacancy", "vacancies", "name"),
        ("magic", "magics", "name"),
        ("personality", "personalities", "name"),
        ("personality_vacancy", "personality_vacancies", "distance"),
        ("personality_candidate", "personality_candidates", "distance"),
    ],
)
def test_update_entity(client, access_token, entity_type, endpoint, update_field):
    """PUT the row back unchanged, then PUT it with one field changed.

    Both calls are asserted because they fail differently: a schema that
    requires a field the GET response omits passes the second and fails the first.
    """
    entity_id = setup_entity(client, entity_type, endpoint, access_token)

    response = api_request(client, "GET", "server/%s/%s_id/%s" % (endpoint, entity_type, entity_id))
    test_data = response.json()

    response = api_request(
        client,
        "PUT",
        "server/%s/%s" % (endpoint, entity_id),
        json_data=test_data,
        headers={"Authorization": "Bearer %s" % (access_token,)},
    )
    assert_response(response, 200)

    updated_data = deepcopy(test_data)
    updated_data[update_field] = generate_random_data(
        "float" if update_field == "distance" else "string"
    )
    response = api_request(
        client,
        "PUT",
        "server/%s/%s" % (endpoint, entity_id),
        json_data=updated_data,
        headers={"Authorization": "Bearer %s" % (access_token,)},
    )
    assert_response(response, 200)

    if updated_data.get("id"):
        entity_id = updated_data.get("id")
    teardown_entity(client, endpoint, entity_id, access_token)
