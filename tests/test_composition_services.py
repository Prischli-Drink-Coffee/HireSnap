"""service/composition/: the wiring tables and the container that memoises them.

WHY THESE ARE TESTS AND NOT PART OF AN INTEGRATION RUN: composition is the one place where a
mistake is silent. A swapped collaborator (the matching slice handed the vacancies service
where it wanted the candidates service) type-checks, imports, and fails only for the user who
uploads a video. The table below is therefore asserted argument by argument, with the class
loader and the persistence loader faked out, so the test states the wiring rather than
observing whatever the slices happen to accept today.

WHY THE CONTAINER GETS ITS OWN TESTS: three properties are invisible at runtime and each of
them is a production incident when lost - a service is built ONCE per process (a second
UserService would hold its own cache), a build that FAILED is not remembered (a redis that was
down at boot must be retried, not cached as broken), and nothing is built until a route asks
(the metric here is MySQL: /server/health must answer with the database down). The last one is
asserted against sys.modules, because "lazy" means "the slice module was not even imported".

WHAT IS DELIBERATELY NOT ASSERTED: the concrete class behind each entry of SERVICE_MODULES.
test_backend_structure.py and the layout guard relate those names to the directories on disk;
duplicating the list here would mean three places to edit for one rename.
"""

from __future__ import annotations

import os
import sys

import pytest

from service.composition import container as container_module
from service.composition import infra, services, state
from service.composition.container import Container


class RecordingFactory:
    """A stand-in for a slice class: records how it was constructed."""

    def __init__(self, label="fake"):
        self.label = label
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"built": self.label, "kwargs": kwargs}


class Resolver:
    """A stand-in for Container.resolve: returns a named marker, never a real service."""

    def __init__(self):
        self.asked = []

    def __call__(self, domain):
        self.asked.append(domain)
        return "service:" + domain


@pytest.fixture()
def patched_loaders(monkeypatch):
    """Replace the two loaders so a wiring test never imports a slice module."""

    def install(factory=None, repository="repository-module"):
        record = {"factory": factory or RecordingFactory(), "repository": repository}
        monkeypatch.setattr(services, "load_class", lambda domain: record["factory"])
        monkeypatch.setattr(services, "load_repository", lambda domain: record["repository"])
        return record

    return install


@pytest.fixture()
def fresh_container():
    """A container of its own, so the process-wide one is never filled by a test."""
    return Container()


# --- the wiring table -------------------------------------------------------
def test_build_order_builds_every_collaborator_before_the_slice_that_needs_it():
    """BUILD_ORDER is a topological order, not a preference: matching asks for nine slices."""
    order = services.BUILD_ORDER
    assert sorted(order) == sorted(services.SERVICE_MODULES), "every slice must be built"

    dependents = {
        "auth": ["users"],
        "personality_vacancies": ["personalities", "vacancies"],
        "personality_candidates": ["personalities", "users"],
        "candidate_vacancies": ["users", "vacancies"],
    }
    for domain, needs in dependents.items():
        for needed in needs:
            assert order.index(needed) < order.index(domain), (
                "%s is built before %s, which asks for it" % (needed, domain)
            )
    assert order[-1] == "matching", "matching pulls in nine slices, so it is built last"


def test_persistence_modules_cover_exactly_the_slices_that_own_a_table():
    """auth, matching and jobs have no table, so load_repository must answer None for them."""
    tableless = {"auth", "matching", "jobs"}
    assert set(services.PERSISTENCE_MODULES) == set(services.SERVICE_MODULES) - tableless
    for domain, module_name in services.PERSISTENCE_MODULES.items():
        assert module_name == "service.services.%s.persistence" % domain


def test_plain_slice_receives_only_its_repository(patched_loaders):
    """A one-table slice is constructed with its repository and nothing else."""
    record = patched_loaders()
    built = services.build_service("files", Resolver())

    assert built == {"built": "fake", "kwargs": {"repository": "repository-module"}}
    assert record["factory"].calls == [{"repository": "repository-module"}]


def test_auth_receives_the_users_service_and_no_repository(patched_loaders):
    """Auth has no table of its own: it validates the password against the users slice."""
    record = patched_loaders(repository=None)
    services.build_service("auth", Resolver())

    assert record["factory"].calls == [{"user": "service:users"}]


def test_jobs_receives_nothing(patched_loaders):
    """The job slice talks to the broker adapter it builds itself, so the factory takes no args."""
    record = patched_loaders(repository=None)
    services.build_service("jobs", Resolver())

    assert record["factory"].calls == [{}]


def test_matching_receives_all_nine_collaborators_by_name(patched_loaders):
    """The nine names are a contract between build_service and MatchingService.__init__.

    The candidates slice is resolved under its SLICE name while the parameter is
    `info_candidates`, which is exactly the kind of rename a reader gets wrong.
    """
    record = patched_loaders(repository=None)
    services.build_service("matching", Resolver())

    kwargs = record["factory"].calls[0]
    assert kwargs == {
        "files": "service:files",
        "embeddings": "service:embeddings",
        "personalities": "service:personalities",
        "users": "service:users",
        "vacancies": "service:vacancies",
        "info_candidates": "service:candidates",
        "personality_candidates": "service:personality_candidates",
        "personality_vacancies": "service:personality_vacancies",
        "candidate_vacancies": "service:candidate_vacancies",
    }


