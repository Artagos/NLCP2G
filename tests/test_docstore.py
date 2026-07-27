"""The non-relational store: privacy, cues, and rules-vs-facts.

The privacy tests are the ones that matter. "A's fact never surfaces for B" is
enforced by the store — B's private documents live in a different file, so there
is no filter that can be got wrong — and these tests are what says so.
"""
from __future__ import annotations

from backend import docstore


def test_private_fact_is_invisible_to_another_user():
    docstore.save(doc_type="fact", text="alice pays for a Codeforces mentor",
                  user_id="alice", cue_keywords=["mentor", "coach"])

    assert any("mentor" in d["text"] for d in docstore.all_docs("alice"))
    assert docstore.all_docs("bob") == []
    # and it must not come back through retrieval either
    assert docstore.retrieve("bob", "who is my mentor") == []
    assert docstore.retrieve("alice", "what did my mentor say") != []


def test_shared_documents_reach_everyone():
    docstore.save(doc_type="rule", text="always define a term before using it",
                  user_id="admin", scope="shared")

    for user in ("alice", "bob", "carol"):
        assert any(d["text"].startswith("always define") for d in docstore.rules_for(user))


def test_rules_are_returned_without_a_query_facts_are_not():
    """A rule is always in force; a fact has to be pulled by its cue."""
    docstore.save(doc_type="rule", text="keep answers under three sentences",
                  user_id="dan")
    docstore.save(doc_type="fact", text="dan finds recursion confusing",
                  user_id="dan", cue_keywords=["recursion", "recursive"])

    rules = docstore.rules_for("dan")
    assert [r["text"] for r in rules] == ["keep answers under three sentences"]

    # the fact is not in the pushed set...
    assert all(r["type"] == "rule" for r in rules)
    # ...but arrives when its cue matches
    assert docstore.retrieve("dan", "can you explain recursion?")
    # ...and stays away when it doesn't
    assert docstore.retrieve("dan", "what is a hash map?") == []


def test_cue_keywords_outrank_incidental_text_overlap():
    docstore.save(doc_type="fact", text="prefers worked examples over definitions",
                  user_id="e", cue_keywords=["explain", "example"])
    docstore.save(doc_type="fact", text="explain that they work night shifts",
                  user_id="e", cue_keywords=["schedule", "time"])

    hits = docstore.retrieve("e", "explain how sorting works")
    assert hits[0]["text"].startswith("prefers worked examples")


def test_retrieval_records_usage_so_dead_memories_are_visible():
    doc = docstore.save(doc_type="fact", text="is preparing for a course in September",
                        user_id="f", cue_keywords=["course", "deadline"])
    assert doc["hits"] == 0

    docstore.retrieve("f", "when is my course starting")
    after = next(d for d in docstore.all_docs("f") if d["id"] == doc["id"])
    assert after["hits"] == 1
    assert after["last_used"] is not None


def test_duplicate_facts_are_not_accumulated():
    a = docstore.save(doc_type="fact", text="dislikes jargon", user_id="g")
    b = docstore.save(doc_type="fact", text="Dislikes Jargon", user_id="g")
    assert a["id"] == b["id"]
    assert len(docstore.all_docs("g")) == 1


def test_user_ids_cannot_escape_the_store_directory():
    """The id comes from a cookie, so a traversal attempt must not write out."""
    docstore.save(doc_type="fact", text="x", user_id="../../etc/passwd")
    assert docstore.all_docs("../../etc/passwd") == docstore.all_docs("etc-passwd")


def test_clear_forgets_only_the_named_user():
    docstore.save(doc_type="fact", text="alice fact", user_id="alice")
    docstore.save(doc_type="fact", text="bob fact", user_id="bob")

    docstore.clear("alice")
    assert docstore.all_docs("alice") == []
    assert len(docstore.all_docs("bob")) == 1
