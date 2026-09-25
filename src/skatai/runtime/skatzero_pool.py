from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import select
import subprocess
import threading
import time
from typing import Sequence

from skatai.runtime.interface import SkatAIInterfaceError


class _WorkerTransportError(SkatAIInterfaceError):
    pass


class _WorkerRequestError(SkatAIInterfaceError):
    pass


def default_threads_per_worker(workers: int) -> int:
    if int(workers) < 1:
        raise ValueError("SKATZERO_POOL_WORKERS_LT_ONE")
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = int(os.cpu_count() or 1)
    return max(1, cpus // int(workers))


class _WarmWorker:
    def __init__(
        self,
        *,
        skatzero_root: Path,
        python_executable: Path,
        startup_timeout_s: float,
        worker_id: int,
        torch_threads: int,
        torch_interop_threads: int,
    ) -> None:
        self.skatzero_root = Path(skatzero_root)
        self.python_executable = Path(python_executable)
        self.worker_id = int(worker_id)
        self._request_seq = 0
        src_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        prior = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(src_root) + (os.pathsep + prior if prior else "")
        thread_value = str(int(torch_threads))
        env["SKATZERO_TORCH_THREADS"] = thread_value
        env["SKATZERO_TORCH_INTEROP_THREADS"] = str(
            int(torch_interop_threads)
        )
        # Keep native math runtimes aligned with PyTorch's per-worker budget.
        env["OMP_NUM_THREADS"] = thread_value
        env["MKL_NUM_THREADS"] = thread_value
        env["OPENBLAS_NUM_THREADS"] = thread_value
        env["NUMEXPR_NUM_THREADS"] = thread_value
        self.proc = subprocess.Popen(
            [
                str(self.python_executable),
                "-m",
                "skatai.runtime.skatzero_worker",
                "--root",
                str(self.skatzero_root),
            ],
            cwd=str(self.skatzero_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        ready = self._read_json_line(startup_timeout_s)
        if ready.get("ready") is not True:
            self.close()
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_NOT_READY:{self.worker_id}"
            )

    def _read_json_line(self, timeout_s: float) -> dict:
        if self.proc.stdout is None:
            raise _WorkerTransportError("SKATZERO_WARM_WORKER_STDOUT_MISSING")
        if self.proc.poll() is not None:
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_EXITED:{self.worker_id}:{self.proc.returncode}"
            )
        ready, _, _ = select.select([self.proc.stdout], [], [], float(timeout_s))
        if not ready:
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_TIMEOUT:{self.worker_id}"
            )
        line = self.proc.stdout.readline()
        if not line:
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_EOF:{self.worker_id}"
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_BAD_JSON:{self.worker_id}"
            ) from exc
        if not isinstance(payload, dict):
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_NONOBJECT:{self.worker_id}"
            )
        return payload

    def request(self, args: Sequence[str], *, timeout_s: float) -> list[str]:
        if self.proc.stdin is None:
            raise _WorkerTransportError("SKATZERO_WARM_WORKER_STDIN_MISSING")
        self._request_seq += 1
        req_id = f"{self.worker_id}:{self._request_seq}"
        request = {
            "id": req_id,
            "args": [str(x) for x in args],
        }
        try:
            self.proc.stdin.write(
                json.dumps(request, separators=(",", ":")) + "\n"
            )
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_WRITE_FAILED:{self.worker_id}"
            ) from exc
        response = self._read_json_line(timeout_s)
        if response.get("id") != req_id:
            raise _WorkerTransportError(
                f"SKATZERO_WARM_WORKER_ID_MISMATCH:{self.worker_id}"
            )
        if response.get("ok") is not True:
            raise _WorkerRequestError(
                "SKATZERO_WARM_WORKER_REQUEST_FAILED:"
                + str(response.get("error") or "unknown")[:300]
            )
        lines = response.get("lines")
        if not isinstance(lines, list) or not lines or not all(
            isinstance(x, str) for x in lines
        ):
            raise _WorkerRequestError(
                f"SKATZERO_WARM_WORKER_EMPTY_OUTPUT:{self.worker_id}"
            )
        return list(lines)

    def close(self) -> None:
        proc = getattr(self, "proc", None)
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        self.proc = None


