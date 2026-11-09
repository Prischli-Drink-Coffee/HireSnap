"""One test per repository function, run against a fake database handle.

WHAT THIS PINS THAT NOTHING ELSE DOES
------------------------------------
The route snapshot pins paths and models, and the application tests pin the rules, but
between them the SQL itself is unpinned: rename a column in a repository and every
route still answers, every rule still passes, and the failure surfaces in production as
a 500 on one screen. So each of the ten persistence modules is exercised through its
real functions - only the db handle is a double - and each test asserts the statement
text and the exact parameter tuple the driver would receive.

WHY THE EXPECTED STATEMENTS ARE WRITTEN OUT HERE
------------------------------------------------
This file is the contract, so the literals are the assertion: a helper that re-derived
"INSERT INTO users (email, ...)" from the model would agree with a repository that
reordered its columns. fake_db parses the statement instead of matching literals, so
the fake stays usable while these literals record what the wire format is supposed to be.

THE THREE QUIRKS THIS DELIBERATELY RECORDS (not fixed - see the comments at each test):
  * the INSERT column order of info_candidates, vacancies and magics does not follow
    the field order of the model the values are read from, so the two orders have to be
    pinned independently or a silent swap reorders data in the table;
  * create_file and create_embedding pass a BARE STRING where the driver expects a
    sequence - it works only because PyMySQL treats a scalar as a one-element list;
  * the three link repositories raise InvalidPayloadError(400) on an empty payload
    while their application layer raises domain errors. The repository sits outside the
    domain-error convention; the test pins today's behaviour rather than inventing one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from service.models import (
    CandidateVacancies,
    Embeddings,
    Files,
    GenderUser,
    InfoCandidates,
    Magics,
    Personalities,
    PersonalityCandidates,
    PersonalityVacancies,
    RoleEnum,
    TypeUser,
    Users,
    Vacancies,
)
from service.services.candidate_vacancies.persistence import candidate_vacancy_repository
from service.services.candidates.persistence import info_candidate_repository
from service.services.embeddings.persistence import embedding_repository
from service.services.files.persistence import file_repository
from service.services.magics.persistence import magic_repository
from service.services.personalities.persistence import personality_repository
from service.services.personality_candidates.persistence import personality_candidate_repository
from service.services.personality_vacancies.persistence import personality_vacancy_repository
from service.services.users.persistence import user_repository
from service.services.vacancies.persistence import vacancy_repository
from service.shared.errors import InvalidPayloadError
from tests.fixtures.fake_db import UnsupportedStatement, install

CREATED_AT = datetime(2024, 1, 2, 3, 4, 5)


@pytest.fixture
def fake_db(monkeypatch):
    """A FakeDatabase installed as the db of every repository module.

    Declared here rather than in conftest.py because the shared fixture registry is
    frozen while the restructure settles; the implementation is fake_db.install, so
    moving this one decorator later is the whole migration.
    """
    return install(monkeypatch)


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------
def test_users_select_and_keyed_select(fake_db):
    fake_db.tables["users"] = [
        {"id": 1, "email": "ada@example.com"},
        {"id": 2, "email": "b@example.com"},
    ]

    assert user_repository.get_all_users() == fake_db.tables["users"]
    assert fake_db.calls == [("SELECT * FROM users", ())]

    assert user_repository.get_user_by_id(2) == {"id": 2, "email": "b@example.com"}
    assert user_repository.get_user_by_email("ada@example.com")["id"] == 1
    assert fake_db.calls[-2:] == [
        ("SELECT * FROM users WHERE id=%s", (2,)),
        ("SELECT * FROM users WHERE email=%s", ("ada@example.com",)),
    ]


def test_users_missing_row_is_none_not_an_error(fake_db):
    """The 404 is the application layer's job; a repository reports what the server said."""
    assert user_repository.get_user_by_id(404) is None
    assert user_repository.get_user_by_email("nobody@example.com") is None


def test_users_insert_binds_the_type_value_but_the_role_object(fake_db):
    """The asymmetry is real and is what the driver receives today.

    Type is unwrapped to its string (.value) while Role is handed over as the enum member
    itself - PyMySQL stringifies it on the wire, so both land as strings in the column.
    Pinned because tidying this up would change the value a test can see.
    """
    user = Users(
        email="ada@example.com",
        password="secret",
        type=TypeUser.candidate,
        info_id=7,
        created_at=CREATED_AT,
        role=RoleEnum.admin,
    )
    assert user_repository.create_user(user) == 1
    assert fake_db.calls == [
        (
            "INSERT INTO users (email, password, type, info_id, created_at, role)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            ("ada@example.com", "secret", "candidate", 7, CREATED_AT, RoleEnum.admin),
        )
    ]


