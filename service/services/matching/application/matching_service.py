"""The rules of the matching slice: score a candidate against vacancies, run the pipeline.

WHAT MOVED AND WHY

This was `service/services/matching.py` (and before the refactor, `main_services.py` in the
old `src/` tree): a flat module holding both the linear algebra and the orchestration of an
upload, reaching every other slice through module imports and raising `fastapi.HTTPException`.
It is one class now, and three things follow:

  * the collaborators are resolved through `service/composition/state.py`, so a test can
    score a candidate without a database, and the import graph no longer decides which
    implementation of the users slice is used;
  * nothing here imports a web framework (`service/services/matching/application/` is
    checked by backend/tests/test_service_boundary.py), so the same rule could run from a
    worker;
  * a failure is a `ServiceError`, and one handler in `service/api/app.py` renders it.

WHY `build_matrix` AND `filter_matrix` STAY MODULE-LEVEL: they are pure functions of their
arguments with no collaborators and no I/O. Putting them on the class would add a `self`
that nothing reads and make them harder to call from a notebook or a test.

WHY THE SCORE IS QUANTISED: the columns are `decimal(6,6)` in `database/HireSnap.sql`, so a
value of exactly 1.0 - which `cosine_distances` can produce - does not fit and MySQL
truncates or errors depending on mode. Clamping to 0.999999 is the same guard the original
code had, kept verbatim.

THE INLINE PIPELINE IS STILL INLINE, DELIBERATELY: `upload_video` transcribes, embeds and
persists inside the request. The `jobs` slice exists to move that work behind a broker, but
wiring it here would change the response shape the frontend already reads, so it is a
separate, contract-changing step - not something a refactor does on the way past.
"""

from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from sklearn.metrics.pairwise import cosine_distances

from service.configuration.logging import get_logger
from service.infrastructure.ml_sidecar import get_user_embedding
from service.infrastructure.uploads import (
    write_embedding_into_server,
    write_file_into_server,
)
from service.models import (
    CandidateVacancies,
    Embeddings,
    Files,
    InfoCandidates,
    PersonalityCandidates,
    PersonalityVacancies,
)
from service.shared.urls import return_url_object

log = get_logger()

#: The five OCEAN axes the personalities table is expected to hold, one embedding each.
OCEAN_NAMES = ("O", "C", "E", "A", "N")

#: Two rows are only recorded when the score clears this. Unchanged from the original.
SIMILARITY_THRESHOLD = 0.7

#: decimal(6,6) cannot hold 1.0; clamp so the INSERT never overflows the column.
MAX_DISTANCE = Decimal("0.999999")


def build_matrix(candidates=np.array, ocean=np.array, vacancies=np.array):
    """Score every candidate against every vacancy through the OCEAN space.

    C is candidate-vs-personality similarity, P is vacancy-vs-personality. Combining them as
    C @ P.T + (1 - C) @ (1 - P.T) means "alike on the axes where they agree, and alike on the
    axes where they disagree", which is the original formulation and is kept as-is: changing
    the metric changes every stored score, so it is a product decision, not a cleanup.
    """
    C = cosine_distances(candidates, ocean)
    P = cosine_distances(vacancies, ocean)
    final_matrix = C.dot(P.T) + (1 - C).dot(1 - P.T)
    return final_matrix, C, P


def filter_matrix(final_matrix, threshold=SIMILARITY_THRESHOLD):
    """Normalise each row to 0..1 and zero everything under the threshold.

    A row whose min equals its max (a single vacancy, or identical scores) divides by zero;
    the original replaced that denominator with 1, which leaves the row at 0 and drops it.
    Preserved because a one-vacancy catalogue behaves that way today.
    """
    normalized_matrix = final_matrix.copy()
    row_min = normalized_matrix.min(axis=1).reshape(-1, 1)
    row_max = normalized_matrix.max(axis=1).reshape(-1, 1)
    denominator = row_max - row_min
    denominator[denominator == 0] = 1
    normalized_matrix = (normalized_matrix - row_min) / denominator
    normalized_matrix[normalized_matrix < threshold] = 0
    return normalized_matrix


def _distance(value) -> Decimal:
    """Quantise a similarity score into the column's scale, never reaching 1.0."""
    return min(
        Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        MAX_DISTANCE,
    )


