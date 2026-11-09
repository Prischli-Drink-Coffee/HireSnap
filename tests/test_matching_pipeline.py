"""The matching slice's ORCHESTRATION: the collaborator resolution and the two pipelines.

WHY A THIRD MATCHING FILE: `test_slices_application.py` owns the arithmetic
(`build_matrix`, `filter_matrix`, `_distance`) plus the preserved personality-index
defect. What it does not reach is the part that only a running pipeline exercises: the
nine collaborators resolved lazily through `service.composition.state`, the write
order of the upload (file row, then embedding, then profile, then the user), and the two
read models that sort dict rows.

WHY THE COLLABORATORS ARE OVERRIDDEN AND NOT `state`: the constructor already accepts
one object per collaborator, so a test never has to touch the wiring root - except in the
ONE test that pins what the wiring root is asked for, which is the only reason to look at
`state` at all.

WHAT IS NOW PINNED HERE AS WELL: the personality-vacancy pairing used to be wrong (a stale
loop index plus row zero of the matrix, docs/KNOWN_GAPS.md section 26). The exact id it
stores is asserted once, in `test_slices_application.py`; this file asserts the axes the
matrix is built from and that the list and the matrix come out of the same filter, which is
the property the defect broke.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from service.services.matching.application import matching_service as matching_module
from service.services.matching.application.matching_service import MatchingService
from tests.fixtures.fake_db import FakeService

# The five OCEAN axes, plus one row that is NOT an axis: the personalities table is
# allowed to hold other rows, so `_ocean_matrix` filters by name.
OCEAN_VECTORS = {
    "O": np.array([1.0, 0.0, 0.0]),
    "C": np.array([0.0, 1.0, 0.0]),
    "E": np.array([0.0, 0.0, 1.0]),
    "A": np.array([1.0, 1.0, 0.0]),
    "N": np.array([0.0, 1.0, 1.0]),
}


def _personalities(names=("O", "C", "E", "A", "N")):
    return [
        types.SimpleNamespace(ID=index + 1, Name=name, EmbeddingID=index + 1)
        for index, name in enumerate(names)
    ]


def _write_vectors(tmp_path, vectors):
    """Save one .npy per embedding id and hand back id -> path."""
    paths = {}
    for embedding_id, vector in vectors.items():
        path = tmp_path / ("embedding-%s.npy" % embedding_id)
        np.save(path, vector)
        paths[embedding_id] = str(path)
    return paths


# --- lazy collaborator resolution -------------------------------------------
COLLABORATORS = [
    ("files", "_files", "get_file_service"),
    ("embeddings", "_embeddings", "get_embedding_service"),
    ("personalities", "_personalities", "get_personality_service"),
    ("users", "_users", "get_user_service"),
    ("vacancies", "_vacancies", "get_vacancy_service"),
    ("info_candidates", "_info_candidates", "get_info_candidate_service"),
    ("personality_candidates", "_personality_candidates", "get_personality_candidate_service"),
    ("personality_vacancies", "_personality_vacancies", "get_personality_vacancy_service"),
    ("candidate_vacancies", "_candidate_vacancies", "get_candidate_vacancy_service"),
]


@pytest.mark.parametrize("parameter,attribute,accessor", COLLABORATORS)
def test_a_collaborator_is_resolved_once_from_the_wiring_root(
    monkeypatch, parameter, attribute, accessor
):
    """Lazy AND cached: the first read asks `service.composition.state`, later reads do not.

    WHY IT MATTERS: resolving on every call would build a new service (and, through it, a
    new repository) per request; resolving at import time would make the slice
    untestable without the whole container.
    """
    from service.composition import state

    asked = []
    sentinel = object()
    monkeypatch.setattr(state, accessor, lambda: asked.append(accessor) or sentinel)
    service = MatchingService()

    assert getattr(service, attribute) is sentinel
    assert getattr(service, attribute) is sentinel
    assert asked == [accessor]


def test_an_injected_collaborator_never_reaches_the_wiring_root(monkeypatch):
    """The nine overrides are the test seam; a constructor argument must win."""
    from service.composition import state

    def explode():
        raise AssertionError("the wiring root must not be asked")

    for _parameter, _attribute, accessor in COLLABORATORS:
        monkeypatch.setattr(state, accessor, explode)

    injected = {parameter: object() for parameter, _attribute, _accessor in COLLABORATORS}
    service = MatchingService(**injected)

    for parameter, attribute, _accessor in COLLABORATORS:
        assert getattr(service, attribute) is injected[parameter]


# --- the OCEAN matrix --------------------------------------------------------
def test_ocean_matrix_keeps_only_the_five_axis_rows(tmp_path):
    """The personalities table may hold rows that are not OCEAN axes; they are skipped.

    THE ASSERTION THAT MATTERS IS THE PAIRING: the list and the matrix come out of the same
    filter, so `returned[j]` is always the row whose vector is column `j`. The defect this
    replaces (docs/KNOWN_GAPS.md section 26) returned EVERY row beside an axes-only matrix,
    which is how a score ended up stored against the wrong personality.
    """
    paths = _write_vectors(
        tmp_path, {index + 1: vector for index, vector in enumerate(OCEAN_VECTORS.values())}
    )
    personalities = _personalities()
    # A non-axis row BETWEEN two axes: filtering alone is not enough, the ORDER has to be
    # the axis order rather than "whatever order the table happened to return".
    personalities.insert(2, types.SimpleNamespace(ID=6, Name="MBTI", EmbeddingID=99))

    service = MatchingService(
        personalities=FakeService(get_all_personalities=lambda: personalities),
        embeddings=FakeService(
            get_embedding_by_id=lambda embedding_id: types.SimpleNamespace(Url=paths[embedding_id])
        ),
    )

    returned, matrix = service._ocean_matrix()

    assert [row.Name for row in returned] == ["O", "C", "E", "A", "N"]
    assert len(returned) == matrix.shape[0]
    assert matrix.shape == (5, 3)
    assert matrix.tolist() == [vector.tolist() for vector in OCEAN_VECTORS.values()]


# --- similarity_for_user ----------------------------------------------------
def _similarity_service(tmp_path, vacancies, monkeypatch):
    """A service wired for `similarity_for_user` over the vectors declared above.

    The candidate's own vector is stored the way the real code stores it - a path RELATIVE
    to `DATA_PATH` - and `DATA_PATH` is pointed at the temporary directory, so the test
    exercises the resolution instead of dodging it with a `chdir`. The old fixture chdir'd
    and relied on the process working directory, which is exactly the bug `_embedding_path`
    removes.
    """
    (tmp_path / "data").mkdir(exist_ok=True)
    np.save(tmp_path / "data" / "user.npy", OCEAN_VECTORS["O"])
    monkeypatch.setenv("DATA_PATH", str(tmp_path / "data"))

    paths = _write_vectors(
        tmp_path,
        {
            **{index + 1: vector for index, vector in enumerate(OCEAN_VECTORS.values())},
            **{vacancy["embedding_id"]: vacancy["vector"] for vacancy in vacancies},
        },
    )
    # The candidate's own vector is the ONE path resolved relative to DATA_PATH (that is
    # the real code's convention for user embeddings); every other path is absolute.
    paths["user"] = "user.npy"
    written = {"personality_candidates": [], "personality_vacancies": [], "candidate_vacancies": []}
    service = MatchingService(
        personalities=FakeService(get_all_personalities=lambda: _personalities()),
        embeddings=FakeService(
            get_embedding_by_id=lambda embedding_id: types.SimpleNamespace(Url=paths[embedding_id])
        ),
        users=FakeService(
            get_user_by_id=lambda user_id: types.SimpleNamespace(ID=user_id, InfoID=7)
        ),
        info_candidates=FakeService(
            get_info_candidate_by_id=lambda info_id: types.SimpleNamespace(EmbeddingID="user")
        ),
        vacancies=FakeService(
            get_all_vacancies=lambda: [
                types.SimpleNamespace(ID=index + 1, EmbeddingID=vacancy["embedding_id"])
                for index, vacancy in enumerate(vacancies)
            ]
        ),
        personality_candidates=FakeService(
            create_personality_candidate=lambda row: written["personality_candidates"].append(row)
        ),
        personality_vacancies=FakeService(
            create_personality_vacancy=lambda row: written["personality_vacancies"].append(row)
        ),
        candidate_vacancies=FakeService(
            create_candidate_vacancy=lambda row: written["candidate_vacancies"].append(row)
        ),
    )
    return service, written


# The user vector is O. Against the five axes the cosine distances are
#   O 0.0 | C 1.0 | E 1.0 | A 0.2929 | N 1.0
# so exactly three of the five clear the 0.7 threshold - which is the point: the loop has
# both a stored row and a skipped one.
def test_similarity_stores_one_personality_row_per_axis_over_the_threshold(tmp_path, monkeypatch):
    vacancies = [
        {"embedding_id": 101, "vector": np.array([0.0, 1.0, 0.0])},
        {"embedding_id": 102, "vector": np.array([1.0, 0.0, 0.0])},
    ]
    service, written = _similarity_service(tmp_path, vacancies, monkeypatch)

    returned = service.similarity_for_user(7)

    assert returned.shape == (1, 5)
    assert sorted(row.UserID for row in written["personality_candidates"]) == [7, 7, 7]
    assert sorted(row.PersonalityID for row in written["personality_candidates"]) == [2, 3, 5]
    # The distance is quantised into decimal(6,6) and never 1.0.
    assert all(
        row.Distance <= matching_module.MAX_DISTANCE for row in written["personality_candidates"]
    )


def test_similarity_stores_the_vacancy_a_candidate_matches(tmp_path, monkeypatch):
    """Two vacancies are needed: with one, per-row normalisation collapses to 0 and no
    candidate_vacancy row is ever written."""
    vacancies = [
        {"embedding_id": 101, "vector": np.array([0.0, 1.0, 0.0])},
        {"embedding_id": 102, "vector": np.array([1.0, 0.0, 0.0])},
    ]
    service, written = _similarity_service(tmp_path, vacancies, monkeypatch)

    service.similarity_for_user(7)

    # The normalised row is [0.0, 1.0]: only the second vacancy clears the threshold.
    assert [row.VacancyID for row in written["candidate_vacancies"]] == [2]
    assert written["candidate_vacancies"][0].Distance == matching_module.MAX_DISTANCE


def test_similarity_does_not_invent_rows_when_nothing_clears_the_threshold(tmp_path, monkeypatch):
    """One vacancy makes the normalised row all zeros: a truthful "no match"."""
    vacancies = [{"embedding_id": 101, "vector": np.array([0.0, 1.0, 0.0])}]
    service, written = _similarity_service(tmp_path, vacancies, monkeypatch)

    service.similarity_for_user(7)

    assert written["candidate_vacancies"] == []


def test_similarity_returns_the_candidate_matrix(tmp_path, monkeypatch):
    vacancies = [{"embedding_id": 101, "vector": np.array([0.0, 1.0, 0.0])}]
    service, _written = _similarity_service(tmp_path, vacancies, monkeypatch)

    returned = service.similarity_for_user(7)

    # C[0] is the candidate's distance to O, C, E, A, N - the value the caller reads.
    assert returned[0].tolist() == pytest.approx([0.0, 1.0, 1.0, 0.292893, 1.0], abs=1e-5)


# --- upload_video -----------------------------------------------------------
class FakeUploadedFile:
    """The one attribute `write_file_into_server` reads from an UploadFile."""

    def __init__(self, filename="clip.mp4", payload=b"video"):
        self.filename = filename
        self.payload = payload

    async def read(self):
        return self.payload


@pytest.fixture()
def upload_io(monkeypatch):
    """Replace the three I/O calls the pipeline makes and record them in order."""
    import asyncio

    order = []

    async def write_file(namespace, file):
        order.append(("write_file", namespace, file.filename))
        return "stored.mp4"

    def write_embedding(namespace, embedding):
        order.append(("write_embedding", namespace, list(embedding)))
        return "stored.npy"

    def embed(url):
        order.append(("embed", url))
        return np.array([0.5, 0.25])

    monkeypatch.setattr(matching_module, "write_file_into_server", write_file)
    monkeypatch.setattr(matching_module, "write_embedding_into_server", write_embedding)
    monkeypatch.setattr(matching_module, "get_user_embedding", embed)
    monkeypatch.setattr(matching_module, "return_url_object", lambda url: "http://public/" + url)
    return types.SimpleNamespace(order=order, loop=asyncio.new_event_loop())


class FakeUser:
    """A Users row that renders ITSELF, so a test sees what the pipeline mutated.

    `model_dump` is a closure over the live attribute, not over the constructor
    argument: the pipeline sets `user.InfoID` before dumping, and a snapshot taken at
    construction time would hide exactly the assignment under test.
    """

    def __init__(self, info_id=None):
        self.ID = 3
        self.Email = "a@example.com"
        self.InfoID = info_id
        self.CreatedAt = "2024-01-02 03:04:05"
        self.Type = types.SimpleNamespace(value="candidate")
        self.Role = types.SimpleNamespace(value="user")

    def model_dump(self, by_alias=False):
        return {"id": self.ID, "info_id": self.InfoID, "email": self.Email}


def _user(info_id=None):
    return FakeUser(info_id)


def test_upload_creates_a_profile_when_the_user_has_none(tmp_path, monkeypatch, upload_io):
    """The order is the contract: file row first (the bytes are visible even if inference
    fails), then the embedding, then the profile, then the user pointing at it."""
    monkeypatch.chdir(tmp_path)
    user = _user(info_id=None)
    files = FakeService(create_file=lambda row: types.SimpleNamespace(ID=11, Url=row.Url))
    embeddings = FakeService(create_embedding=lambda row: types.SimpleNamespace(ID=22, Url=row.Url))
    info_candidates = FakeService(
        create_info_candidate=lambda row: types.SimpleNamespace(
            ID=33,
            Name=row.Name,
            Phone=row.Phone,
            Gender=row.Gender,
            DateBirth=row.DateBirth,
        )
    )
    users = FakeService(
        get_user_by_id=lambda user_id: user, update_user=lambda user_id, payload: None
    )
    service = MatchingService(
        files=files,
        embeddings=embeddings,
        info_candidates=info_candidates,
        users=users,
    )

    result = upload_io.loop.run_until_complete(
        service.upload_video(FakeUploadedFile(), 3, "Ada", "+1", None, "female")
    )

    assert [step[0] for step in upload_io.order] == ["write_file", "embed", "write_embedding"]
    assert files.calls[0][1][0].Url == "data/files/stored.mp4"
    assert embeddings.calls[0][1][0].Url == "embeddings/users/stored.npy"
    created = info_candidates.calls[0][1][0]
    assert (created.Name, created.FileID, created.EmbeddingID) == ("Ada", 11, 22)
    assert user.InfoID == 33
    # The user update carries the alias keys the API accepts back, and runs AFTER the
    # profile exists, so the row it writes points at a real info_candidate.
    user_id, payload = users.called("update_user")[0][1]
    assert user_id == 3
    assert payload["info_id"] == 33
    assert result["email"] == "a@example.com"
    assert result["name"] == "Ada"
    assert result["video_url"] == "http://public/data/files/stored.mp4"


def test_upload_updates_the_existing_profile_and_formats_the_birth_date(
    tmp_path, monkeypatch, upload_io
):
    monkeypatch.chdir(tmp_path)
    user = _user(info_id=44)
    existing = types.SimpleNamespace(
        ID=44,
        Name="old",
        Phone="old",
        DateBirth=None,
        Gender=None,
        model_dump=lambda **kw: {"x": 1},
    )
    reads = []

    def get_info(info_id):
        reads.append(info_id)
        return existing

    info_candidates = FakeService(
        get_info_candidate_by_id=get_info,
        update_info_candidate=lambda info_id, payload: None,
    )
    service = MatchingService(
        files=FakeService(create_file=lambda row: types.SimpleNamespace(ID=11, Url=row.Url)),
        embeddings=FakeService(
            create_embedding=lambda row: types.SimpleNamespace(ID=22, Url=row.Url)
        ),
        info_candidates=info_candidates,
        users=FakeService(get_user_by_id=lambda user_id: user, update_user=lambda *args: None),
    )

    result = upload_io.loop.run_until_complete(
        service.upload_video(FakeUploadedFile(), 3, "Ada", "+1", "1990-05-06", "female")
    )

    # Read, patch, write, read back - the profile the response reports is the stored one.
    assert reads == [44, 44]
    updated = info_candidates.called("update_info_candidate")[0][1]
    assert updated[0] == 44
    assert existing.DateBirth == "1990-05-06"
    assert result["date_birth"].startswith("1990-05-06")


# --- upload_vacancy ---------------------------------------------------------
@pytest.fixture()
def vacancy_io(monkeypatch):
    """The three I/O calls the vacancy pipeline makes, recorded in the order they run."""
    from service.models import Vacancies

    order = []
    stored = {}

    def embed(text):
        order.append(("embed", text))
        return np.array([0.5, 0.25, 0.125])

    def write_embedding(namespace, embedding):
        order.append(("write_embedding", namespace, list(embedding)))
        return "vacancy.npy"

    def create_embedding(entity):
        order.append(("create_embedding", entity.Url))
        return types.SimpleNamespace(ID=77)

    def create_vacancy(vacancy):
        order.append(("create_vacancy", vacancy.Name, vacancy.EmbeddingID))
        stored["vacancy"] = vacancy
        # Built by ALIAS: the model declares field names (ID, Name) with snake_case aliases
        # and does not populate by field name, which is how the repository returns rows.
        return Vacancies(
            id=5,
            name=vacancy.Name,
            description=vacancy.Description,
            salary=vacancy.Salary,
            skill=vacancy.Skill,
            embedding_id=vacancy.EmbeddingID,
        )

    monkeypatch.setattr(matching_module, "get_text_embedding", embed)
    monkeypatch.setattr(matching_module, "write_embedding_into_server", write_embedding)
    return types.SimpleNamespace(
        order=order,
        stored=stored,
        embeddings=FakeService(create_embedding=create_embedding),
        vacancies=FakeService(create_vacancy=create_vacancy),
    )


def test_upload_vacancy_stores_the_embedding_before_the_row(vacancy_io):
    """The order is the contract: a vacancy row must never point at a vector that is absent.

    This replaces a test that pinned `{"message": "test"}` - the placeholder the route used
    to answer while the employer form told the user the vacancy had been saved
    (docs/KNOWN_GAPS.md section 37).
    """
    service = MatchingService(embeddings=vacancy_io.embeddings, vacancies=vacancy_io.vacancies)

    result = service.upload_vacancy("Backend", "Build it", "100-200", "python")

    assert [step[0] for step in vacancy_io.order] == [
        "embed",
        "write_embedding",
        "create_embedding",
        "create_vacancy",
    ]
    assert vacancy_io.order[1][1] == "vacancies"
    assert vacancy_io.order[2][1] == "embeddings/vacancies/vacancy.npy"
    # The six keys the frontend contract names, and nothing else.
    assert set(result) == {"id", "name", "salary", "description", "skill", "embedding_id"}
    assert result["id"] == 5
    assert result["embedding_id"] == 77


def test_upload_vacancy_embeds_title_description_and_skills(vacancy_io):
    """The title is the most informative token in a posting, so it is part of the text."""
    service = MatchingService(embeddings=vacancy_io.embeddings, vacancies=vacancy_io.vacancies)

    service.upload_vacancy("Backend", "Build it", "100-200", "python, sql")

    assert vacancy_io.order[0][1] == "Backend\n\nBuild it\n\nSkills: python, sql"


def test_upload_vacancy_leaves_blank_optional_fields_null(vacancy_io):
    """A browser posts "" for an untouched box; the column should hold NULL, not ""."""
    service = MatchingService(embeddings=vacancy_io.embeddings, vacancies=vacancy_io.vacancies)

    service.upload_vacancy("  Backend  ", " Build it ", "   ", "")

    stored = vacancy_io.stored["vacancy"]
    assert stored.Name == "Backend"
    assert stored.Salary is None
    assert stored.Skill is None
    assert vacancy_io.order[0][1] == "Backend\n\nBuild it"


@pytest.mark.parametrize("name,description", [("", "Build it"), ("Backend", "   ")])
def test_upload_vacancy_names_the_blank_required_field(vacancy_io, name, description):
    """Rejecting here names the field; the sidecar would answer 400 without naming it."""
    from service.shared.errors import InvalidPayloadError

    service = MatchingService(embeddings=vacancy_io.embeddings, vacancies=vacancy_io.vacancies)

    with pytest.raises(InvalidPayloadError) as failure:
        service.upload_vacancy(name, description, None, None)

    assert failure.value.status_code == 400
    assert "Missing required vacancy field" in failure.value.detail
    assert vacancy_io.order == []


def test_a_stopped_sidecar_is_a_503_and_creates_no_row(vacancy_io, monkeypatch):
    """MLUnavailable is a RuntimeError; uncaught it would be an opaque 500."""
    from service.infrastructure.ml_sidecar import MLUnavailable
    from service.shared.errors import UpstreamUnavailableError

    def explode(text):
        raise MLUnavailable(
            "ml_service_unreachable",
            "cannot reach the ml sidecar at http://ml:8080: refused",
        )

    monkeypatch.setattr(matching_module, "get_text_embedding", explode)
    service = MatchingService(embeddings=vacancy_io.embeddings, vacancies=vacancy_io.vacancies)

    with pytest.raises(UpstreamUnavailableError) as failure:
        service.upload_vacancy("Backend", "Build it", None, None)

    assert failure.value.status_code == 503
    assert "http://ml:8080" in failure.value.detail
    assert vacancy_io.order == []  # no embedding row, no vacancy row


def test_a_refused_text_is_a_400(vacancy_io, monkeypatch):
    """The sidecar saying "I will not embed that" is the client's 400, not our 500."""
    from service.infrastructure.ml_sidecar import MLRejected
    from service.shared.errors import InvalidPayloadError

    def explode(text):
        raise MLRejected("empty_body", "text is empty", status=400)

    monkeypatch.setattr(matching_module, "get_text_embedding", explode)
    service = MatchingService(embeddings=vacancy_io.embeddings, vacancies=vacancy_io.vacancies)

    with pytest.raises(InvalidPayloadError) as failure:
        service.upload_vacancy("Backend", "Build it", None, None)

    assert failure.value.status_code == 400
    assert "empty_body" in failure.value.detail