def test_users_insert_columns_are_model_aliases(fake_db):
    """The stored row must build a Users again: a renamed column breaks the read path.

    This is the cheap check that every column name in the INSERT is one the model knows as
    an alias - a typo such as info_id -> infoid inserts fine and fails on the way out.
    """
    user_repository.create_user(
        Users(
            email="ada@example.com",
            password="secret",
            type=TypeUser.candidate,
            created_at=CREATED_AT,
        )
    )
    stored = fake_db.rows("users")[0]
    assert Users(**stored).Email == "ada@example.com"


def test_users_update_builds_one_assignment_per_key_in_dict_order(fake_db):
    fake_db.tables["users"] = [{"id": 1, "email": "ada@example.com", "role": "user"}]

    user_repository.update_user(1, {"email": "new@example.com", "role": "admin"})

    assert fake_db.calls == [
        ("UPDATE users SET email=%s, role=%s WHERE id=%s", ("new@example.com", "admin", 1))
    ]
    assert fake_db.rows("users") == [{"id": 1, "email": "new@example.com", "role": "admin"}]


def test_users_delete_binds_the_id(fake_db):
    fake_db.tables["users"] = [
        {"id": 1, "email": "ada@example.com"},
        {"id": 2, "email": "b@example.com"},
    ]

    user_repository.delete_user(1)

    assert fake_db.calls == [("DELETE FROM users WHERE id=%s", (1,))]
    assert fake_db.rows("users") == [{"id": 2, "email": "b@example.com"}]


# --------------------------------------------------------------------------
# info_candidates  (the candidates slice serves the /info_candidates/ routes)
# --------------------------------------------------------------------------
def test_info_candidates_select_and_keyed_select(fake_db):
    fake_db.tables["info_candidates"] = [{"id": 1, "name": "Ada"}]

    assert info_candidate_repository.get_all_info_candidates() == [{"id": 1, "name": "Ada"}]
    assert info_candidate_repository.get_info_candidate_by_id(1) == {"id": 1, "name": "Ada"}
    assert info_candidate_repository.get_info_candidate_by_id(2) is None
    assert fake_db.calls[0] == ("SELECT * FROM info_candidates", ())
    assert fake_db.calls[1] == ("SELECT * FROM info_candidates WHERE id=%s", (1,))


def test_info_candidates_insert_order_differs_from_the_model_field_order(fake_db):
    """name, phone, gender, date_birth, file_id, embedding_id

    The model declares ID, Name, Phone, Gender, DateBirth, FileID, EmbeddingID, so today
    the two orders agree - and this assertion is what keeps them agreeing. A reorder on
    either side shows up here instead of as dates in the phone column.
    """
    candidate = InfoCandidates(
        name="Ada",
        phone="+100",
        gender=GenderUser.female,
        date_birth=datetime(1990, 5, 6),
        file_id=3,
        embedding_id=4,
    )
    assert info_candidate_repository.create_info_candidate(candidate) == 1
    assert fake_db.calls == [
        (
            "INSERT INTO info_candidates (name, phone, gender,"
            " date_birth, file_id, embedding_id)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            ("Ada", "+100", GenderUser.female, datetime(1990, 5, 6), 3, 4),
        )
    ]
    assert InfoCandidates(**fake_db.rows("info_candidates")[0]).Name == "Ada"


def test_info_candidates_update_and_delete(fake_db):
    fake_db.tables["info_candidates"] = [{"id": 1, "name": "Ada", "phone": "+100"}]

    info_candidate_repository.update_info_candidate(1, {"phone": "+200"})
    info_candidate_repository.delete_info_candidate(1)

    assert fake_db.calls == [
        ("UPDATE info_candidates SET phone=%s WHERE id=%s", ("+200", 1)),
        ("DELETE FROM info_candidates WHERE id=%s", (1,)),
    ]
    assert fake_db.rows("info_candidates") == []


# --------------------------------------------------------------------------
# vacancies
# --------------------------------------------------------------------------
def test_vacancies_select_and_keyed_select(fake_db):
    fake_db.tables["vacancies"] = [{"id": 1, "name": "Backend"}]

    assert vacancy_repository.get_all_vacancies() == [{"id": 1, "name": "Backend"}]
    assert vacancy_repository.get_vacancy_by_id(1) == {"id": 1, "name": "Backend"}
    assert vacancy_repository.get_vacancy_by_id(2) is None
    assert fake_db.calls[0] == ("SELECT * FROM vacancies", ())
    assert fake_db.calls[1] == ("SELECT * FROM vacancies WHERE id=%s", (1,))


