"""Identity over HTTP: the cookie, and what each learner can see through the API.

No model calls here — these exercise the endpoints that decide scope. The
Codeforces fetch is forced to fail so every request lands on the deterministic
offline problem instead of scraping.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import codeforces, docstore, main, state


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(codeforces, "random_problem", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("offline in tests")))
    state._cache.clear()
    return lambda: TestClient(main.app)


def _as(client, name):
    c = client()
    c.post("/whoami", json={"user": name})
    return c


def test_an_unknown_visitor_is_guest(client):
    assert client().get("/whoami").json()["user"] == "guest"


def test_switching_user_sticks_across_requests(client):
    c = _as(client, "alice")
    # the regression this guards: the middleware used to append a second
    # Set-Cookie on this very response and hand the session back to guest
    assert c.get("/whoami").json()["user"] == "alice"
    assert c.get("/whoami").json()["user"] == "alice"


def test_display_names_are_normalised(client):
    c = client()
    assert c.post("/whoami", json={"user": "  Alice Smith "}).json()["user"] == "alice-smith"


def test_private_documents_are_not_served_to_another_learner(client):
    docstore.save(doc_type="fact", text="alice is colour-blind", user_id="alice")

    alice = _as(client, "alice").get("/memory").json()
    bob = _as(client, "bob").get("/memory").json()

    assert [d["text"] for d in alice["documents"]["facts"]] == ["alice is colour-blind"]
    assert bob["documents"]["facts"] == []


def test_a_note_posted_by_one_learner_is_served_to_another(client):
    posted = _as(client, "alice").post(
        "/notes", json={"body": "the second sample has a trailing blank line"})
    assert posted.status_code == 200

    seen = _as(client, "bob").get("/notes").json()["notes"]
    assert [n["author"] for n in seen] == ["alice"]
    assert "trailing blank line" in seen[0]["body"]


def test_the_operating_rules_are_served_with_their_ids(client):
    body = client().get("/rules").json()
    assert "R1" in body["ids"] and body["titles"]["R1"]
    assert "OPERATING RULES" not in body["text"]     # raw file, not the wrapper


def test_reset_clears_this_learners_documents_only(client):
    docstore.save(doc_type="fact", text="alice fact", user_id="alice")
    docstore.save(doc_type="fact", text="bob fact", user_id="bob")

    _as(client, "alice").post("/reset")

    assert docstore.all_docs("alice") == []
    assert len(docstore.all_docs("bob")) == 1
