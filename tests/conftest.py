"""Point every store at a throwaway directory before the backend is imported.

The store modules read their paths from the environment at import time, so this
has to happen here — conftest is imported before the test modules that import
`backend.*`. The upshot is that the suite never touches the real cp_tutor.db or
the real memory_store, and can be run repeatedly without cleanup.
"""
from __future__ import annotations

import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="nlcp2g-tests-")

os.environ["CP_TUTOR_DB"] = os.path.join(_TMP, "test.db")
os.environ["CP_TUTOR_DOCSTORE"] = os.path.join(_TMP, "memory_store")
os.environ["CP_TUTOR_REPORTS"] = os.path.join(_TMP, "reports")
# no API key needed: every test that would call the model stubs it out
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Each test starts from an empty schema."""
    from backend import memory
    db = os.environ["CP_TUTOR_DB"]
    if os.path.exists(db):
        os.remove(db)
    memory.init()
    yield
    shutil.rmtree(os.environ["CP_TUTOR_DOCSTORE"], ignore_errors=True)


@pytest.fixture
def problem():
    from backend.problem import fallback_problem
    return fallback_problem()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
