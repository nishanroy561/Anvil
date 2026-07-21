"""Demo-data generator: `python -m demo seed --n 200`.

Produces the *varied* traffic the dashboard needs to look real — a mix of
models, a slice of errors, a slow tail, several sessions and users. Later
phases extend this file: `--drift` (Phase 8) and thumbs-down feedback
(Phase 9).
"""

from __future__ import annotations

import random
import time

import anvil_sdk as anvil
from anvil_sdk.langchain import AnvilCallbackHandler

from .chain import DEMO_MODELS, build_chain, build_model
from .kb import QUESTIONS

# Roughly what a healthy-but-not-perfect app looks like. Errors and slow calls
# are the traces worth opening, so seeded data has to contain some.
ERROR_RATE = 0.08
SLOW_RATE = 0.12

SESSIONS = [f"sess-{i:03d}" for i in range(1, 21)]
USERS = [f"user-{i:03d}" for i in range(1, 13)]
CHANNELS = ["web", "mobile", "api"]


def seed(n: int = 200, delay: float = 0.0, verbose: bool = True) -> dict:
    counts = {"ok": 0, "error": 0, "slow": 0}

    for i in range(n):
        question, topic = random.choice(QUESTIONS)
        model = random.choice(DEMO_MODELS)
        fail = random.random() < ERROR_RATE
        slow = not fail and random.random() < SLOW_RATE

        chain = build_chain(model=build_model(model=model, fail=fail, slow=slow))
        handler = AnvilCallbackHandler(
            trace_name="ask",
            session_id=random.choice(SESSIONS),
            user_ref=random.choice(USERS),
            metadata={
                "topic": topic,
                "channel": random.choice(CHANNELS),
                "seeded": True,
            },
        )

        try:
            chain.invoke(question, config={"callbacks": [handler]})
            counts["slow" if slow else "ok"] += 1
        except Exception as exc:  # the simulated provider failure
            counts["error"] += 1
            if verbose and counts["error"] <= 2:
                print(f"  (expected simulated failure: {exc})")

        if verbose and (i + 1) % 25 == 0:
            print(f"  {i + 1}/{n} traces")
        if delay:
            time.sleep(delay)

    # The flusher is on a 2s timer; without this the process can exit with the
    # tail still queued.
    anvil.flush()
    if verbose:
        print(
            f"seeded {n} traces: {counts['ok']} ok, {counts['slow']} slow, "
            f"{counts['error']} error | client stats: {anvil.stats()}"
        )
    return counts
