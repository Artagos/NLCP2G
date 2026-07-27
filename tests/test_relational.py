"""The relational store: the domain model is queryable, not a key-value dump."""
from __future__ import annotations

from backend import memory


def _seed():
    memory.upsert_problem("p800", "Watermelon", 800, ["math", "brute force"],
                          "http://x/800", "codeforces", 1000, 256, 2)
    memory.upsert_problem("p1200", "Two Sets", 1200, ["greedy", "math"],
                          "http://x/1200", "codeforces", 2000, 256, 3)
    memory.upsert_problem("p1900", "Tree Cutting", 1900, ["trees", "dp"],
                          "http://x/1900", "codeforces", 2000, 256, 1)


def test_problems_can_be_filtered_by_rating_band_and_tag():
    _seed()
    assert [p["name"] for p in memory.find_problems(min_rating=1000)] == \
        ["Two Sets", "Tree Cutting"]
    assert [p["name"] for p in memory.find_problems(max_rating=1200)] == \
        ["Watermelon", "Two Sets"]
    assert [p["name"] for p in memory.find_problems(tag="math")] == \
        ["Watermelon", "Two Sets"]
    assert memory.find_problems(min_rating=1000, max_rating=1500, tag="greedy")[0]["name"] \
        == "Two Sets"


def test_upsert_is_idempotent_and_refreshes_mutable_fields():
    _seed()
    memory.upsert_problem("p800", "Watermelon", 900, ["math"], "http://x/800",
                          "codeforces", 1000, 256, 2)
    rows = memory.find_problems()
    assert len(rows) == 3                       # not duplicated
    assert memory.get_problem("p800")["rating"] == 900   # updated


def test_attempts_drive_progress_and_the_solved_flag():
    memory.set_current("u:alice", "p800", "Watermelon", 800, 4, "A")

    memory.record_attempt("u:alice", "p800", "Watermelon", 800, "check every pair", "WA")
    n = memory.record_attempt("u:alice", "p800", "Watermelon", 800, "count evens", "AC")

    assert n == 2
    progress = memory.progress("u:alice")
    assert progress["attempts"] == 2 and progress["solved"] == 1
    assert progress["by_verdict"] == {"WA": 1, "AC": 1}
    assert memory.solved_ratings("u:alice") == [800]


def test_one_learners_attempts_do_not_appear_in_anothers_progress():
    memory.set_current("u:alice", "p800", "Watermelon", 800, 4, "A")
    memory.record_attempt("u:alice", "p800", "Watermelon", 800, "...", "AC")

    assert memory.progress("u:bob")["attempts"] == 0
    assert memory.solved_ratings("u:bob") == []


def test_the_scratchpad_replays_a_handoff_in_order():
    memory.scratch("run1", "executor", 1, "generated", {"len": 400})
    memory.scratch("run1", "critic", 1, "revise", {"result": "remove the sort"})
    memory.scratch("run1", "executor", 2, "generated", {"len": 380})
    memory.scratch("run2", "critic", 1, "approved", {})

    trace = memory.scratch_for("run1")
    assert [(e["agent"], e["round"], e["status"]) for e in trace] == [
        ("executor", 1, "generated"),
        ("critic", 1, "revise"),
        ("executor", 2, "generated"),
    ]
    assert trace[1]["payload"]["result"] == "remove the sort"


def test_runs_are_logged_and_only_ungraded_ones_come_back():
    a = memory.log_run(user_id="alice", sid="u:alice", problem_key="p800",
                       problem_name="Watermelon", intent="concept",
                       user_message="what is a hash map?", reply="A hash map is...",
                       rules_applied=["R1", "R2"])
    b = memory.log_run(user_id="bob", sid="u:bob", problem_key="p800",
                       problem_name="Watermelon", intent="chitchat",
                       user_message="hi", reply="Hi!")

    assert {r["run_id"] for r in memory.ungraded_runs()} == {a, b}

    memory.save_judgment(a, "strictly_adheres", "none", "completed",
                         "not_applicable", "checked R1 and R2; clean", ["R1"])

    assert [r["run_id"] for r in memory.ungraded_runs()] == [b]
    judged = memory.judgments()
    assert judged[0]["run_id"] == a and judged[0]["cited_rules"] == ["R1"]
    # the judgment joins back to the run it graded
    assert judged[0]["user_message"] == "what is a hash map?"


def test_reset_clears_the_session_but_not_the_audit_trail():
    memory.record_attempt("u:alice", "p800", "W", 800, "...", "WA")
    run = memory.log_run(user_id="alice", sid="u:alice", problem_key="p800",
                         problem_name="W", intent="solution",
                         user_message="...", reply="...", verdict="WA")

    memory.reset("u:alice")

    assert memory.progress("u:alice")["attempts"] == 0
    assert memory.get_run(run) is not None      # a reset is not a way to erase evidence