class PersistentSkatZeroPool:
    """Pool of warm SkatZero workers with one request in flight per process."""

    def __init__(
        self,
        skatzero_root: Path,
        python_executable: Path,
        *,
        workers: int = 1,
        startup_timeout_s: float = 180.0,
        torch_threads: int | None = None,
        torch_interop_threads: int = 1,
    ) -> None:
        if workers < 1:
            raise ValueError("SKATZERO_POOL_WORKERS_LT_ONE")
        self.skatzero_root = Path(skatzero_root)
        self.python_executable = Path(python_executable)
        self.workers = int(workers)
        self.startup_timeout_s = float(startup_timeout_s)
        self.torch_threads = (
            default_threads_per_worker(self.workers)
            if torch_threads is None
            else int(torch_threads)
        )
        self.torch_interop_threads = int(torch_interop_threads)
        if self.torch_threads < 1 or self.torch_interop_threads < 1:
            raise ValueError("BAD_SKATZERO_POOL_THREAD_CONFIG")
        self._available: queue.Queue[_WarmWorker] = queue.Queue()
        self._all: list[_WarmWorker] = []
        self._replace_lock = threading.Lock()
        self._closed = False
        for worker_id in range(self.workers):
            worker = self._new_worker(worker_id)
            self._all.append(worker)
            self._available.put(worker)

    def _new_worker(self, worker_id: int) -> _WarmWorker:
        return _WarmWorker(
            skatzero_root=self.skatzero_root,
            python_executable=self.python_executable,
            startup_timeout_s=self.startup_timeout_s,
            worker_id=worker_id,
            torch_threads=self.torch_threads,
            torch_interop_threads=self.torch_interop_threads,
        )

    def _replace(self, failed: _WarmWorker) -> _WarmWorker:
        with self._replace_lock:
            failed.close()
            try:
                index = self._all.index(failed)
            except ValueError:
                index = 0
            replacement = self._new_worker(index)
            if failed in self._all:
                self._all[index] = replacement
            return replacement

    def run(self, args: Sequence[str], *, timeout_s: float) -> list[str]:
        if self._closed:
            raise SkatAIInterfaceError("SKATZERO_POOL_CLOSED")
        try:
            worker = self._available.get(timeout=max(1.0, float(timeout_s)))
        except queue.Empty as exc:
            raise SkatAIInterfaceError("SKATZERO_POOL_ACQUIRE_TIMEOUT") from exc

        return_worker: _WarmWorker | None = None
        try:
            try:
                result = worker.request(args, timeout_s=timeout_s)
                return_worker = worker
                return result
            except _WorkerRequestError:
                # The worker is healthy; this request itself is invalid or
                # failed deterministically. Never churn/retry it.
                return_worker = worker
                raise
            except _WorkerTransportError:
                # No ISS effect exists yet. Replace the broken local inference
                # process and retry this request exactly once.
                replacement = self._replace(worker)
                try:
                    result = replacement.request(args, timeout_s=timeout_s)
                    return_worker = replacement
                    return result
                except _WorkerRequestError:
                    return_worker = replacement
                    raise
                except _WorkerTransportError:
                    # Restore pool capacity for later requests, but do not
                    # execute this logical request a third time.
                    return_worker = self._replace(replacement)
                    raise
        finally:
            if return_worker is not None and not self._closed:
                self._available.put(return_worker)

    def close(self) -> None:
        self._closed = True
        for worker in list(self._all):
            worker.close()
        self._all.clear()
        while True:
            try:
                self._available.get_nowait()
            except queue.Empty:
                break

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
