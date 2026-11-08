"""How a slice service is assembled: its persistence module plus its collaborators.

WHY THIS IS A TABLE OF BUILDERS RATHER THAN THIRTEEN IMPORTS AND THIRTEEN CALLS: the
interesting information is the ARGUMENT to each constructor - the users slice needs its
repository, the candidate-vacancies slice needs the users and vacancies services, the
matching slice needs nine of them. Writing that as a function per slice makes the wiring
readable and makes a missing collaborator impossible to hide, because the constructor
signature is on the line that calls it.

WHY THE MODULES ARE IMPORTED INSIDE THE BUILDERS: a top-level import of all thirteen
slices here would drag the entire application - pymysql included - into any module that
imported the container to ask one question. The table holds import strings, exactly like
`state.SLICE_SERVICES`, and the strings are resolved on first use.

WHY THE PERSISTENCE MODULE IS PASSED IN EXPLICITLY: a slice must be constructible with a
fake repository and nothing else. That is what `backend/tests/test_service_boundary.py`
checks from the outside, and it is why every constructor takes `repository=None`.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict

#: domain -> the module holding the class. Kept in step with `state.SLICE_SERVICES`.
SERVICE_MODULES: Dict[str, tuple] = {
    "auth": ("service.services.auth.application.auth_service", "AuthService"),
    "users": ("service.services.users.application.user_service", "UserService"),
    "candidates": (
        "service.services.candidates.application.info_candidate_service",
        "InfoCandidateService",
    ),
    "vacancies": (
        "service.services.vacancies.application.vacancy_service",
        "VacancyService",
    ),
    "magics": ("service.services.magics.application.magic_service", "MagicService"),
    "personalities": (
        "service.services.personalities.application.personality_service",
        "PersonalityService",
    ),
    "personality_vacancies": (
        "service.services.personality_vacancies.application.personality_vacancy_service",
        "PersonalityVacancyService",
    ),
    "personality_candidates": (
        "service.services.personality_candidates.application.personality_candidate_service",
        "PersonalityCandidateService",
    ),
    "candidate_vacancies": (
        "service.services.candidate_vacancies.application.candidate_vacancy_service",
        "CandidateVacancyService",
    ),
    "files": ("service.services.files.application.file_service", "FileService"),
    "embeddings": (
        "service.services.embeddings.application.embedding_service",
        "EmbeddingService",
    ),
    "matching": (
        "service.services.matching.application.matching_service",
        "MatchingService",
    ),
    "jobs": ("service.services.jobs.application.job_service", "JobService"),
}

#: domain -> the persistence package its repository lives in. A slice without a table of
#: its own (auth, matching, jobs) has no entry, which is why `build_service` asks first.
PERSISTENCE_MODULES: Dict[str, str] = {
    "users": "service.services.users.persistence",
    "candidates": "service.services.candidates.persistence",
    "vacancies": "service.services.vacancies.persistence",
    "magics": "service.services.magics.persistence",
    "personalities": "service.services.personalities.persistence",
    "personality_vacancies": "service.services.personality_vacancies.persistence",
    "personality_candidates": "service.services.personality_candidates.persistence",
    "candidate_vacancies": "service.services.candidate_vacancies.persistence",
    "files": "service.services.files.persistence",
    "embeddings": "service.services.embeddings.persistence",
}

#: The build order for a full container. The matching slice pulls in almost everything, so
#: it is last; every other slice only needs its own repository.
BUILD_ORDER = (
    "users",
    "candidates",
    "vacancies",
    "magics",
    "personalities",
    "files",
    "embeddings",
    "personality_vacancies",
    "personality_candidates",
    "candidate_vacancies",
    "auth",
    "jobs",
    "matching",
)


def load_class(domain: str) -> Any:
    """Import and return the class for one domain, with a message that names the table."""
    try:
        module_name, class_name = SERVICE_MODULES[domain]
    except KeyError:
        raise KeyError(
            "unknown service %r; known slices: %s" % (domain, ", ".join(sorted(SERVICE_MODULES)))
        ) from None
    module = importlib.import_module(module_name)
    try:
        return getattr(module, class_name)
    except AttributeError as error:
        raise ImportError(
            "%s defines no %s - the slice moved and this table did not (%s)"
            % (module_name, class_name, error)
        ) from error


def load_repository(domain: str) -> Any:
    """The persistence package for one domain, or None when the slice owns no table."""
    module_name = PERSISTENCE_MODULES.get(domain)
    if module_name is None:
        return None
    return importlib.import_module(module_name)


def build_service(domain: str, resolve) -> Any:
    """Construct one slice service.

    `resolve` is called with a domain name and returns that sibling service; passing the
    container's own resolver in means a slice never imports the container and the graph is
    still closed in one place. Cycles are harmless: `AuthService` and the three link slices
    take their collaborators behind lazy properties too, so a resolver that arrives late is
    enough.
    """
    factory = load_class(domain)
    repository = load_repository(domain)
    if domain == "auth":
        return factory(user=resolve("users"))
    if domain == "jobs":
        return factory()
    if domain == "matching":
        return factory(
            files=resolve("files"),
            embeddings=resolve("embeddings"),
            personalities=resolve("personalities"),
            users=resolve("users"),
            vacancies=resolve("vacancies"),
            info_candidates=resolve("candidates"),
            personality_candidates=resolve("personality_candidates"),
            personality_vacancies=resolve("personality_vacancies"),
            candidate_vacancies=resolve("candidate_vacancies"),
        )
    if domain == "personality_vacancies":
        return factory(
            repository=repository,
            personality=resolve("personalities"),
            vacancy=resolve("vacancies"),
        )
    if domain == "personality_candidates":
        return factory(
            repository=repository,
            personality=resolve("personalities"),
            user=resolve("users"),
        )
    if domain == "candidate_vacancies":
        return factory(
            repository=repository,
            user=resolve("users"),
            vacancy=resolve("vacancies"),
        )
    return factory(repository=repository)
