"""The run-complete webhook: the interactive-mode trigger.

Why this exists, rather than being invented for the homework. A turn that ends in
a sandbox run takes twenty to sixty seconds. Over HTTP the browser can wait; over
a chat channel it cannot — the learner is left staring at nothing, and Telegram
gives us no way to say "still working". So the run is handed to a worker process,
the turn ends immediately with an acknowledgement, and when the sandbox finishes
**the worker calls back here**. This handler then composes the verdict and pushes
it into the chat, unprompted by any message.

That makes it a trigger the learner did not send and the bot did not schedule:
an external process reporting an event.

It is also a door open on the internet, so every request is authenticated with an
HMAC over the raw body. An unsigned or mis-signed payload is rejected before the
job is even looked up.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

from . import memory, state, translator
from .sandbox import RunResult

log = logging.getLogger("cp_tutor.hooks")

SIGNATURE_HEADER = "x-nlcp2g-signature"


def secret() -> str:
    """Shared secret between the worker and the API.

    Defaults to a fixed development value so the system runs out of the box, but
    an unset secret in production means anyone who finds the URL can post fake
    verdicts into a learner's chat — so it is loud about it.
    """
    value = os.environ.get("CP_TUTOR_WEBHOOK_SECRET", "")
    if not value:
        log.warning("CP_TUTOR_WEBHOOK_SECRET is unset — using the development default")
        return "dev-insecure-secret"
    return value


def sign(body: bytes, key: str | None = None) -> str:
    digest = hmac.new((key or secret()).encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify(body: bytes, header: str | None, key: str | None = None) -> bool:
    if not header:
        return False
    # compare_digest, not ==, so a wrong signature can't be recovered byte by
    # byte from response timing
    return hmac.compare_digest(sign(body, key), header.strip())


def result_from_payload(payload: dict) -> RunResult:
    return RunResult(
        ok=bool(payload.get("ok")),
        compile_error=payload.get("compile_error"),
        results=payload.get("results") or [],
        infra_error=payload.get("infra_error"),
    )


def handle_completion(payload: dict) -> dict:
    """Compose the verdict for a finished job and return what to deliver.

    Returns {"status": ..., "chat_id": ..., "text": ...}. Delivery is the
    caller's job so this stays synchronous and testable.
    """
    job_id = payload.get("job_id") or ""
    job = memory.get_job(job_id)
    if job is None:
        return {"status": "unknown_job"}
    if job["status"] not in ("running", "pending"):
        # the worker retried, or two workers raced — don't double-report
        return {"status": "already_finished"}

    run = result_from_payload(payload)
    outcome = translator.finish_run(
        job["approach_summary"], job["cpp_source"], run,
        critic_status=job.get("critic_status") or "",
        critic_rounds=int(job.get("critic_rounds") or 0),
        violations=job.get("violations") or [],
        run_id=job["run_id"],
    )

    sid = f"u:{job['user_id']}"
    key = job["problem_key"]
    row = memory.seen_row(sid, key) or {}
    attempt_no = memory.record_attempt(sid, key, row.get("name") or key,
                                       row.get("rating"), job["described_approach"],
                                       outcome.verdict)

    memory.add_message(sid, "assistant", outcome.reply, "solution", key)
    memory.log_run(user_id=job["user_id"], sid=sid, problem_key=key,
                   problem_name=row.get("name"), intent="solution",
                   user_message=job["described_approach"], reply=outcome.reply,
                   verdict=outcome.verdict, run_id=job["run_id"])
    memory.finish_job(job_id, "done")

    header = f"[attempt {attempt_no} · {outcome.verdict}]\n\n"
    return {"status": "delivered", "chat_id": job["chat_id"],
            "text": header + outcome.reply, "verdict": outcome.verdict,
            "job_id": job_id}


def problem_for_job(job: dict):
    """Used by the worker to rebuild the test suite from the job's problem key."""
    return state.problem_for_key(job["problem_key"])
