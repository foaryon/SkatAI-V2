from pathlib import Path

import pytest

import skatai.runtime.skatzero_pool as poolmod


def test_default_threads_per_worker_uses_affinity(monkeypatch):
    monkeypatch.setattr(poolmod.os, "sched_getaffinity", lambda pid: set(range(8)))
    assert poolmod.default_threads_per_worker(1) == 8
    assert poolmod.default_threads_per_worker(2) == 4
    assert poolmod.default_threads_per_worker(4) == 2
    assert poolmod.default_threads_per_worker(16) == 1


def test_pool_passes_thread_budget_to_workers(monkeypatch):
    created = []

    class FakeWorker:
        def __init__(self, **kwargs):
            created.append(kwargs)
            self.proc = None
        def close(self):
            pass

    monkeypatch.setattr(poolmod, "_WarmWorker", FakeWorker)
    monkeypatch.setattr(poolmod, "default_threads_per_worker", lambda workers: 3)

    p = poolmod.PersistentSkatZeroPool(
        Path("/tmp/root"),
        Path("/tmp/python"),
        workers=2,
        startup_timeout_s=7.0,
    )
    try:
        assert len(created) == 2
        assert [x["worker_id"] for x in created] == [0, 1]
        assert all(x["torch_threads"] == 3 for x in created)
        assert all(x["torch_interop_threads"] == 1 for x in created)
    finally:
        p.close()


def test_pool_rejects_bad_thread_budget(monkeypatch):
    monkeypatch.setattr(poolmod, "_WarmWorker", lambda **kwargs: None)
    with pytest.raises(ValueError, match="BAD_SKATZERO_POOL_THREAD_CONFIG"):
        poolmod.PersistentSkatZeroPool(
            Path("/tmp/root"),
            Path("/tmp/python"),
            workers=1,
            torch_threads=0,
        )


def _bare_pool_with_worker(worker):
    import queue

    p = object.__new__(poolmod.PersistentSkatZeroPool)
    p._closed = False
    p._available = queue.Queue()
    p._available.put(worker)
    p._all = [worker]
    p._replace_lock = __import__("threading").Lock()
    return p


def test_request_error_keeps_healthy_worker_and_does_not_replace(monkeypatch):
    class Worker:
        def request(self, args, *, timeout_s):
            raise poolmod._WorkerRequestError("bad request")

    worker = Worker()
    p = _bare_pool_with_worker(worker)
    monkeypatch.setattr(
        p,
        "_replace",
        lambda failed: (_ for _ in ()).throw(
            AssertionError("request errors must not replace worker")
        ),
    )

    with pytest.raises(poolmod._WorkerRequestError, match="bad request"):
        p.run(["BAD"], timeout_s=1.0)
    assert p._available.get_nowait() is worker


def test_transport_error_replaces_and_retries_once(monkeypatch):
    class Broken:
        def request(self, args, *, timeout_s):
            raise poolmod._WorkerTransportError("broken")

    class Healthy:
        def request(self, args, *, timeout_s):
            return ["OK"]

    broken = Broken()
    healthy = Healthy()
    p = _bare_pool_with_worker(broken)
    replacements = []

    def replace(failed):
        replacements.append(failed)
        return healthy

    monkeypatch.setattr(p, "_replace", replace)
    assert p.run(["X"], timeout_s=1.0) == ["OK"]
    assert replacements == [broken]
    assert p._available.get_nowait() is healthy


def test_second_transport_failure_repairs_capacity_without_third_request(monkeypatch):
    calls = []

    class Broken:
        def __init__(self, name):
            self.name = name
        def request(self, args, *, timeout_s):
            calls.append(self.name)
            raise poolmod._WorkerTransportError(self.name)

    first = Broken("first")
    second = Broken("second")

    class Standby:
        pass

    standby = Standby()
    p = _bare_pool_with_worker(first)
    replacements = iter([second, standby])
    monkeypatch.setattr(p, "_replace", lambda failed: next(replacements))

    with pytest.raises(poolmod._WorkerTransportError, match="second"):
        p.run(["X"], timeout_s=1.0)
    assert calls == ["first", "second"]
    assert p._available.get_nowait() is standby