# --- the read models --------------------------------------------------------
def _candidate_vacancy_row(user_id, vacancy_id, distance):
    return types.SimpleNamespace(
        UserID=user_id,
        VacancyID=vacancy_id,
        Distance=distance,
        model_dump=lambda row_user=user_id, row_vacancy=vacancy_id, row_distance=distance: {
            "user_id": row_user,
            "vacancy_id": row_vacancy,
            "distance": row_distance,
        },
    )


def test_get_all_vacancies_returns_the_candidates_rows_closest_first():
    """The rows are DICTS, so the sort key has to name a field: a bare sort() raised
    TypeError as soon as a candidate had two rows (docs/KNOWN_GAPS.md)."""
    rows = [
        _candidate_vacancy_row(1, 10, 0.2),
        _candidate_vacancy_row(2, 11, 0.9),
        _candidate_vacancy_row(1, 12, 0.7),
    ]
    service = MatchingService(
        users=FakeService(get_user_by_id=lambda user_id: types.SimpleNamespace(ID=user_id)),
        candidate_vacancies=FakeService(get_all_candidate_vacancies=lambda: rows),
    )

    assert [row["vacancy_id"] for row in service.get_all_vacancies(1)] == [12, 10]


def test_get_all_candidates_returns_the_vacancies_rows_closest_first():
    """This route used to call the OTHER read model, looking a vacancy id up as a user id."""
    rows = [
        _candidate_vacancy_row(1, 10, 0.3),
        _candidate_vacancy_row(2, 10, 0.8),
        _candidate_vacancy_row(3, 11, 0.9),
    ]
    service = MatchingService(
        vacancies=FakeService(
            get_vacancy_by_id=lambda vacancy_id: types.SimpleNamespace(ID=vacancy_id)
        ),
        candidate_vacancies=FakeService(get_all_candidate_vacancies=lambda: rows),
    )

    assert [row["user_id"] for row in service.get_all_candidates(10)] == [2, 1]


def test_a_row_without_a_distance_sorts_last_instead_of_raising():
    """`distance` is nullable in the table; a NULL must not decide the order."""
    rows = [
        _candidate_vacancy_row(1, 10, None),
        _candidate_vacancy_row(2, 10, 0.5),
    ]
    service = MatchingService(
        vacancies=FakeService(
            get_vacancy_by_id=lambda vacancy_id: types.SimpleNamespace(ID=vacancy_id)
        ),
        candidate_vacancies=FakeService(get_all_candidate_vacancies=lambda: rows),
    )

    assert [row["user_id"] for row in service.get_all_candidates(10)] == [2, 1]