def test_link_slices_receive_their_repository_and_both_siblings(patched_loaders):
    """A link table validates both foreign keys, so both services must arrive."""
    expected = {
        "personality_vacancies": {
            "repository": "repository-module",
            "personality": "service:personalities",
            "vacancy": "service:vacancies",
        },
        "personality_candidates": {
            "repository": "repository-module",
            "personality": "service:personalities",
            "user": "service:users",
        },
        "candidate_vacancies": {
            "repository": "repository-module",
            "user": "service:users",
            "vacancy": "service:vacancies",
        },
    }
    for domain, kwargs in expected.items():
        record = patched_loaders()
        services.build_service(domain, Resolver())
        assert record["factory"].calls[0] == kwargs, domain


def test_load_repository_answers_none_for_the_tableless_slices():
    for domain in ("auth", "matching", "jobs"):
        assert services.load_repository(domain) is None


def test_load_repository_imports_the_persistence_package_of_a_table_slice():
    module = services.load_repository("users")

    assert module.__name__ == "service.services.users.persistence"


def test_load_class_names_the_known_slices_when_the_domain_is_a_typo():
    """A KeyError with a list of names is a five-second fix; a bare KeyError is a search."""
    with pytest.raises(KeyError) as excinfo:
        services.load_class("user")

    message = str(excinfo.value)
    assert "unknown service 'user'" in message
    assert "known slices:" in message
    assert "users" in message


def test_load_class_explains_a_renamed_class(monkeypatch):
    """The ImportError has to say WHICH table is stale, because the slice kept working."""
    monkeypatch.setitem(services.SERVICE_MODULES, "users", ("service.settings", "NoSuchClass"))

    with pytest.raises(ImportError) as excinfo:
        services.load_class("users")

    message = str(excinfo.value)
    assert "service.settings defines no NoSuchClass" in message
    assert "the slice moved and this table did not" in message


# --- the container ----------------------------------------------------------
def test_a_service_is_built_once_and_the_same_instance_is_returned(fresh_container):
    """A second UserService would hold a second cache and a second repository."""
    first = fresh_container.get_service("users")
    second = fresh_container.get_service("users")

    assert first is second


def test_a_failed_build_is_not_cached(fresh_container):
    """Redis down at boot must not be remembered as broken for the life of the process."""
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("redis is not up yet")
        return "cache-after-retry"

    with pytest.raises(RuntimeError):
        fresh_container._singleton(fresh_container._platform, "cache", flaky)

    assert fresh_container.get_infra.__self__ is fresh_container
    assert fresh_container._singleton(fresh_container._platform, "cache", flaky) == (
        "cache-after-retry"
    )
    assert len(attempts) == 2


def test_the_container_hands_its_own_resolver_to_build_service(fresh_container, monkeypatch):
    """A slice must never import the container; it receives a callable instead.

    The resolver is the container's own bound method, so asking for a collaborator goes
    through the same memo store and a slice cannot build a second copy of a sibling.
    """
    seen = {}

    def record(domain, resolve):
        seen["domain"] = domain
        seen["resolve"] = resolve
        return "built:" + domain

    monkeypatch.setattr(services, "build_service", record)

    assert fresh_container.get_service("users") == "built:users"
    assert seen["domain"] == "users"
    assert seen["resolve"].__self__ is fresh_container
    assert seen["resolve"].__func__ is Container.resolve


def test_unknown_infrastructure_port_lists_the_known_ones(fresh_container):
    with pytest.raises(KeyError) as excinfo:
        fresh_container.get_infra("cachee")

    message = str(excinfo.value)
    assert "unknown infrastructure port 'cachee'" in message
    assert "known ports:" in message
    assert "mailer" in message


def test_describe_reports_what_is_built_and_reset_forgets_it(fresh_container, monkeypatch):
    monkeypatch.setattr(services, "build_service", lambda domain, resolve: object())
    assert fresh_container.describe() == {"services": [], "platform": []}

    fresh_container.get_service("users")
    fresh_container.get_cache()
    described = fresh_container.describe()
    assert described["services"] == ["users"]
    assert "cache" in described["platform"]

    fresh_container.reset()
    assert fresh_container.describe() == {"services": [], "platform": []}


def test_build_all_builds_every_slice_in_dependency_order(monkeypatch):
    """build_all is the warm-start probe; it must not skip a slice or reorder the graph."""
    built = []

    def fake_build(domain, resolve):
        built.append(domain)
        return domain

    monkeypatch.setattr(services, "build_service", fake_build)
    monkeypatch.setattr(services, "load_repository", lambda domain: None)

    container = Container()
    result = container.build_all()

    assert built == list(services.BUILD_ORDER)
    assert sorted(result) == sorted(services.BUILD_ORDER)


