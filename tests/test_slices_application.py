"""The RULES of every slice, with the persistence swapped for a fake repository.

WHY A FAKE REPOSITORY AND NOT A FAKE DATABASE
---------------------------------------------
`tests/test_slices_repositories.py` owns the SQL and runs the real repository functions;
this file owns the decisions that sit above it. Every service takes its repository as a
constructor argument (`repository=None` means "the real module"), so passing an object
here exercises the same code path the composition container uses without a database,
without a socket and without monkeypatching a module global. A module-global patch would
also let a test that runs after this one inherit the fake, which is the class of bug this
shape cannot have.

WHY THE ASSERTIONS ARE ABOUT MESSAGES AND ORDER
-----------------------------------------------
The status code and the detail string are the API contract: the sign-in screen prints
"Invalid password" and the frontend keys off "Email already exist". The ORDER of the
checks is part of it too - a duplicate is rejected BEFORE the row is written, and a link
row validates both foreign keys BEFORE it is inserted - so those tests assert "the
repository was never called", not just "an exception was raised".

WHAT USED TO BE EXCLUDED AND IS NOT ANY MORE
-------------------------------------------
`matching.similarity_for_user` carried a preserved defect: it indexed `personalities[j]`
with the loop variable left over from the previous loop, and walked row zero of the
vacancy-vs-personality matrix (docs/KNOWN_GAPS.md section 26). The bottom of this file
states the correct expectation, and it is now a plain passing test - the `xfail(strict=True)`
marker had to go in the same commit as the fix, which is exactly what that marker was for.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from service.models import (
    Auth,
    CandidateVacancies,
    Embeddings,
    Files,
    InfoCandidates,
    Magics,
    Personalities,
    PersonalityCandidates,
    PersonalityVacancies,
    TokenInfo,
    Users,
    Vacancies,
)
from service.services.auth.application import auth_service as auth_module
from service.services.auth.application.auth_service import AuthService
from service.services.candidate_vacancies.application.candidate_vacancy_service import (
    CandidateVacancyService,
)
from service.services.candidates.application.info_candidate_service import InfoCandidateService
from service.services.embeddings.application.embedding_service import EmbeddingService
from service.services.files.application.file_service import FileService
from service.services.magics.application.magic_service import MagicService
from service.services.matching.application import matching_service as matching_module
from service.services.matching.application.matching_service import (
    MatchingService,
    build_matrix,
    filter_matrix,
)
from service.services.personalities.application.personality_service import PersonalityService
from service.services.personality_candidates.application.personality_candidate_service import (
    PersonalityCandidateService,
)
from service.services.personality_vacancies.application.personality_vacancy_service import (
    PersonalityVacancyService,
)
from service.services.users.application.user_service import UserService
from service.services.vacancies.application.vacancy_service import VacancyService
from service.shared.errors import ConflictError, NotFoundError, UnauthorizedError
from service.shared.hashing import hash_password, validate_password
from tests.fixtures.fake_db import FakeRepository, FakeService

# ---------------------------------------------------------------------------
# Descriptors for the six slices shaped "one table, one name, CRUD".
#
# WHY A TABLE OF LAMBDAS: the six differ only in names. Writing the eight behaviours out
# six times would be 48 copies of the same assertion, and the copy that drifts would be
# the one nobody reads. Everything slice-specific is data here.
# ---------------------------------------------------------------------------
SIMPLE_SLICES = [
    {
        "label": "candidates",
        "noun": "Info candidate",
        "attr": "Name",
        "duplicate_detail": "Name already exist",
        "value": "Ada",
        "model": InfoCandidates,
        "row": lambda row_id, value: {
            "id": row_id,
            "name": value,
            "phone": None,
            "gender": None,
            "date_birth": None,
            "file_id": None,
            "embedding_id": None,
        },
        "make": lambda value: InfoCandidates(name=value),
        "payload": lambda value: {"name": value},
        "service": lambda repository: InfoCandidateService(repository=repository),
        "list": "get_all_info_candidates",
        "get": "get_info_candidate_by_id",
        "create": "create_info_candidate",
        "update": "update_info_candidate",
        "delete": "delete_info_candidate",
    },
    {
        "label": "vacancies",
        "noun": "Vacancy",
        "attr": "Name",
        "duplicate_detail": "Name already exist",
        "value": "Backend",
        "model": Vacancies,
        "row": lambda row_id, value: {
            "id": row_id,
            "name": value,
            "description": "Build the API",
            "salary": None,
            "skill": None,
            "embedding_id": None,
        },
        "make": lambda value: Vacancies(name=value, description="Build the API"),
        "payload": lambda value: {"name": value},
        "service": lambda repository: VacancyService(repository=repository),
        "list": "get_all_vacancies",
        "get": "get_vacancy_by_id",
        "create": "create_vacancy",
        "update": "update_vacancy",
        "delete": "delete_vacancy",
    },
    {
        "label": "magics",
        "noun": "Magic",
        "attr": "Name",
        "duplicate_detail": "Name already exist",
        "value": "The Fool",
        "model": Magics,
        "row": lambda row_id, value: {
            "id": row_id,
            "name": value,
            "candidate_explanation": "new beginnings",
            "vacancy_explanation": "fresh ideas",
            "candidate_answer": "Yes",
            "vacancy_answer": "No",
            "file_id": None,
        },
        "make": lambda value: Magics(
            name=value,
            candidate_explanation="new beginnings",
            vacancy_explanation="fresh ideas",
            candidate_answer="Yes",
            vacancy_answer="No",
        ),
        "payload": lambda value: {"name": value},
        "service": lambda repository: MagicService(repository=repository),
        "list": "get_all_magics",
        "get": "get_magic_by_id",
        "create": "create_magic",
        "update": "update_magic",
        "delete": "delete_magic",
    },
    {
        "label": "personalities",
        "noun": "Personality",
        "attr": "Name",
        "duplicate_detail": "Name already exist",
        "value": "O",
        "model": Personalities,
        "row": lambda row_id, value: {
            "id": row_id,
            "name": value,
            "description": "Openness",
            "embedding_id": None,
        },
        "make": lambda value: Personalities(name=value, description="Openness"),
        "payload": lambda value: {"name": value},
        "service": lambda repository: PersonalityService(repository=repository),
        "list": "get_all_personalities",
        "get": "get_personality_by_id",
        "create": "create_personality",
        "update": "update_personality",
        "delete": "delete_personality",
    },
    {
        "label": "files",
        "noun": "File",
        "attr": "Url",
        "duplicate_detail": "File already exist",
        "value": "data/files/a.mp4",
        "model": Files,
        "row": lambda row_id, value: {"id": row_id, "url": value},
        # The update path of this slice takes a MODEL, not a dict - the asymmetry the
        # repository test records. The descriptor spells it out so the shared update
        # tests pass the shape the service actually accepts.
        "make": lambda value: Files(url=value),
        "payload": lambda value: Files(url=value),
        "service": lambda repository: FileService(repository=repository),
        "list": "get_all_files",
        "get": "get_file_by_id",
        "create": "create_file",
        "update": "update_file",
        "delete": "delete_file",
    },
    {
        "label": "embeddings",
        "noun": "Embedding",
        "attr": "Url",
        "duplicate_detail": "Embedding already exist",
        "value": "embeddings/users/a.npy",
        "model": Embeddings,
        "row": lambda row_id, value: {"id": row_id, "url": value},
        "make": lambda value: Embeddings(url=value),
        "payload": lambda value: Embeddings(url=value),
        "service": lambda repository: EmbeddingService(repository=repository),
        "list": "get_all_embeddings",
        "get": "get_embedding_by_id",
        "create": "create_embedding",
        "update": "update_embedding",
        "delete": "delete_embedding",
    },
]

SLICE_IDS = [descriptor["label"] for descriptor in SIMPLE_SLICES]


def _repo_for(descriptor, **responses):
    """A fake repository plus a service wired to it."""
    repository = FakeRepository(**responses)
    return descriptor["service"](repository), repository


# ---------------------------------------------------------------------------
# The six simple slices
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_list_returns_model_instances(descriptor):
    repository = FakeRepository(
        **{
            descriptor["list"]: [
                descriptor["row"](1, descriptor["value"]),
                descriptor["row"](2, descriptor["value"] + " two"),
            ]
        }
    )
    service = descriptor["service"](repository)

    rows = getattr(service, descriptor["list"])()

    assert [type(row) for row in rows] == [descriptor["model"], descriptor["model"]]
    assert [row.ID for row in rows] == [1, 2]


@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_missing_row_is_a_404_with_the_detail_the_client_prints(descriptor):
    service, _repository = _repo_for(descriptor, **{descriptor["get"]: None})

    with pytest.raises(NotFoundError) as failure:
        getattr(service, descriptor["get"])(99)

    assert failure.value.status_code == 404
    assert failure.value.detail == descriptor["noun"] + " not found"


@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_create_rejects_a_duplicate_before_writing(descriptor):
    service, repository = _repo_for(
        descriptor,
        **{
            descriptor["list"]: [descriptor["row"](9, descriptor["value"])],
            descriptor["create"]: 99,
        },
    )

    with pytest.raises(ConflictError) as failure:
        getattr(service, descriptor["create"])(descriptor["make"](descriptor["value"]))

    assert failure.value.status_code == 404  # a conflict renders as 404 by design
    assert failure.value.detail == descriptor["duplicate_detail"]
    assert repository.called(descriptor["create"]) == []


@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_create_returns_the_row_it_just_stored(descriptor):
    service, repository = _repo_for(
        descriptor,
        **{
            descriptor["list"]: [],
            descriptor["create"]: 7,
            descriptor["get"]: descriptor["row"](7, descriptor["value"]),
        },
    )

    created = getattr(service, descriptor["create"])(descriptor["make"](descriptor["value"]))

    assert isinstance(created, descriptor["model"])
    assert created.ID == 7
    assert repository.called(descriptor["get"]) == [(descriptor["get"], (7,), {})]


@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_update_accepts_a_row_keeping_its_own_value(descriptor):
    """Re-saving a record with its own name is not a conflict; the row is skipped by id."""
    payload = descriptor["payload"](descriptor["value"])
    service, repository = _repo_for(
        descriptor,
        **{
            descriptor["list"]: [
                descriptor["row"](1, "someone else"),
                descriptor["row"](3, descriptor["value"]),
            ],
            descriptor["update"]: None,
        },
    )

    message = getattr(service, descriptor["update"])(3, payload)

    assert message == {"message": descriptor["noun"] + " updated successfully"}
    assert repository.called(descriptor["update"]) == [(descriptor["update"], (3, payload), {})]


@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_update_rejects_a_value_held_by_another_row(descriptor):
    service, repository = _repo_for(
        descriptor,
        **{descriptor["list"]: [descriptor["row"](9, descriptor["value"])]},
    )

    with pytest.raises(ConflictError) as failure:
        getattr(service, descriptor["update"])(3, descriptor["payload"](descriptor["value"]))

    assert failure.value.detail == descriptor["duplicate_detail"]
    assert repository.called(descriptor["update"]) == []


@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_delete_reports_the_documented_message(descriptor):
    service, repository = _repo_for(
        descriptor,
        **{
            descriptor["get"]: descriptor["row"](1, descriptor["value"]),
            descriptor["delete"]: None,
        },
    )

    message = getattr(service, descriptor["delete"])(1)

    assert message == {"message": descriptor["noun"] + " deleted successfully"}
    assert repository.called(descriptor["delete"]) == [(descriptor["delete"], (1,), {})]


@pytest.mark.parametrize("descriptor", SIMPLE_SLICES, ids=SLICE_IDS)
def test_delete_of_a_missing_row_fails_before_the_delete(descriptor):
    service, repository = _repo_for(descriptor, **{descriptor["get"]: None})

    with pytest.raises(NotFoundError):
        getattr(service, descriptor["delete"])(404)

    assert repository.called(descriptor["delete"]) == []


# ---------------------------------------------------------------------------
# users: the same shape plus password hashing on both write paths and a second lookup
# ---------------------------------------------------------------------------
def _user_row(row_id=1, email="ada@example.com", password="secret"):
    return {
        "id": row_id,
        "email": email,
        "password": password,
        "type": "candidate",
        "info_id": None,
        "created_at": "2024-01-01T00:00:00",
        "role": "user",
    }


def test_user_lookup_by_email_returns_the_model():
    service = UserService(repository=FakeRepository(get_user_by_email=_user_row()))

    found = service.get_user_by_email("ada@example.com")

    assert isinstance(found, Users)
    assert found.Email == "ada@example.com"


def test_user_lookup_by_email_of_a_missing_address_is_a_404():
    service = UserService(repository=FakeRepository(get_user_by_email=None))

    with pytest.raises(NotFoundError) as failure:
        service.get_user_by_email("nobody@example.com")

    assert failure.value.detail == "User not found"


def test_create_user_hashes_the_password_and_never_stores_it():
    service = UserService(
        repository=FakeRepository(
            get_all_users=[],
            create_user=5,
            get_user_by_id=_user_row(5, password="hashed-by-the-database"),
        )
    )

    service.create_user(Users(email="ada@example.com", password="secret", type="candidate"))

    stored = service._repository.called("create_user")[0][1][0]
    assert stored.Password != "secret"
    assert validate_password("secret", stored.Password)


def test_create_user_hashes_only_after_the_duplicate_check():
    service = UserService(repository=FakeRepository(get_all_users=[_user_row(1)], create_user=2))

    with pytest.raises(ConflictError) as failure:
        service.create_user(Users(email="ada@example.com", password="secret", type="candidate"))

    assert failure.value.detail == "Email already exist"
    assert service._repository.called("create_user") == []


def test_update_user_hashes_a_password_that_is_present():
    service = UserService(repository=FakeRepository(get_all_users=[_user_row(1)], update_user=None))

    message = service.update_user(1, {"email": "ada@example.com", "password": "secret"})

    assert message == {"message": "User updated successfully"}
    payload = service._repository.called("update_user")[0][1][1]
    assert payload["password"] != "secret"
    assert validate_password("secret", payload["password"])


def test_update_user_leaves_the_stored_hash_alone_when_no_password_is_sent():
    """The frontend omits `password` when the user did not change it; re-hashing the absent
    value would replace it with a hash of the empty string and lock the account out."""
    service = UserService(repository=FakeRepository(get_all_users=[_user_row(1)], update_user=None))

    service.update_user(1, {"email": "new@example.com"})

    payload = service._repository.called("update_user")[0][1][1]
    assert "password" not in payload
    assert payload["email"] == "new@example.com"


def test_update_user_rejects_an_email_held_by_another_row():
    service = UserService(
        repository=FakeRepository(
            get_all_users=[_user_row(9, email="taken@example.com")], update_user=None
        )
    )

    with pytest.raises(ConflictError) as failure:
        service.update_user(1, {"email": "taken@example.com"})

    assert failure.value.detail == "Email already exist"
    assert service._repository.called("update_user") == []


def test_delete_user_returns_the_message_and_deletes():
    service = UserService(repository=FakeRepository(get_user_by_id=_user_row(1), delete_user=None))

    assert service.delete_user(1) == {"message": "User deleted successfully"}
    assert service._repository.called("delete_user") == [("delete_user", (1,), {})]


def test_delete_user_of_a_missing_row_is_a_404():
    service = UserService(repository=FakeRepository(get_user_by_id=None, delete_user=None))

    with pytest.raises(NotFoundError):
        service.delete_user(1)

    assert service._repository.called("delete_user") == []


# ---------------------------------------------------------------------------
# The three link slices: both foreign keys are validated BEFORE the write
# ---------------------------------------------------------------------------
LINK_SLICES = [
    {
        "label": "personality_vacancies",
        "noun": "Personality vacancy",
        "first": "personality",
        "first_detail": "Personality not found",
        "second": "vacancy",
        "second_detail": "Vacancy not found",
        "create_row": lambda: PersonalityVacancies(personality_id=1, vacancy_id=2, distance=0.5),
        "update_payload": {"distance": 0.25},
        "row": {"id": 4, "personality_id": 1, "vacancy_id": 2, "distance": 0.5},
        "service": lambda repository, first, second: PersonalityVacancyService(
            repository=repository, personality=first, vacancy=second
        ),
        "methods": (
            "get_all_personality_vacancies",
            "get_personality_vacancy_by_id",
            "create_personality_vacancy",
            "update_personality_vacancy",
            "delete_personality_vacancy",
        ),
    },
    {
        "label": "personality_candidates",
        "noun": "Personality candidate",
        "first": "personality",
        "first_detail": "Personality not found",
        "second": "user",
        "second_detail": "User not found",
        "create_row": lambda: PersonalityCandidates(personality_id=1, user_id=2, distance=0.5),
        "update_payload": {"distance": 0.25},
        "row": {"id": 4, "personality_id": 1, "user_id": 2, "distance": 0.5},
        "service": lambda repository, first, second: PersonalityCandidateService(
            repository=repository, personality=first, user=second
        ),
        "methods": (
            "get_all_personality_candidates",
            "get_personality_candidate_by_id",
            "create_personality_candidate",
            "update_personality_candidate",
            "delete_personality_candidate",
        ),
    },
    {
        "label": "candidate_vacancies",
        "noun": "Candidate vacancy",
        "first": "user",
        "first_detail": "User not found",
        "second": "vacancy",
        "second_detail": "Vacancy not found",
        "create_row": lambda: CandidateVacancies(user_id=1, vacancy_id=2, distance=0.125),
        "update_payload": {"distance": 0.25},
        "row": {"id": 4, "user_id": 1, "vacancy_id": 2, "distance": 0.125},
        "service": lambda repository, first, second: CandidateVacancyService(
            repository=repository, user=first, vacancy=second
        ),
        "methods": (
            "get_all_candidate_vacancies",
            "get_candidate_vacancy_by_id",
            "create_candidate_vacancy",
            "update_candidate_vacancy",
            "delete_candidate_vacancy",
        ),
    },
]

LINK_IDS = [descriptor["label"] for descriptor in LINK_SLICES]


def _neighbour(descriptor, side, behaviour):
    """A neighbouring slice, stubbed at its key lookup."""
    return FakeService(**{"get_%s_by_id" % descriptor[side]: behaviour})


@pytest.mark.parametrize("descriptor", LINK_SLICES, ids=LINK_IDS)
def test_link_create_validates_the_first_key_then_the_second_then_writes(descriptor):
    order = []
    methods = descriptor["methods"]
    repository = FakeRepository(
        **{
            methods[1]: descriptor["row"],
            methods[2]: lambda entity: order.append("write") or 4,
        }
    )
    service = descriptor["service"](
        repository,
        _neighbour(descriptor, "first", lambda key: order.append(("first", key))),
        _neighbour(descriptor, "second", lambda key: order.append(("second", key))),
    )

    created = getattr(service, methods[2])(descriptor["create_row"]())

    assert order == [("first", 1), ("second", 2), "write"]
    assert created.ID == 4


@pytest.mark.parametrize("descriptor", LINK_SLICES, ids=LINK_IDS)
def test_link_create_writes_nothing_when_the_first_key_is_missing(descriptor):
    methods = descriptor["methods"]
    repository = FakeRepository(**{methods[2]: 4})
    service = descriptor["service"](
        repository,
        _neighbour(descriptor, "first", NotFoundError(descriptor["first_detail"])),
        FakeService(),
    )

    with pytest.raises(NotFoundError) as failure:
        getattr(service, methods[2])(descriptor["create_row"]())

    assert failure.value.detail == descriptor["first_detail"]
    assert repository.called(methods[2]) == []


@pytest.mark.parametrize("descriptor", LINK_SLICES, ids=LINK_IDS)
def test_link_create_writes_nothing_when_the_second_key_is_missing(descriptor):
    methods = descriptor["methods"]
    repository = FakeRepository(**{methods[2]: 4})
    service = descriptor["service"](
        repository,
        _neighbour(descriptor, "first", None),
        _neighbour(descriptor, "second", NotFoundError(descriptor["second_detail"])),
    )

    with pytest.raises(NotFoundError) as failure:
        getattr(service, methods[2])(descriptor["create_row"]())

    assert failure.value.detail == descriptor["second_detail"]
    assert repository.called(methods[2]) == []


@pytest.mark.parametrize("descriptor", LINK_SLICES, ids=LINK_IDS)
def test_link_update_validates_only_the_keys_the_payload_carries(descriptor):
    """A partial update that touches no foreign key must not re-check either one."""
    methods = descriptor["methods"]
    first = FakeService()
    second = FakeService()
    repository = FakeRepository(**{methods[1]: descriptor["row"], methods[3]: None})
    service = descriptor["service"](repository, first, second)

    message = getattr(service, methods[3])(4, dict(descriptor["update_payload"]))

    assert message == {"message": descriptor["noun"] + " updated successfully"}
    assert first.calls == [] and second.calls == []


@pytest.mark.parametrize("descriptor", LINK_SLICES, ids=LINK_IDS)
def test_link_update_of_a_missing_row_is_a_404(descriptor):
    methods = descriptor["methods"]
    repository = FakeRepository(**{methods[1]: None, methods[3]: None})
    service = descriptor["service"](repository, FakeService(), FakeService())

    with pytest.raises(NotFoundError) as failure:
        getattr(service, methods[3])(404, dict(descriptor["update_payload"]))

    assert failure.value.detail == descriptor["noun"] + " not found"
    assert repository.called(methods[3]) == []


@pytest.mark.parametrize("descriptor", LINK_SLICES, ids=LINK_IDS)
def test_link_delete_reports_the_documented_message(descriptor):
    methods = descriptor["methods"]
    repository = FakeRepository(**{methods[1]: descriptor["row"], methods[4]: None})
    service = descriptor["service"](repository, FakeService(), FakeService())

    message = getattr(service, methods[4])(4)

    assert message == {"message": descriptor["noun"] + " deleted successfully"}
    assert repository.called(methods[4]) == [(methods[4], (4,), {})]


# ---------------------------------------------------------------------------
# auth: the four 401s, the swallowed duplicate, and the token shape
# ---------------------------------------------------------------------------
def _stored_user(password="secret"):
    return Users(**dict(_user_row(password=hash_password(password).decode("utf-8"))))


def test_sign_in_returns_the_user_when_the_password_matches():
    user = _stored_user()
    service = AuthService(user=FakeRepository(get_user_by_email=user))

    assert service.validate_auth_user(Auth(email="ada@example.com", password="secret")) is user


def test_sign_in_with_a_wrong_password_is_a_401():
    user = _stored_user()
    service = AuthService(user=FakeRepository(get_user_by_email=user))

    with pytest.raises(UnauthorizedError) as failure:
        service.validate_auth_user(Auth(email="ada@example.com", password="wrong"))

    assert failure.value.detail == "Invalid password"
    assert failure.value.status_code == 401


def test_sign_in_for_an_unknown_address_surfaces_the_404_from_the_users_slice():
    service = AuthService(user=FakeRepository(get_user_by_email=NotFoundError("User not found")))

    with pytest.raises(NotFoundError):
        service.validate_auth_user(Auth(email="nobody@example.com", password="secret"))


def test_registration_without_a_type_is_a_401():
    """The model requires Type, so only a hand-built payload can reach this rule."""
    service = AuthService(user=FakeRepository())

    with pytest.raises(UnauthorizedError) as failure:
        service.validate_reg_user(types.SimpleNamespace(Type=None, Email="ada@example.com"))

    assert failure.value.detail == "Invalid type user"


def test_registration_swallows_the_duplicate_lookup_failure_on_purpose():
    """Documented behaviour: the duplicate is left to the INSERT so the error the client
    sees stays the same as it has always been (see the module docstring)."""
    service = AuthService(user=FakeRepository(get_user_by_email=UnauthorizedError("boom")))
    candidate = Users(email="ada@example.com", password="secret", type="candidate")

    assert service.validate_reg_user(candidate) is candidate


def test_sign_in_builds_a_token_pair(monkeypatch):
    user = _stored_user()
    service = AuthService(user=FakeRepository(get_user_by_email=user))
    monkeypatch.setattr(auth_module, "create_access_token", lambda current: "access-for-1")
    monkeypatch.setattr(auth_module, "create_refresh_token", lambda current: "refresh-for-1")

    tokens = service.signin(Auth(email="ada@example.com", password="secret"))

    assert isinstance(tokens, TokenInfo)
    assert tokens.AccessToken == "access-for-1"
    assert tokens.RefreshToken == "refresh-for-1"
    assert tokens.Type == "Bearer"


def test_sign_up_creates_the_user_then_builds_a_token_pair(monkeypatch):
    created = Users(**dict(_user_row(3, password="hashed")))
    repository = FakeRepository(
        get_user_by_email=NotFoundError("User not found"), create_user=created
    )
    service = AuthService(user=repository)
    monkeypatch.setattr(auth_module, "create_access_token", lambda current: "access-for-3")
    monkeypatch.setattr(auth_module, "create_refresh_token", lambda current: "refresh-for-3")

    tokens = service.signup(Users(email="ada@example.com", password="secret", type="candidate"))

    assert tokens.AccessToken == "access-for-3"
    assert repository.called("create_user") != []


def test_payload_without_a_usable_user_is_a_401():
    service = AuthService(user=FakeRepository(get_user_by_id=None))

    with pytest.raises(UnauthorizedError) as failure:
        service.get_user_by_payload({"user_id": 7})

    assert failure.value.detail == "User not found"


def test_an_undecodable_token_is_a_401(monkeypatch):
    service = AuthService(user=FakeRepository())
    monkeypatch.setattr(auth_module, "decode_jwt", lambda token: {})

    with pytest.raises(UnauthorizedError) as failure:
        service.get_current_token_payload("garbage")

    assert failure.value.detail == "Invalid token error"


def test_token_of_the_wrong_type_is_rejected_by_the_shared_helper(monkeypatch):
    """The shared codec raises `UnauthorizedError`, not `fastapi.HTTPException`.

    WHAT CHANGED AND WHY THE ASSERTIONS DID: `service/shared/tokens.py` is a codec, and
    importing fastapi there made it unusable from a celery worker or a CLI
    (docs/KNOWN_GAPS.md section 36). The status a client sees is unchanged - 401 through
    the single `ServiceError` handler on the /server sub-app - so this test asserts the
    error TYPE the codec owns and the exact detail string, which is the part a client can
    read.
    """
    service = AuthService(user=FakeRepository())
    monkeypatch.setattr(
        auth_module, "decode_jwt", lambda token: {"TOKEN_TYPE_FIELD": "refresh_token_type"}
    )

    with pytest.raises(UnauthorizedError) as failure:
        service.user_from_token("token", "access_token_type")

    assert failure.value.status_code == 401
    assert failure.value.detail == (
        "Invalid token type 'access_token_type' excepted 'refresh_token_type'"
    )


def test_a_valid_token_resolves_to_its_user(monkeypatch):
    user = _stored_user()
    service = AuthService(user=FakeRepository(get_user_by_id=user))
    monkeypatch.setattr(
        auth_module,
        "decode_jwt",
        lambda token: {"TOKEN_TYPE_FIELD": "access_token_type", "user_id": 1},
    )

    assert service.user_from_token("token", "access_token_type") is user


def test_refresh_returns_an_access_token_only(monkeypatch):
    service = AuthService(user=FakeRepository())
    monkeypatch.setattr(auth_module, "create_access_token", lambda current: "fresh-access")

    tokens = service.auth_refresh_jwt(_stored_user())

    assert tokens.AccessToken == "fresh-access"
    assert tokens.RefreshToken is None


# ---------------------------------------------------------------------------
# matching: the arithmetic, tested as arithmetic
# ---------------------------------------------------------------------------
def test_build_matrix_returns_the_documented_combination():
    """One candidate and one vacancy, both identical to the first OCEAN axis.

    C = [[0, 1]] and P = [[0, 1]], so C @ P.T = [[1]] and (1 - C) @ (1 - P.T) = [[1]]:
    the score is 2.0, which is exactly why filter_matrix normalises afterwards.
    """
    candidates = np.array([[1.0, 0.0]])
    ocean = np.array([[1.0, 0.0], [0.0, 1.0]])
    vacancies = np.array([[1.0, 0.0]])

    final_matrix, candidate_distances, vacancy_distances = build_matrix(
        candidates, ocean, vacancies
    )

    assert candidate_distances.tolist() == [[0.0, 1.0]]
    assert vacancy_distances.tolist() == [[0.0, 1.0]]
    assert final_matrix.tolist() == [[2.0]]


def test_build_matrix_shape_follows_the_inputs():
    rng = np.random.default_rng(7)
    candidates = rng.random((3, 5))
    ocean = rng.random((5, 5))
    vacancies = rng.random((4, 5))

    final_matrix, candidate_distances, vacancy_distances = build_matrix(
        candidates, ocean, vacancies
    )

    assert candidate_distances.shape == (3, 5)
    assert vacancy_distances.shape == (4, 5)
    assert final_matrix.shape == (3, 4)


def test_filter_matrix_normalises_each_row_to_zero_and_one():
    """The normalisation itself, with the threshold taken out of the way: the default 0.7
    would drop the middle value, so the row is checked at threshold 0."""
    assert filter_matrix(np.array([[0.0, 1.0, 2.0]]), threshold=0.0).tolist() == [[0.0, 0.5, 1.0]]


def test_filter_matrix_drops_the_whole_row_when_every_score_is_equal():
    """The single-vacancy case: min == max, the denominator becomes 1 and the row is 0, so
    a catalogue with one vacancy recommends nothing. Preserved on purpose."""
    assert filter_matrix(np.array([[2.0]])).tolist() == [[0.0]]


def test_filter_matrix_keeps_a_score_exactly_at_the_threshold():
    """The comparison is "below threshold", so the boundary belongs to the kept side;
    moving it to "at or below" would silently drop every exact match."""
    final_matrix = np.array([[0.0, 1.0]])

    assert filter_matrix(final_matrix, threshold=1.0).tolist() == [[0.0, 1.0]]
    assert filter_matrix(final_matrix, threshold=1.000001).tolist() == [[0.0, 0.0]]


def test_filter_matrix_does_not_mutate_its_argument():
    final_matrix = np.array([[0.0, 1.0]])

    filter_matrix(final_matrix)

    assert final_matrix.tolist() == [[0.0, 1.0]]


def test_distance_is_quantised_and_never_reaches_one():
    """`distance` is decimal(6,6) in the schema, so 1.0 does not fit; the clamp is what
    keeps a perfect cosine match insertable."""
    assert matching_module._distance(0.5) == matching_module.Decimal("0.500000")
    assert matching_module._distance(0.1234565) == matching_module.Decimal("0.123457")
    assert matching_module._distance(1.0) == matching_module.Decimal("0.999999")


def test_similarity_stores_the_personality_the_score_belongs_to(tmp_path):
    """Each stored row names the axis its own score was computed against.

    THIS WAS A STRICT XFAIL UNTIL THE DEFECT WAS CLOSED (docs/KNOWN_GAPS.md section 26).
    The old loop walked ROW ZERO of the vacancy-vs-personality matrix - the first
    vacancy's scores against all five axes - zipped with the vacancy list, which
    truncated it to the number of vacancies, and stored it under `personalities[j]`
    with `j` left over from the candidate loop. The row's personality was therefore
    decided by where an earlier loop happened to stop: for the fixture below it was id 5
    while the score described id 1.

    The vacancy is orthogonal to O and to E, so BOTH of those axes legitimately clear
    the threshold and both rows are expected. Neither of them is 5, which is the value
    that used to be written.

    Every vector is loaded from disk because that is what the real code does: the
    paths come back from the embeddings slice and are handed straight to numpy.
    """
    axes = {
        1: np.array([1.0, 0.0, 0.0]),
        2: np.array([0.0, 1.0, 0.0]),
        3: np.array([0.0, 0.0, 1.0]),
        4: np.array([1.0, 1.0, 0.0]),
        5: np.array([0.0, 1.0, 1.0]),
        6: np.array([1.0, 0.0, 0.0]),  # the candidate
        99: np.array([0.0, 1.0, 0.0]),  # the vacancy: orthogonal to O, so it scores high
    }
    paths = {}
    for embedding_id, vector in axes.items():
        path = tmp_path / ("embedding-%d.npy" % embedding_id)
        np.save(path, vector)
        paths[embedding_id] = str(path)

    personalities = [
        types.SimpleNamespace(ID=index + 1, Name=name, EmbeddingID=index + 1)
        for index, name in enumerate(("O", "C", "E", "A", "N"))
    ]
    written = []
    service = MatchingService(
        personalities=FakeService(get_all_personalities=lambda: personalities),
        embeddings=FakeService(
            get_embedding_by_id=lambda embedding_id: types.SimpleNamespace(Url=paths[embedding_id])
        ),
        users=FakeService(get_user_by_id=lambda user_id: types.SimpleNamespace(ID=1, InfoID=1)),
        info_candidates=FakeService(
            get_info_candidate_by_id=lambda info_id: types.SimpleNamespace(EmbeddingID=6)
        ),
        vacancies=FakeService(
            get_all_vacancies=lambda: [types.SimpleNamespace(ID=1, EmbeddingID=99)]
        ),
        personality_candidates=FakeService(create_personality_candidate=lambda row: None),
        personality_vacancies=FakeService(
            create_personality_vacancy=lambda row: written.append(row)
        ),
        candidate_vacancies=FakeService(create_candidate_vacancy=lambda row: None),
    )

    service.similarity_for_user(1)

    # O (column 0) and E (column 2) are both at distance 1.0 from the vacancy; C, A and N
    # are at 0.0, 0.2929 and 0.2929 and stay under the threshold. personalities[j].ID == j+1
    # in this fixture, so the expected ids are 1 and 3.
    assert sorted(row.PersonalityID for row in written) == [1, 3]
    # And every stored pair is a (vacancy, axis) coordinate that exists.
    assert {row.VacancyID for row in written} == {1}