class MatchingService:
    """The pipeline: one candidate video in, a profile and three link tables out."""

    def __init__(
        self,
        files=None,
        embeddings=None,
        personalities=None,
        users=None,
        vacancies=None,
        info_candidates=None,
        personality_candidates=None,
        personality_vacancies=None,
        candidate_vacancies=None,
    ) -> None:
        self._files_override = files
        self._embeddings_override = embeddings
        self._personalities_override = personalities
        self._users_override = users
        self._vacancies_override = vacancies
        self._info_candidates_override = info_candidates
        self._personality_candidates_override = personality_candidates
        self._personality_vacancies_override = personality_vacancies
        self._candidate_vacancies_override = candidate_vacancies

    @property
    def _files(self):
        if self._files_override is None:
            from service.composition import state

            self._files_override = state.get_file_service()
        return self._files_override

    @property
    def _embeddings(self):
        if self._embeddings_override is None:
            from service.composition import state

            self._embeddings_override = state.get_embedding_service()
        return self._embeddings_override

    @property
    def _personalities(self):
        if self._personalities_override is None:
            from service.composition import state

            self._personalities_override = state.get_personality_service()
        return self._personalities_override

    @property
    def _users(self):
        if self._users_override is None:
            from service.composition import state

            self._users_override = state.get_user_service()
        return self._users_override

    @property
    def _vacancies(self):
        if self._vacancies_override is None:
            from service.composition import state

            self._vacancies_override = state.get_vacancy_service()
        return self._vacancies_override

    @property
    def _info_candidates(self):
        if self._info_candidates_override is None:
            from service.composition import state

            self._info_candidates_override = state.get_info_candidate_service()
        return self._info_candidates_override

    @property
    def _personality_candidates(self):
        if self._personality_candidates_override is None:
            from service.composition import state

            self._personality_candidates_override = state.get_personality_candidate_service()
        return self._personality_candidates_override

    @property
    def _personality_vacancies(self):
        if self._personality_vacancies_override is None:
            from service.composition import state

            self._personality_vacancies_override = state.get_personality_vacancy_service()
        return self._personality_vacancies_override

    @property
    def _candidate_vacancies(self):
        if self._candidate_vacancies_override is None:
            from service.composition import state

            self._candidate_vacancies_override = state.get_candidate_vacancy_service()
        return self._candidate_vacancies_override

    # --- scoring -----------------------------------------------------------
    def _ocean_matrix(self):
        """The five OCEAN embeddings, stacked in the order the personalities table lists them."""
        personalities = self._personalities.get_all_personalities()
        rows = []
        for personality in personalities:
            if personality.Name in OCEAN_NAMES:
                storage_path = self._embeddings.get_embedding_by_id(personality.EmbeddingID).Url
                rows.append(np.load(storage_path))
        return personalities, np.vstack(rows)

    def similarity_for_user(self, user_id: int):
        """Score one candidate against every personality and vacancy and store the rows.

        WHAT THIS RETURNS: the candidate-vs-personality matrix (C), which is what the original
        returned and what the commented-out caller expected.

        KNOWN DEFECT, PRESERVED ON PURPOSE: the personality-vacancy loop reads
        `personalities[j]` while iterating zipped P rows, so `j` is the leftover index of the
        previous loop. The original did the same, and the row it writes is therefore not
        always the personality the score belongs to. Fixing it changes stored data, which is
        a migration, so it is recorded in docs/KNOWN_GAPS.md instead of being slipped into a
        refactor whose whole claim is "behaviour unchanged".
        """
        personalities, ocean = self._ocean_matrix()

        user = self._users.get_user_by_id(user_id)
        info_candidate = self._info_candidates.get_info_candidate_by_id(user.InfoID)
        embedding_url = self._embeddings.get_embedding_by_id(info_candidate.EmbeddingID).Url
        user_embedding = np.load(os.path.join("./data", embedding_url))

        vacancies = self._vacancies.get_all_vacancies()
        vacancy_embeddings = np.vstack(
            [
                np.load(self._embeddings.get_embedding_by_id(vacancy.EmbeddingID).Url)
                for vacancy in vacancies
            ]
        )

        a, C, P = build_matrix(np.vstack([user_embedding]), ocean, vacancy_embeddings)
        b = filter_matrix(a)

        for j, personality in enumerate(personalities):
            similarity = C[0, j]
            if similarity >= SIMILARITY_THRESHOLD:
                self._personality_candidates.create_personality_candidate(
                    PersonalityCandidates(
                        personality_id=personality.ID,
                        user_id=user.ID,
                        distance=_distance(similarity),
                    )
                )

        for similarity, vacancy in zip(P[0, :], vacancies):
            if similarity >= SIMILARITY_THRESHOLD:
                self._personality_vacancies.create_personality_vacancy(
                    PersonalityVacancies(
                        personality_id=personalities[j].ID,
                        vacancy_id=vacancy.ID,
                        distance=_distance(similarity),
                    )
                )

        for j, vacancy in enumerate(vacancies):
            similarity = b[0, j]
            if similarity >= SIMILARITY_THRESHOLD:
                self._candidate_vacancies.create_candidate_vacancy(
                    CandidateVacancies(
                        user_id=user.ID,
                        vacancy_id=vacancy.ID,
                        distance=_distance(similarity),
                    )
                )

        return C

    # --- the upload pipeline -----------------------------------------------
    async def upload_video(self, file, user_id, name, phone, date_birth, gender):
        """Store the video, embed it through the sidecar, and write the candidate profile.

        THE ORDER IS THE CONTRACT: the file row is written before inference so a failed
        embedding leaves the upload visible rather than losing the bytes, and the profile is
        written last so a candidate row never points at an embedding that does not exist.
        """
        if date_birth is not None:
            date_birth = f"{date_birth}"

        unique_file = await write_file_into_server("files", file)
        file_entity = self._files.create_file(Files(url=f"data/files/{unique_file}"))

        # The sidecar answers over HTTP (service/infrastructure/ml_sidecar.py); this call is
        # the ONLY path to inference from the backend, so the API process carries no torch.
        video_embedding = get_user_embedding(file_entity.Url)

        unique_embedding = write_embedding_into_server("users", video_embedding)
        embedding_entity = self._embeddings.create_embedding(
            Embeddings(url=f"embeddings/users/{unique_embedding}")
        )

        user = self._users.get_user_by_id(user_id)
        log.info(user)
        if user.InfoID is not None:
            info_candidate = self._info_candidates.get_info_candidate_by_id(user.InfoID)
            info_candidate.Name = name
            info_candidate.Phone = phone
            info_candidate.DateBirth = date_birth
            info_candidate.Gender = gender
            self._info_candidates.update_info_candidate(
                user.InfoID, info_candidate.model_dump(by_alias=True)
            )
            info_candidate = self._info_candidates.get_info_candidate_by_id(user.InfoID)
        else:
            info_candidate = self._info_candidates.create_info_candidate(
                InfoCandidates(
                    name=name,
                    phone=phone,
                    gender=gender,
                    date_birth=date_birth,
                    file_id=file_entity.ID,
                    embedding_id=embedding_entity.ID,
                )
            )

        user.InfoID = info_candidate.ID
        user.CreatedAt = f"{user.CreatedAt}"
        user.Type = user.Type.value
        user.Role = user.Role.value
        self._users.update_user(user.ID, user.model_dump(by_alias=True))

        return {
            "email": user.Email,
            "name": info_candidate.Name,
            "phone": info_candidate.Phone,
            "gender": info_candidate.Gender,
            "date_birth": f"{info_candidate.DateBirth}",
            "video_url": return_url_object(file_entity.Url),
        }

    def upload_vacancy(self, name, description, salary, skill):
        """Accept a vacancy from the employer form and persist nothing yet.

        THIS IS THE HONEST STATE OF THE FEATURE, not a stub a refactor introduced: the route
        validates its four form fields and answers with a fixed body, because the form posts
        text and the pipeline needs an embedding the employer has not produced. The old
        module returned the same literal; the frontend shows a warning instead of a success
        toast because of it (docs/KNOWN_GAPS.md).
        """
        log.info("vacancy received but not persisted: name=%r salary=%r", name, salary)
        return {"message": "test"}

    # --- read models -------------------------------------------------------
    def get_all_vacancies(self, user_id):
        """The vacancies recommended to one candidate, closest first.

        The sort key is explicit because the rows are DICTS: `sort(reverse=True)` on a list of
        dicts raised "TypeError: '<' not supported between instances of 'dict' and 'dict'"
        as soon as a candidate had two rows - a latent 500 recorded in docs/KNOWN_GAPS.md and
        fixed here, since a refactor that preserves a guaranteed crash is not a refactor.
        """
        user = self._users.get_user_by_id(user_id)
        candidate_vacancies = self._candidate_vacancies.get_all_candidate_vacancies()
        rows = [sample.model_dump() for sample in candidate_vacancies if sample.UserID == user.ID]
        rows.sort(key=lambda row: row.get("distance") or 0, reverse=True)
        return rows

    def get_all_candidates(self, vacancy_id):
        """The candidates recommended for one vacancy, closest first.

        This is what the "candidates for a vacancy" route calls. The old handler called the
        vacancy-for-user function instead - a copy-paste bug that looked up a vacancy id as a
        user id - so that route never answered the question it was asked.
        """
        vacancy = self._vacancies.get_vacancy_by_id(vacancy_id)
        candidate_vacancies = self._candidate_vacancies.get_all_candidate_vacancies()
        rows = [
            sample.model_dump() for sample in candidate_vacancies if sample.VacancyID == vacancy.ID
        ]
        rows.sort(key=lambda row: row.get("distance") or 0, reverse=True)
        return rows