def test_vacancies_insert_records_column_order_and_alias_round_trip(fake_db):
    vacancy = Vacancies(
        name="Backend", description="FastAPI", salary="100", skill="python", embedding_id=9
    )

    assert vacancy_repository.create_vacancy(vacancy) == 1
    assert fake_db.calls == [
        (
            "INSERT INTO vacancies (name, description, salary, skill, embedding_id)"
            " VALUES (%s, %s, %s, %s, %s)",
            ("Backend", "FastAPI", "100", "python", 9),
        )
    ]
    assert Vacancies(**fake_db.rows("vacancies")[0]).Description == "FastAPI"


def test_vacancies_update_and_delete(fake_db):
    fake_db.tables["vacancies"] = [{"id": 1, "name": "Backend", "salary": "100"}]

    vacancy_repository.update_vacancy(1, {"salary": "200", "skill": "sql"})
    vacancy_repository.delete_vacancy(1)

    assert fake_db.calls == [
        ("UPDATE vacancies SET salary=%s, skill=%s WHERE id=%s", ("200", "sql", 1)),
        ("DELETE FROM vacancies WHERE id=%s", (1,)),
    ]


# --------------------------------------------------------------------------
# magics
# --------------------------------------------------------------------------
def test_magics_select_and_keyed_select(fake_db):
    fake_db.tables["magics"] = [{"id": 1, "name": "Card"}]

    assert magic_repository.get_all_magics() == [{"id": 1, "name": "Card"}]
    assert magic_repository.get_magic_by_id(1) == {"id": 1, "name": "Card"}
    assert magic_repository.get_magic_by_id(2) is None
    assert fake_db.calls[0] == ("SELECT * FROM magics", ())


def test_magics_insert_records_column_order_and_alias_round_trip(fake_db):
    magic = Magics(
        name="Card",
        candidate_explanation="ce",
        vacancy_explanation="ve",
        candidate_answer="ca",
        vacancy_answer="va",
        file_id=5,
    )

    assert magic_repository.create_magic(magic) == 1
    assert fake_db.calls == [
        (
            "INSERT INTO magics (name, candidate_explanation, vacancy_explanation,"
            " candidate_answer, vacancy_answer, file_id)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            ("Card", "ce", "ve", "ca", "va", 5),
        )
    ]
    assert Magics(**fake_db.rows("magics")[0]).VacancyAnswer == "va"


def test_magics_update_and_delete(fake_db):
    fake_db.tables["magics"] = [{"id": 1, "name": "Card", "file_id": None}]

    magic_repository.update_magic(1, {"file_id": 8})
    magic_repository.delete_magic(1)

    assert fake_db.calls == [
        ("UPDATE magics SET file_id=%s WHERE id=%s", (8, 1)),
        ("DELETE FROM magics WHERE id=%s", (1,)),
    ]


# --------------------------------------------------------------------------
# personalities
# --------------------------------------------------------------------------
def test_personalities_select_and_keyed_select(fake_db):
    fake_db.tables["personalities"] = [{"id": 1, "name": "O"}]

    assert personality_repository.get_all_personalities() == [{"id": 1, "name": "O"}]
    assert personality_repository.get_personality_by_id(1) == {"id": 1, "name": "O"}
    assert personality_repository.get_personality_by_id(2) is None
    assert fake_db.calls[0] == ("SELECT * FROM personalities", ())


def test_personalities_insert_records_column_order_and_alias_round_trip(fake_db):
    created = personality_repository.create_personality(
        Personalities(name="O", description="Openness", embedding_id=2)
    )

    assert created == 1
    assert fake_db.calls == [
        (
            "INSERT INTO personalities (name, description, embedding_id) VALUES (%s, %s, %s)",
            ("O", "Openness", 2),
        )
    ]
    assert Personalities(**fake_db.rows("personalities")[0]).EmbeddingID == 2


def test_personalities_update_and_delete(fake_db):
    fake_db.tables["personalities"] = [{"id": 1, "name": "O", "description": "Openness"}]

    personality_repository.update_personality(1, {"description": "Trait"})
    personality_repository.delete_personality(1)

    assert fake_db.calls == [
        ("UPDATE personalities SET description=%s WHERE id=%s", ("Trait", 1)),
        ("DELETE FROM personalities WHERE id=%s", (1,)),
    ]


