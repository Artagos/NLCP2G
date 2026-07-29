"""What happens when a message arrives while the agent is mid-turn.

A turn here is expensive and slow: the feasibility screen, code generation, and up
to two critic rounds — several chained model calls, ten seconds and up, during
which the learner can and will send something else. (On the channel the sandbox
run itself is *not* in this queue: it is handed to the worker process and its
verdict arrives by webhook. That is what keeps a sixty-second Docker run from
holding a queue slot at all.)

Of the four available strategies — drop the new input, queue it, interrupt the
current turn, or run both in parallel — this implements **per-learner FIFO**:
each learner has one worker processing their messages in arrival order, and
different learners never block each other.

Why, and what it costs:

  Why not drop. The input we would be discarding is a paragraph the learner wrote
  describing an algorithm. That is the single most expensive thing they produce,
  and it is not recoverable by retrying — they would have to type it again.
  Throwing it away to save them a wait is the wrong trade.

  Why not interrupt. Killing an in-flight turn means killing a Docker run that is
  partway through, after the LLM calls for it have already been paid for, and it
  leaves the attempt half-recorded — the attempt counter and the memory of what
  was tried would disagree with what actually ran.

  Why not parallel. Two concurrent turns for one learner would race on their
  attempt numbering and their current-problem pointer, and would double their
  share of a rate-limited free-tier model.

  What FIFO costs: **head-of-line blocking.** A two-second concept question sits
  behind a sixty-second sandbox run. That is a real cost, paid by the learner, and
  we pay it deliberately — with an immediate acknowledgement so the wait is
  visible rather than mysterious. The cap below keeps a runaway sender from
  building an unbounded backlog, and overflow is reported to them, never dropped
  in silence.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Literal

log = logging.getLogger("cp_tutor.queue")

Handler = Callable[[str, str, str], Awaitable[None]]
Status = Literal["started", "queued", "dropped"]

# Messages allowed to wait behind the one being processed, per learner.
DEFAULT_MAX_DEPTH = 3


class TurnQueue:
    """One in-order worker per learner."""

    def __init__(self, handler: Handler, max_depth: int = DEFAULT_MAX_DEPTH):
        self._handler = handler
        self._max_depth = max_depth
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._in_flight: set[str] = set()

    # ------------------------------------------------------------------ state

    def waiting(self, user_id: str) -> int:
        """Messages queued behind the current one."""
        q = self._queues.get(user_id)
        return q.qsize() if q else 0

    def busy(self, user_id: str) -> bool:
        return user_id in self._in_flight or self.waiting(user_id) > 0

    def idle(self) -> bool:
        return not self._in_flight and all(q.empty() for q in self._queues.values())

    # ----------------------------------------------------------------- submit

    async def submit(self, user_id: str, chat_id: str, text: str) -> tuple[Status, int]:
        """Accept a message. Returns (status, position).

        `position` is how many messages are ahead of this one — 0 when it starts
        immediately. The caller uses it to acknowledge honestly.
        """
        queue = self._queues.get(user_id)
        if queue is None:
            queue = self._queues[user_id] = asyncio.Queue()

        if self.waiting(user_id) >= self._max_depth:
            log.warning("queue full for %s; refusing message", user_id)
            return "dropped", self.waiting(user_id)

        was_busy = self.busy(user_id)
        ahead = self.waiting(user_id) + (1 if user_id in self._in_flight else 0)
        await queue.put((chat_id, text))

        worker = self._workers.get(user_id)
        if worker is None or worker.done():
            self._workers[user_id] = asyncio.create_task(
                self._run(user_id), name=f"turnqueue:{user_id}")

        return ("queued" if was_busy else "started"), ahead

    # ------------------------------------------------------------------ worker

    async def _run(self, user_id: str) -> None:
        queue = self._queues[user_id]
        while True:
            try:
                chat_id, text = queue.get_nowait()
            except asyncio.QueueEmpty:
                return                       # nothing left; the worker retires
            self._in_flight.add(user_id)
            try:
                await self._handler(user_id, chat_id, text)
            except asyncio.CancelledError:
                raise
            except Exception:
                # one bad turn must not stop this learner's remaining messages
                log.exception("turn failed for %s", user_id)
            finally:
                self._in_flight.discard(user_id)
                queue.task_done()

    # ------------------------------------------------------------------- admin

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait until everything submitted so far has been processed (tests)."""
        async def _wait():
            while not self.idle():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(_wait(), timeout)

    async def close(self) -> None:
        for task in self._workers.values():
            task.cancel()
        for task in self._workers.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()
        self._queues.clear()
        self._in_flight.clear()

    def snapshot(self) -> dict[str, int]:
        """Queue depths per learner — surfaced to the admin path."""
        return {u: self.waiting(u) + (1 if u in self._in_flight else 0)
                for u in set(self._queues) | self._in_flight
                if self.waiting(u) or u in self._in_flight}
