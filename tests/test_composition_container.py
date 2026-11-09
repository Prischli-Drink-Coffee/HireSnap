"""The container and its access surface: build once, never leak a failed build.

WHY THIS IS A SEPARATE FILE FROM test_composition_seams.py: that one proves a slice
REACHES the factory. This one is about the factory itself - the object graph, the lock,
the memoisation, and the rule that a port which failed to build is not remembered as
broken for the life of the process.

The container is the one module every request passes through and the one module a
misconfiguration would turn into an import-time crash, so its failure behaviour is worth
pinning rather than observing.
"""

from __future__ import annotations

import threading
import time

import pytest

from service.composition import infra, state
from service.composition.container import Container, get_container, reset_container


@pytest.fixture(autouse=True)
def fresh_container():
    """No test may inherit a half-built graph from another one."""
    reset_container()
    yield
    reset_container()


# ---------------------------------------------------------------------------
# Building and memoising
# ---------------------------------------------------------------------------


def test_a_port_is_built_once_and_handed_out_again():
    built = []

    def factory():
        built.append(1)
        return object()

    graph = Container()
    graph._platform.clear()
    first = graph._singleton(graph._platform, "probe", factory)
    second = graph._singleton(graph._platform, "probe", factory)

    assert first is second
    assert built == [1]


def test_a_failed_build_is_not_remembered():
    """A redis that was down at boot must not be remembered as down for the process.

    This is the difference between a cache that recovers when redis comes back and one
    that stays broken until the container is restarted - and the second would look like
    a working deployment with a cold cache.
    """
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("redis is not up yet")
        return "the real thing"

    graph = Container()
    graph._platform.clear()

    with pytest.raises(RuntimeError):
        graph._singleton(graph._platform, "probe", flaky)

    assert graph._singleton(graph._platform, "probe", flaky) == "the real thing"
    assert len(attempts) == 2


def test_an_unknown_port_names_the_ones_that_exist():
    graph = Container()

    with pytest.raises(KeyError) as failure:
        graph.get_infra("nonsense")

    message = str(failure.value)
    assert "unknown infrastructure port" in message
    for known in ("cache", "storage", "mailer", "ml_client", "dispatcher"):
        assert known in message


def test_every_declared_port_can_be_asked_for_by_its_own_accessor():
    """The five accessors are spelling, not extra wiring: each must reach the table."""
    graph = Container()
    accessors = {
        "cache": graph.get_cache,
        "storage": graph.get_storage,
        "mailer": graph.get_mailer,
        "ml_client": graph.get_ml_client,
        "dispatcher": graph.get_dispatcher,
    }

    assert set(accessors) == set(infra.INFRA_FACTORIES)
    for name, accessor in accessors.items():
        assert accessor() is graph.get_infra(name)


def test_the_ml_client_port_is_the_module_itself(monkeypatch):
    """It is stateless, so the port hands out the module and not a wrapper object."""
    from service.infrastructure import ml_sidecar

    assert Container().get_ml_client() is ml_sidecar


def test_an_unknown_slice_names_the_ones_that_exist():
    graph = Container()

    with pytest.raises(KeyError) as failure:
        graph.get_service("nonsense")

    assert "unknown service 'nonsense'" in str(failure.value)


def test_building_all_walks_the_declared_order_and_returns_each_slice():
    from service.composition import services as services_module

    graph = Container()
    built = graph.build_all()

    assert list(built) == list(services_module.BUILD_ORDER)
    assert sorted(graph.describe()["services"]) == sorted(services_module.BUILD_ORDER)


def test_describe_reports_what_is_built_and_not_what_could_be():
    graph = Container()
    assert graph.describe() == {"services": [], "platform": []}

    graph.get_ml_client()

    assert graph.describe() == {"services": [], "platform": ["ml_client"]}


def test_reset_forgets_the_platform_and_the_slices():
    graph = Container()
    graph.get_ml_client()
    graph.reset()

    assert graph.describe() == {"services": [], "platform": []}


def test_the_container_is_one_object_per_process():
    assert get_container() is get_container()


def test_reset_container_drops_it_so_the_next_call_builds_a_new_one():
    first = get_container()
    reset_container()
    second = get_container()

    assert first is not second
    assert first.describe() == {"services": [], "platform": []}


def test_the_lock_is_reentrant_because_building_a_slice_asks_for_nine_others():
    """A plain Lock would deadlock on the first nested resolve - this is a regression test."""
    graph = Container()

    assert graph._lock.acquire(timeout=1)
    try:
        assert graph._lock.acquire(timeout=1)
        graph._lock.release()
    finally:
        graph._lock.release()


# ---------------------------------------------------------------------------
# The access surface
# ---------------------------------------------------------------------------


def test_the_accessors_are_thin_and_named_for_their_slice():
    """One accessor per domain, each answering the same object the generic form does."""
    from service.composition import services as services_module

    named = {
        "auth": state.get_auth_service,
        "users": state.get_user_service,
        "candidates": state.get_info_candidate_service,
        "vacancies": state.get_vacancy_service,
        "magics": state.get_magic_service,
        "personalities": state.get_personality_service,
        "personality_vacancies": state.get_personality_vacancy_service,
        "personality_candidates": state.get_personality_candidate_service,
        "candidate_vacancies": state.get_candidate_vacancy_service,
        "files": state.get_file_service,
        "embeddings": state.get_embedding_service,
        "matching": state.get_matching_service,
        "jobs": state.get_job_service,
    }

    assert set(named) == set(services_module.SERVICE_MODULES)
    for domain, accessor in named.items():
        assert accessor() is state.get_service(domain)


def test_the_platform_accessors_reach_the_container(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(state.get_container(), "get_cache", lambda: sentinel)
    monkeypatch.setattr(state.get_container(), "get_storage", lambda: sentinel)
    monkeypatch.setattr(state.get_container(), "get_mailer", lambda: sentinel)
    monkeypatch.setattr(state.get_container(), "get_ml_client", lambda: sentinel)
    monkeypatch.setattr(state.get_container(), "get_dispatcher", lambda: sentinel)

    assert state.get_cache() is sentinel
    assert state.get_storage() is sentinel
    assert state.get_mailer() is sentinel
    assert state.get_ml_client() is sentinel
    assert state.get_dispatcher() is sentinel


def test_describe_state_is_the_container_description():
    state.describe_state()

    assert state.describe_state() == get_container().describe()


def test_reset_state_resets_the_container():
    get_container().get_ml_client()
    state.reset_state()

    assert get_container().describe() == {"services": [], "platform": []}


def test_the_container_can_be_built_from_two_threads_without_racing():
    """Two workers hitting the first request at once must not build two graphs.

    The factory sleeps so the second thread is guaranteed to arrive while the first
    is still inside it - that is the only window in which a missing lock shows up as
    two builds (and, for the matching slice, as two MySQL clients).
    """
    graph = Container()
    built = []

    def slow_factory():
        time.sleep(0.1)
        built.append(1)
        return object()

    results = []

    def ask():
        results.append(graph._singleton(graph._platform, "probe", slow_factory))

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 2
    assert results[0] is results[1]
    assert built == [1]


def test_the_infra_builders_are_all_reachable_and_callable():
    """The table is the contract; every entry must be a zero-argument callable."""
    assert set(infra.INFRA_FACTORIES) == {"cache", "storage", "mailer", "ml_client", "dispatcher"}
    for name, builder in infra.INFRA_FACTORIES.items():
        assert callable(builder), name
