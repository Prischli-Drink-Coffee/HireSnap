"""The seams that hold the slices together: lazy resolution, shared helpers.

WHY THIS FILE EXISTS SEPARATELY FROM test_slices_application.py: that file drives each
slice with collaborators PASSED IN, which is how a router works in a test. The
production path is the other one - the collaborator is resolved on first use through
`service.composition.state` - and it was the least covered code in the tree because
passing a fake in is easier than proving the factory is reached.

Also here: the small shared helpers whose branches are one line each but whose
behaviour is a contract (a password hash read from the database arrives as a str, a
bad JWT expiry value must fall back instead of raising at import).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from service.composition import state as state_module
from service.models import CandidateVacancies, PersonalityCandidates, PersonalityVacancies
from service.services.candidate_vacancies.application.candidate_vacancy_service import (
    CandidateVacancyService,
)
from service.services.personality_candidates.application.personality_candidate_service import (
    PersonalityCandidateService,
)
from service.services.personality_vacancies.application.personality_vacancy_service import (
    PersonalityVacancyService,
)


class _Neighbour:
    """A slice service that records the lookups made against it."""

    def __init__(self, name):
        self.name = name
        self.seen = []

    def get_personality_by_id(self, key):
        self.seen.append(("personality", key))
        return {"id": key}

    def get_vacancy_by_id(self, key):
        self.seen.append(("vacancy", key))
        return {"id": key}

    def get_user_by_id(self, key):
        self.seen.append(("user", key))
        return {"id": key}


class _Repository:
    """Only the statements these tests reach; anything else is a test bug."""

    def __init__(self, **overrides):
        self._overrides = overrides
        self.writes = []

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        raise AssertionError("unexpected repository call: " + name)


LINK_CASES = [
    {
        "label": "personality_vacancies",
        "singular": "personality_vacancy",
        "service": PersonalityVacancyService,
        "fields": {
            "get_personality_service": "personality_getter",
            "get_vacancy_service": "vacancy_getter",
        },
        "first": "personality",
        "second": "vacancy",
        "row": lambda: PersonalityVacancies(personality_id=1, vacancy_id=2, distance=0.5),
        "stored": {"id": 9, "personality_id": 1, "vacancy_id": 2, "distance": 0.5},
        "create_method": "create_personality_vacancy",
    },
    {
        "label": "personality_candidates",
        "singular": "personality_candidate",
        "service": PersonalityCandidateService,
        "fields": {
            "get_personality_service": "personality_getter",
            "get_user_service": "user_getter",
        },
        "first": "personality",
        "second": "user",
        "row": lambda: PersonalityCandidates(personality_id=1, user_id=2, distance=0.5),
        "stored": {"id": 9, "personality_id": 1, "user_id": 2, "distance": 0.5},
        "create_method": "create_personality_candidate",
    },
    {
        "label": "candidate_vacancies",
        "singular": "candidate_vacancy",
        "service": CandidateVacancyService,
        "fields": {"get_user_service": "user_getter", "get_vacancy_service": "vacancy_getter"},
        "first": "user",
        "second": "vacancy",
        "row": lambda: CandidateVacancies(user_id=1, vacancy_id=2, distance=0.125),
        "stored": {"id": 9, "user_id": 1, "vacancy_id": 2, "distance": 0.125},
        "create_method": "create_candidate_vacancy",
    },
]

LINK_IDS = [case["label"] for case in LINK_CASES]


def built_by_getter(case):
    """Map each state-module getter name to the neighbour stub it must return.

    `fields` is keyed by the getter (`get_personality_service`) because that is
    what monkeypatch has to replace; its values are the constructor attribute names
    the service stores the result on (`personality_getter`).
    """
    stubs = [_Neighbour(name) for name in case["fields"].values()]
    return dict(zip(case["fields"], stubs))


@pytest.mark.parametrize("case", LINK_CASES, ids=LINK_IDS)
def test_collaborators_are_resolved_through_the_composition_layer(case, monkeypatch):
    """No overrides passed: the factory is the only way the service can work.

    This is the production path, and it is the one that breaks silently when a slice
    is renamed: `get_<slice>_service` would raise AttributeError inside a request
    instead of at import, so no test that passes fakes in would notice.
    """
    built = built_by_getter(case)
    for getter, neighbour in built.items():
        monkeypatch.setattr(state_module, getter, lambda n=neighbour: n)
    # The repository method is the service method without the "Service." wrapper, so
    # it carries the SINGULAR name ("create_candidate_vacancy") while the case label is
    # the plural slice name ("candidate_vacancies").
    repository = _Repository(
        **{
            case["create_method"]: lambda entity: 9,
            "get_" + case["singular"] + "_by_id": lambda key, row=case["stored"]: dict(row),
        }
    )

    service = case["service"](repository=repository)
    created = getattr(service, case["create_method"])(case["row"]())

    assert created.ID == 9
    seen = [entry for neighbour in built.values() for entry in neighbour.seen]
    assert sorted(seen) == sorted([(case["first"], 1), (case["second"], 2)])


@pytest.mark.parametrize("case", LINK_CASES, ids=LINK_IDS)
def test_a_resolved_collaborator_is_memoised(case):
    """Resolving twice must not ask the composition layer twice."""
    service = case["service"](repository=_Repository())
    attribute = next(iter(case["fields"].values()))

    first = getattr(service, "_" + attribute.replace("_getter", ""))
    second = getattr(service, "_" + attribute.replace("_getter", ""))

    assert first is second


# ---------------------------------------------------------------------------
# The three link slices: the paths that only differ by which foreign key moves
# ---------------------------------------------------------------------------

UPDATE_METHODS = {
    "personality_vacancies": ("update_personality_vacancy", {"personality_id": 4, "vacancy_id": 5}),
    "personality_candidates": ("update_personality_candidate", {"personality_id": 4, "user_id": 5}),
    "candidate_vacancies": ("update_candidate_vacancy", {"user_id": 4, "vacancy_id": 5}),
}

DELETE_METHODS = {
    "personality_vacancies": "delete_personality_vacancy",
    "personality_candidates": "delete_personality_candidate",
    "candidate_vacancies": "delete_candidate_vacancy",
}

GET_ALL_METHODS = {
    "personality_vacancies": "get_all_personality_vacancies",
    "personality_candidates": "get_all_personality_candidates",
    "candidate_vacancies": "get_all_candidate_vacancies",
}


@pytest.mark.parametrize("case", LINK_CASES, ids=LINK_IDS)
def test_the_listing_turns_every_row_into_a_model(case):
    """A row dict from PyMySQL has snake_case keys; the model reads them as aliases."""
    label = case["label"]
    row = dict(case["stored"])
    repository = _Repository(**{"get_all_" + label: lambda: [row, dict(row, id=10)]})

    rows = getattr(case["service"](repository=repository), GET_ALL_METHODS[label])()

    assert [item.ID for item in rows] == [9, 10]


@pytest.mark.parametrize("case", LINK_CASES, ids=LINK_IDS)
def test_an_update_re_validates_both_foreign_keys_when_the_payload_carries_them(case, monkeypatch):
    """The row must exist first; then each moved key is checked before the write."""
    label = case["label"]
    method, moved = UPDATE_METHODS[label]
    built = built_by_getter(case)
    for getter, neighbour in built.items():
        monkeypatch.setattr(state_module, getter, lambda n=neighbour: n)
    updated = []
    repository = _Repository(
        **{
            "get_" + case["singular"] + "_by_id": lambda key: dict(case["stored"]),
            "update_" + case["singular"]: lambda key, payload: updated.append((key, payload)),
        }
    )

    answer = getattr(case["service"](repository=repository), method)(9, moved)

    assert answer["message"].endswith("updated successfully")
    assert updated == [(9, moved)]
    seen = [entry for neighbour in built.values() for entry in neighbour.seen]
    assert sorted(seen) == [(case["first"], 4), (case["second"], 5)]


@pytest.mark.parametrize("case", LINK_CASES, ids=LINK_IDS)
def test_a_delete_checks_the_row_exists_and_then_removes_it(case):
    label = case["label"]
    deleted = []
    repository = _Repository(
        **{
            "get_" + case["singular"] + "_by_id": lambda key: dict(case["stored"]),
            "delete_" + case["singular"]: lambda key: deleted.append(key),
        }
    )

    answer = getattr(case["service"](repository=repository), DELETE_METHODS[label])(9)

    assert answer["message"].endswith("deleted successfully")
    assert deleted == [9]


@pytest.mark.parametrize("case", LINK_CASES, ids=LINK_IDS)
def test_deleting_a_row_that_is_not_there_is_a_404(case):
    from service.shared.errors import NotFoundError

    label = case["label"]
    repository = _Repository(**{"get_" + case["singular"] + "_by_id": lambda key: None})

    with pytest.raises(NotFoundError) as failure:
        getattr(case["service"](repository=repository), DELETE_METHODS[label])(9)

    assert failure.value.status_code == 404


# ---------------------------------------------------------------------------
# service/shared/hashing.py
# ---------------------------------------------------------------------------


def test_a_stored_hash_is_accepted_as_text_or_as_bytes():
    """The database hands back a str; bcrypt wants bytes.

    Both spellings must work: a row read through PyMySQL is a str, and every caller
    inside a test or a migration may already hold bytes.
    """
    from service.shared.hashing import hash_password, validate_password

    digest = hash_password("correct horse")
    assert validate_password("correct horse", digest.decode("utf-8")) is True
    assert validate_password("correct horse", digest) is True
    assert validate_password("wrong horse", digest) is False


# ---------------------------------------------------------------------------
# service/models/auth_models.py: the two environment readers
# ---------------------------------------------------------------------------


def test_the_key_path_prefers_the_explicit_variable(monkeypatch, tmp_path):
    from service.models import auth_models

    explicit = tmp_path / "pair" / "private.pem"
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "  %s  " % explicit)

    assert auth_models._key_path("JWT_PRIVATE_KEY_PATH", "jwt-private.pem") == explicit


def test_the_key_path_falls_back_to_the_packaged_pair(monkeypatch):
    from service.models import auth_models

    monkeypatch.delenv("JWT_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", "   ")

    resolved = auth_models._key_path("JWT_PRIVATE_KEY_PATH", "jwt-private.pem")

    assert resolved == auth_models.CERTS_DIR / "jwt-private.pem"


def test_the_key_path_is_not_resolved_so_a_missing_file_is_not_an_import_error(monkeypatch):
    """Eager .resolve() would make importing the app fail on a machine with no keys."""
    from service.models import auth_models

    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", "relative/public.pem")

    resolved = auth_models._key_path("JWT_PUBLIC_KEY_PATH", "jwt-public.pem")

    assert resolved == Path("relative/public.pem")


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, 15),
        ("", 15),
        ("   ", 15),
        ("30", 30),
        ("not-a-number", 15),
        ("12.5", 15),
    ],
)
def test_a_bad_expiry_value_falls_back_instead_of_stopping_the_import(monkeypatch, raw, expected):
    from service.models import auth_models

    if raw is None:
        monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    else:
        monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", raw)

    assert auth_models._int_env("ACCESS_TOKEN_EXPIRE_MINUTES", 15) == expected


# ---------------------------------------------------------------------------
# service/shared/errors.py
# ---------------------------------------------------------------------------


def test_the_payload_is_the_same_shape_the_frontend_reads():
    from service.shared.errors import ServiceError, error_payload

    error = ServiceError("something broke")

    assert error.to_payload() == {"detail": "something broke"}
    assert error_payload(error) == {"detail": "something broke"}
    assert error.status_code == 500


def test_a_status_can_be_overridden_for_one_raise_and_does_not_leak_to_the_class():
    """Constructing with status_code= must not mutate the subclass for the process."""
    from service.shared.errors import NotFoundError

    assert NotFoundError("x", status_code=410).status_code == 410
    assert NotFoundError("x").status_code == 404