# --------------------------------------------------------------------------
# personality_vacancies, personality_candidates, candidate_vacancies
#
# The three link tables share a shape and an empty-payload guard. The guard used to raise
# fastapi.HTTPException(400); it now raises InvalidPayloadError (also 400) because a
# persistence module must not import the framework, and the detail strings are unchanged.
# The status and the words are asserted here because both are part of the client contract.
# --------------------------------------------------------------------------
def test_personality_vacancies_select_and_keyed_select(fake_db):
    fake_db.tables["personality_vacancies"] = [{"id": 1, "personality_id": 2, "vacancy_id": 3}]

    assert personality_vacancy_repository.get_all_personality_vacancies() == [
        {"id": 1, "personality_id": 2, "vacancy_id": 3}
    ]
    assert personality_vacancy_repository.get_personality_vacancy_by_id(1) == {
        "id": 1,
        "personality_id": 2,
        "vacancy_id": 3,
    }
    assert personality_vacancy_repository.get_personality_vacancy_by_id(2) is None
    assert fake_db.calls[0] == ("SELECT * FROM personality_vacancies", ())


def test_personality_vacancies_insert_binds_the_three_ids(fake_db):
    link = PersonalityVacancies(personality_id=2, vacancy_id=3, distance=Decimal("0.5"))

    assert personality_vacancy_repository.create_personality_vacancy(link) == 1
    assert fake_db.calls == [
        (
            "INSERT INTO personality_vacancies (personality_id, vacancy_id, distance) "
            "VALUES (%s, %s, %s)",
            (2, 3, Decimal("0.5")),
        )
    ]


def test_personality_vacancies_update_returns_its_own_message(fake_db):
    """Unlike its siblings this update reports success - the message comes from here."""
    fake_db.tables["personality_vacancies"] = [{"id": 1, "distance": Decimal("0.5")}]

    result = personality_vacancy_repository.update_personality_vacancy(
        1, {"distance": Decimal("0.25")}
    )

    assert result == {"message": "Personality vacancy updated successfully"}
    assert fake_db.calls == [
        ("UPDATE personality_vacancies SET distance=%s WHERE id=%s", (Decimal("0.25"), 1))
    ]


def test_personality_vacancies_update_rejects_an_empty_payload(fake_db):
    with pytest.raises(InvalidPayloadError) as raised:
        personality_vacancy_repository.update_personality_vacancy(1, {})

    assert raised.value.status_code == 400
    assert raised.value.detail == "Not get personality_vacancy_dict"
    assert fake_db.calls == []


def test_personality_vacancies_delete(fake_db):
    fake_db.tables["personality_vacancies"] = [{"id": 1}, {"id": 2}]

    personality_vacancy_repository.delete_personality_vacancy(1)

    assert fake_db.calls == [("DELETE FROM personality_vacancies WHERE id=%s", (1,))]
    assert fake_db.rows("personality_vacancies") == [{"id": 2}]


def test_personality_candidates_select_insert_update_delete(fake_db):
    fake_db.tables["personality_candidates"] = [{"id": 1, "personality_id": 2, "user_id": 3}]

    assert personality_candidate_repository.get_personality_candidate_by_id(1)["user_id"] == 3
    assert (
        personality_candidate_repository.create_personality_candidate(
            PersonalityCandidates(personality_id=2, user_id=3, distance=Decimal("0.75"))
        )
        == 2
    )
    assert personality_candidate_repository.update_personality_candidate(
        1, {"distance": Decimal("0.5")}
    ) == {"message": "Personality candidate updated successfully"}
    personality_candidate_repository.delete_personality_candidate(1)

    assert fake_db.sql[0] == "SELECT * FROM personality_candidates WHERE id=%s"
    assert fake_db.params[1] == (2, 3, Decimal("0.75"))
    assert fake_db.sql[2] == "UPDATE personality_candidates SET distance=%s WHERE id=%s"
    assert fake_db.sql[3] == "DELETE FROM personality_candidates WHERE id=%s"


def test_personality_candidates_update_rejects_an_empty_payload(fake_db):
    with pytest.raises(InvalidPayloadError) as raised:
        personality_candidate_repository.update_personality_candidate(1, {})

    assert raised.value.status_code == 400
    assert raised.value.detail == "Not get personality_candidate_dict"