# --- the platform factories -------------------------------------------------
def test_infra_factories_cover_the_ports_the_state_module_exposes():
    """Adding a port is one entry here plus one accessor; the two lists must agree."""
    assert set(infra.INFRA_FACTORIES) == {
        "cache",
        "storage",
        "mailer",
        "ml_client",
        "dispatcher",
    }
    for name, builder in infra.INFRA_FACTORIES.items():
        assert callable(builder), name


def test_the_builders_return_the_documented_objects_without_a_network():
    """Each builder is a function, not a constant, so calling it is when the port appears."""
    cache = infra.build_cache()
    assert cache.describe()["backend"] in {"memory", "redis", "noop"}

    mailer = infra.build_mailer()
    assert "backend" in mailer.describe()

    transport = infra.build_ml_client()
    assert transport.__name__ == "service.infrastructure.ml_sidecar"

    dispatcher = infra.build_job_dispatcher()
    assert dispatcher.configured() in (True, False)


def test_importing_the_composition_tables_imports_no_slice_and_no_database():
    """The table holds strings; resolving one is what imports a slice. Proven in a subprocess.

    A checker that ran inside this process would see the modules this very test file already
    imported, so the probe starts from an empty interpreter.
    """
    import subprocess
    import sys as sys_module
    from pathlib import Path

    backend_root = Path(__file__).resolve().parent.parent
    program = (
        "import sys;"
        "import service.composition.services;"
        "import service.composition.infra;"
        "slices = [name for name in sys.modules"
        " if name.startswith('service.services.')];"
        "print('slices=%s' % len(slices));"
        "print('mysql=%s' % ('service.infrastructure.database.connection' in sys.modules))"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(backend_root)
    for name in [key for key in environment if key.startswith(("DB_", "MYSQL_"))]:
        del environment[name]
    completed = subprocess.run(
        [sys_module.executable, "-c", program],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr
    assert "slices=0" in completed.stdout, completed.stdout
    assert "mysql=False" in completed.stdout, completed.stdout


# --- the access surface -----------------------------------------------------
ACCESSOR_DOMAINS = {
    "get_auth_service": "auth",
    "get_user_service": "users",
    "get_info_candidate_service": "candidates",
    "get_vacancy_service": "vacancies",
    "get_magic_service": "magics",
    "get_personality_service": "personalities",
    "get_personality_vacancy_service": "personality_vacancies",
    "get_personality_candidate_service": "personality_candidates",
    "get_candidate_vacancy_service": "candidate_vacancies",
    "get_file_service": "files",
    "get_embedding_service": "embeddings",
    "get_matching_service": "matching",
    "get_job_service": "jobs",
}


@pytest.mark.parametrize("accessor, domain", sorted(ACCESSOR_DOMAINS.items()))
def test_each_accessor_asks_for_its_own_domain(accessor, domain, monkeypatch):
    """The accessor name and the domain string drift apart silently: the router type-hints
    nothing, so a copy-paste returns the vacancies service to the magics route."""
    asked = []

    class FakeContainer:
        def get_service(self, name):
            asked.append(name)
            return "service:" + name

    monkeypatch.setattr(state, "get_container", lambda: FakeContainer())
    assert getattr(state, accessor)() == "service:" + domain
    assert asked == [domain]


def test_slice_services_table_is_re_exported_for_the_structure_gate():
    assert state.SLICE_SERVICES is services.SERVICE_MODULES


def test_reset_state_drops_the_container(monkeypatch):
    """A test that filled the container must not leak a fake into the next one."""
    calls = []
    monkeypatch.setattr(state, "reset_container", lambda: calls.append(1))

    state.reset_state()

    assert calls == [1]


def test_describe_state_answers_from_the_container(monkeypatch):
    class FakeContainer:
        def describe(self):
            return {"services": ["users"], "platform": ["cache"]}

    monkeypatch.setattr(state, "get_container", lambda: FakeContainer())

    assert state.describe_state() == {"services": ["users"], "platform": ["cache"]}


def test_the_module_container_is_reused_and_reset_container_replaces_it(monkeypatch):
    """The process-wide accessor is what a router reaches; it must be one object, then none."""
    monkeypatch.setattr(container_module, "_CONTAINER", None)

    first = container_module.get_container()
    assert container_module.get_container() is first

    assert first.describe() == {"services": [], "platform": []}
    container_module.reset_container()

    second = container_module.get_container()
    assert second is not first


def test_get_ml_client_hands_out_the_transport_module_not_an_instance():
    """The transport is stateless, so the port IS the module; wrapping it would add nothing."""
    from service.infrastructure import ml_sidecar

    assert infra.build_ml_client() is ml_sidecar
    assert isinstance(sys.modules["service.infrastructure.ml_sidecar"], type(ml_sidecar))
