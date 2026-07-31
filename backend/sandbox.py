"""Host side of the sandbox. Given C++ source, build a work dir, run it in the
Docker sandbox against the problem's test suite, and return structured results.

Security: the container runs with --network=none, a memory cap, a CPU cap, and
an unprivileged user; runner.py additionally enforces per-test rlimits and
wall-clock timeouts. Generated code must never run outside this.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from .problem import Problem

IMAGE = os.environ.get("CP_TUTOR_SANDBOX_IMAGE", "cp-tutor-sandbox")

# --- running the sandbox when the worker is ITSELF in a container -------------
#
# On the host, the work dir is a temp directory bind-mounted straight in. That
# breaks under docker-compose: the worker talks to the *host* daemon through the
# mounted socket, so a `-v /tmp/xyz:/work` it asks for is resolved against the
# HOST filesystem, where /tmp/xyz does not exist. The sandbox would start with an
# empty /work, find no manifest, and every run would come back as an infra error.
#
# The fix is to stop naming a path at all. Both containers mount the same *named
# volume*, so the daemon resolves it identically for each of them, and the job
# gets a subdirectory inside it.
SANDBOX_VOLUME = os.environ.get("CP_TUTOR_SANDBOX_VOLUME", "")
# where that volume is mounted inside THIS process's container
SANDBOX_WORKROOT = os.environ.get("CP_TUTOR_SANDBOX_WORKROOT", "")
# where it gets mounted inside the sandbox container
_GUEST_WORKROOT = "/workroot"


@dataclass
class RunResult:
    ok: bool                     # True unless a compile error occurred
    compile_error: str | None
    results: list[dict]          # per-test dicts as produced by runner.py
    # set when the sandbox itself couldn't run (Docker down, etc.) — NOT the
    # user's fault, so it must not be reported as a compile error
    infra_error: str | None = None

    @property
    def all_accepted(self) -> bool:
        return self.ok and bool(self.results) and all(
            r["verdict"] == "AC" for r in self.results
        )

    def first_failure(self) -> dict | None:
        for r in self.results:
            if r["verdict"] != "AC":
                return r
        return None


def run_cpp(problem: Problem, source: str) -> RunResult:
    """Compile and run `source` against the given problem's test suite."""
    # In volume mode the work dir must live inside the shared volume, not in the
    # container's own /tmp — that is the whole point of it.
    with tempfile.TemporaryDirectory(prefix="cp_tutor_",
                                     dir=SANDBOX_WORKROOT or None) as workdir:
        if SANDBOX_VOLUME:
            # mkdtemp gives 0700 and we are root here; the sandbox runs as uid
            # 10001 and has to write results.json back into this directory.
            # Widening it is safe: it is a private volume holding one job.
            os.chmod(workdir, 0o777)

        tests_dir = os.path.join(workdir, "tests")
        os.makedirs(tests_dir, exist_ok=True)

        with open(os.path.join(workdir, "solution.cpp"), "w", newline="\n") as f:
            f.write(source)

        manifest_tests = []
        for t in problem.tests:
            in_rel = f"tests/{t.name}.in"
            out_rel = f"tests/{t.name}.out"
            with open(os.path.join(workdir, in_rel), "w", newline="\n") as f:
                f.write(t.stdin)
            with open(os.path.join(workdir, out_rel), "w", newline="\n") as f:
                f.write(t.expected_stdout)
            manifest_tests.append(
                {"name": t.name, "input_file": in_rel, "expected_file": out_rel}
            )

        manifest = {
            "time_limit_ms": problem.time_limit_ms,
            "memory_limit_mb": problem.memory_limit_mb,
            "tests": manifest_tests,
        }
        with open(os.path.join(workdir, "manifest.json"), "w") as f:
            json.dump(manifest, f)

        proc = _invoke_docker(problem, workdir)

        results_path = os.path.join(workdir, "results.json")
        if not os.path.exists(results_path):
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            return RunResult(
                ok=False,
                compile_error=None,
                results=[],
                infra_error=(
                    "The sandbox couldn't run (is Docker running?). "
                    f"Details: {detail}" if detail else
                    "The sandbox couldn't run (is Docker running?)."
                ),
            )
        payload = json.load(open(results_path))

    if "compile_error" in payload:
        return RunResult(ok=False, compile_error=payload["compile_error"], results=[])
    return RunResult(ok=True, compile_error=None, results=payload["results"])


def docker_cmd(problem: Problem, workdir: str) -> list[str]:
    """The exact `docker run` argv. Split out from the call so the two mount
    strategies can be asserted in a test without a daemon."""
    # Container-level caps; runner.py enforces finer per-test limits inside.
    container_mem_mb = problem.memory_limit_mb + 128
    cmd = [
        "docker", "run", "--rm",
        "--network=none",
        f"--memory={container_mem_mb}m",
        "--cpus=1",
        "--pids-limit=64",
    ]

    if SANDBOX_VOLUME:
        # Sibling container: name the shared volume, never a path. The image's
        # ENTRYPOINT hardcodes /work, so override it to point runner.py at this
        # job's subdirectory instead.
        job = os.path.basename(workdir.rstrip("/\\"))
        cmd += ["-v", f"{SANDBOX_VOLUME}:{_GUEST_WORKROOT}",
                "--entrypoint", "python3", IMAGE,
                "/runner.py", f"{_GUEST_WORKROOT}/{job}"]
    else:
        cmd += ["-v", f"{os.path.abspath(workdir)}:/work", IMAGE]

    return cmd


def _invoke_docker(problem: Problem, workdir: str) -> subprocess.CompletedProcess:
    # Generous overall ceiling; per-test timeouts are handled inside the container.
    overall_timeout = (problem.time_limit_ms / 1000.0) * len(problem.tests) + 90
    return subprocess.run(docker_cmd(problem, workdir), timeout=overall_timeout,
                          capture_output=True, text=True)