def test_candidate_vacancies_select_insert_update_delete(fake_db):
    fake_db.tables["candidate_vacancies"] = [{"id": 1, "user_id": 2, "vacancy_id": 3}]

    assert candidate_vacancy_repository.get_all_candidate_vacancies() == [
        {"id": 1, "user_id": 2, "vacancy_id": 3}
    ]
    assert (
        candidate_vacancy_repository.create_candidate_vacancy(
            CandidateVacancies(user_id=2, vacancy_id=3, distance=Decimal("0.125"))
        )
        == 2
    )
    assert candidate_vacancy_repository.update_candidate_vacancy(1, {"distance": Decimal("1")}) == {
        "message": "Candidate vacancy updated successfully"
    }
    candidate_vacancy_repository.delete_candidate_vacancy(1)

    assert fake_db.sql[1] == (
        "INSERT INTO candidate_vacancies (user_id, vacancy_id, distance) VALUES (%s, %s, %s)"
    )
    assert fake_db.params[1] == (2, 3, Decimal("0.125"))
    assert fake_db.sql[3] == "DELETE FROM candidate_vacancies WHERE id=%s"


def test_candidate_vacancies_update_rejects_an_empty_payload(fake_db):
    """The empty payload is the only 400 these repositories can produce.

    The statement text is asserted too: the insert is built as a bare tuple expression
    (params = a, b, c without parentheses), which reads like a bug and is not one.
    """
    with pytest.raises(InvalidPayloadError) as raised:
        candidate_vacancy_repository.update_candidate_vacancy(1, {})

    assert raised.value.status_code == 400
    assert raised.value.detail == "Not get candidate_vacancy_dict"


# --------------------------------------------------------------------------
# files / embeddings
# --------------------------------------------------------------------------
def test_files_select_and_keyed_select(fake_db):
    fake_db.tables["files"] = [{"id": 1, "url": "data/files/a.mp4"}]

    assert file_repository.get_all_files() == [{"id": 1, "url": "data/files/a.mp4"}]
    assert file_repository.get_file_by_id(1) == {"id": 1, "url": "data/files/a.mp4"}
    assert file_repository.get_file_by_id(2) is None
    assert fake_db.calls[0] == ("SELECT * FROM files", ())


def test_files_insert_passes_a_bare_string_as_the_parameter_list(fake_db):
    """params = file.Url, not (file.Url,).

    It works: PyMySQL treats a scalar as a one-element parameter list, so the single %s
    is filled. The fake normalises the same way, so this asserts the EFFECTIVE binding
    (("data/files/a.mp4",)) and does not pretend the statement looks like the others.
    """
    assert file_repository.create_file(Files(url="data/files/a.mp4")) == 1
    assert fake_db.calls == [
        ("INSERT INTO files (url) VALUES (%s)", ("data/files/a.mp4",)),
    ]
    assert fake_db.rows("files") == [{"id": 1, "url": "data/files/a.mp4"}]


def test_files_update_requires_the_payload_to_be_dict_like(fake_db):
    """A model cannot be updated: the repository calls .keys() and .values() on it.

    Files is a pydantic model with no keys()/values(), so passing one raises
    AttributeError. The service hands over whatever the request body was, which for
    PUT /files/{file_id} is a dict - so this pins the dict path, and pins that the model
    path does NOT work, because that asymmetry is the latent defect recorded in
    docs/KNOWN_GAPS.md.
    """
    fake_db.tables["files"] = [{"id": 1, "url": "data/files/a.mp4"}]

    with pytest.raises(AttributeError):
        file_repository.update_file(1, Files(url="data/files/b.mp4"))

    file_repository.update_file(1, {"url": "data/files/b.mp4"})
    assert fake_db.calls == [("UPDATE files SET url=%s WHERE id=%s", ("data/files/b.mp4", 1))]
    assert fake_db.rows("files") == [{"id": 1, "url": "data/files/b.mp4"}]


def test_files_delete(fake_db):
    fake_db.tables["files"] = [{"id": 1}, {"id": 2}]

    file_repository.delete_file(1)

    assert fake_db.calls == [("DELETE FROM files WHERE id=%s", (1,))]
    assert fake_db.rows("files") == [{"id": 2}]


def test_embeddings_select_and_keyed_select(fake_db):
    fake_db.tables["embeddings"] = [{"id": 1, "url": "embeddings/users/a.npy"}]

    assert embedding_repository.get_all_embeddings() == [{"id": 1, "url": "embeddings/users/a.npy"}]
    assert embedding_repository.get_embedding_by_id(1) == {
        "id": 1,
        "url": "embeddings/users/a.npy",
    }
    assert embedding_repository.get_embedding_by_id(2) is None
    assert fake_db.calls[0] == ("SELECT * FROM embeddings", ())


