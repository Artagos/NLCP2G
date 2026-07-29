"""The sandbox worker: runs somewhere Docker exists, calls back when done.

The bot, the scheduler and the API can live on a small always-on host. The
sandbox cannot — it needs a Docker daemon, which free hosting tiers do not give
you. So execution is split off: this process claims `run_jobs` from the shared
database, runs the C++ in the real container with all its limits, and POSTs the
result to the run-complete webhook.

Two things fall out of that split, both good:

  * the webhook becomes load-bearing rather than decorative — it is how the
    verdict gets back at all;
  * if no worker is running, jobs simply queue. The learner was already told
    their run was accepted, the scheduler sees `run_in_flight` and stays quiet,
    and nothing is lost. Turning the worker off is a survivable state, not an
    outage.

    python -m backend.worker              # poll forever
    python -m backend.worker --once       # drain the queue and exit
"""
from __future__ import annotations

import argparse
import json
import logging
import time

import httpx

from . import hooks, memory
from .sandbox import run_cpp

log = logging.getLogger("cp_tutor.worker")

POLL_SECONDS = 3


def _payload(job: dict) -> dict:
    """Run the job's program and shape the result for the webhook."""
    try:
        problem = hooks.problem_for_job(job)
    except Exception as exc:
        log.error("job %s: cannot resolve problem %s (%s)",
                  job["job_id"], job["problem_key"], exc)
        return {"job_id": job["job_id"], "ok": False, "results": [],
                "infra_error": f"could not rebuild the problem: {exc}"}

    log.info("job %s: running %d test(s) for %s",
             job["job_id"], len(problem.tests), job["problem_key"])
    result = run_cpp(problem, job["cpp_source"])
    return {
        "job_id": job["job_id"],
        "ok": result.ok,
        "compile_error": result.compile_error,
        "results": result.results,
        "infra_error": result.infra_error,
    }


def report(payload: dict, url: str) -> bool:
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json",
               hooks.SIGNATURE_HEADER: hooks.sign(body)}
    try:
        resp = httpx.post(url, content=body, headers=headers, timeout=60)
    except Exception as exc:
        log.error("callback to %s failed: %s", url, exc)
        return False
    if resp.status_code >= 300:
        log.error("callback rejected (%s): %s", resp.status_code, resp.text[:200])
        return False
    log.info("job %s reported: %s", payload["job_id"], resp.json().get("status"))
    return True


def drain(url: str, limit: int | None = None) -> int:
    done = 0
    while limit is None or done < limit:
        job = memory.claim_job()
        if job is None:
            return done
        payload = _payload(job)
        if not report(payload, url):
            # put it back so a later pass (or another worker) retries it rather
            # than the learner never hearing anything
            memory.finish_job(job["job_id"], "pending")
        done += 1
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="NLCP2G sandbox worker.")
    parser.add_argument("--url", default=None,
                        help="run-complete webhook URL "
                             "(default $CP_TUTOR_WEBHOOK_URL or localhost:8000)")
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    parser.add_argument("--poll", type=int, default=POLL_SECONDS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    memory.init()

    import os
    url = args.url or os.environ.get(
        "CP_TUTOR_WEBHOOK_URL", "http://127.0.0.1:8000/hooks/run-complete")
    log.info("worker up, reporting to %s", url)

    if args.once:
        print(f"processed {drain(url)} job(s)")
        return
    while True:
        if drain(url) == 0:
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