def test_embeddings_insert_passes_a_bare_string_as_the_parameter_list(fake_db):
    assert embedding_repository.create_embedding(Embeddings(url="embeddings/users/a.npy")) == 1
    assert fake_db.calls == [
        ("INSERT INTO embeddings (url) VALUES (%s)", ("embeddings/users/a.npy",)),
    ]


def test_embeddings_update_and_delete(fake_db):
    fake_db.tables["embeddings"] = [{"id": 1, "url": "embeddings/users/a.npy"}]

    with pytest.raises(AttributeError):
        embedding_repository.update_embedding(1, Embeddings(url="embeddings/users/b.npy"))

    embedding_repository.update_embedding(1, {"url": "embeddings/users/b.npy"})
    embedding_repository.delete_embedding(1)

    assert fake_db.calls == [
        ("UPDATE embeddings SET url=%s WHERE id=%s", ("embeddings/users/b.npy", 1)),
        ("DELETE FROM embeddings WHERE id=%s", (1,)),
    ]


# --------------------------------------------------------------------------
# the fake itself, and the ten modules it must cover
# --------------------------------------------------------------------------
def test_fake_database_refuses_a_statement_it_does_not_model(fake_db):
    """If it answered anything, a repository could grow SQL nothing checks.

    The failure is an AssertionError naming the statement, not a silent empty list.
    """
    with pytest.raises(UnsupportedStatement) as raised:
        fake_db.execute_query("TRUNCATE TABLE users")

    assert "TRUNCATE TABLE users" in str(raised.value)


def test_every_persistence_module_is_exercised_by_this_file():
    """A new slice's persistence package must not be silently untested.

    The check is deliberately structural: every module under the ten persistence
    packages has to be named in this file. A slice added without a repository test fails
    here with the module name rather than leaving a hole nobody notices.
    """
    import pathlib

    import service.services as services_package

    root = pathlib.Path(services_package.__file__).parent
    repository_modules = sorted(
        path.relative_to(root).as_posix() for path in root.glob("*/persistence/*_repository.py")
    )
    assert repository_modules, "no persistence modules found - the tree moved again"

    this_file = pathlib.Path(__file__).read_text(encoding="utf-8")
    missing = [
        module
        for module in repository_modules
        if module.split("/")[-1].removesuffix(".py") not in this_file
    ]
    assert missing == [], "persistence modules with no test in this file: %s" % missing


def test_every_collection_and_statements_are_in_expected_order(fake_db):
    """A smoke check that the fake and the ten repositories agree on statement shapes.

    Every repository is walked once; if any of them issues a statement fake_db cannot
    parse, this fails with the SQL. It is the guard that turns "the fake drifted" into a
    named failing module instead of a confusing assertion in an unrelated test.
    """
    fake_db.tables.setdefault("users", [])
    seen = []
    for name, call in [
        ("get_all_users", lambda: user_repository.get_all_users()),
        ("get_all_info_candidates", lambda: info_candidate_repository.get_all_info_candidates()),
        ("get_all_vacancies", lambda: vacancy_repository.get_all_vacancies()),
        ("get_all_magics", lambda: magic_repository.get_all_magics()),
        ("get_all_personalities", lambda: personality_repository.get_all_personalities()),
        (
            "get_all_personality_vacancies",
            lambda: personality_vacancy_repository.get_all_personality_vacancies(),
        ),
        (
            "get_all_personality_candidates",
            lambda: personality_candidate_repository.get_all_personality_candidates(),
        ),
        (
            "get_all_candidate_vacancies",
            lambda: candidate_vacancy_repository.get_all_candidate_vacancies(),
        ),
        ("get_all_files", lambda: file_repository.get_all_files()),
        ("get_all_embeddings", lambda: embedding_repository.get_all_embeddings()),
    ]:
        call()
        seen.append(name)

    assert len(seen) == 10
    assert fake_db.sql == [
        "SELECT * FROM users",
        "SELECT * FROM info_candidates",
        "SELECT * FROM vacancies",
        "SELECT * FROM magics",
        "SELECT * FROM personalities",
        "SELECT * FROM personality_vacancies",
        "SELECT * FROM personality_candidates",
        "SELECT * FROM candidate_vacancies",
        "SELECT * FROM files",
        "SELECT * FROM embeddings",
    ]
