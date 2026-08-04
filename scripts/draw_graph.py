"""Draw the compiled graphs into the README, so the picture cannot drift.

    python scripts/draw_graph.py            # rewrite the generated block
    python scripts/draw_graph.py --check    # non-zero exit if it is stale
    python scripts/draw_graph.py --stdout   # just print it

A diagram maintained by hand is a diagram that is wrong by the third commit. The
mermaid here comes from `compiled.get_graph().draw_mermaid()` on the real
objects, so a node added or an edge rerouted shows up in the README or fails
`--check` in CI. The prose around each one is written by hand and is not
touched — the generator owns the boxes and arrows, not the argument.

Nothing here opens the database: every graph is compiled *without* its
checkpointer, because drawing a picture should not create state.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import order matters only in that this must not run backend.main, which would
# build a FastAPI app for no reason.
from backend.graphs import (admin, monitor, reflect, solution,  # noqa: E402
                            summarize, sweep, turn, tutor)

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "README.md")

BEGIN = "<!-- BEGIN GENERATED GRAPHS -->"
END = "<!-- END GENERATED GRAPHS -->"

# (heading, builder, one line of why it looks like that)
GRAPHS = [
    ("The chat turn", turn.BUILDER,
     "The orchestrator. Routing is the first layer of the guardrail: `strategy` "
     "reaches `refuse`, the one branch with no model call in it."),
    ("The blind solution pipeline", solution.BUILDER,
     "The one cycle in the system. `revise` carries the critic's fix list back "
     "to the executor, at most twice; three of the four exits run nothing."),
    ("The tutor", tutor.BUILDER,
     "A ReAct loop over three retrieval tools. The model decides whether to "
     "call them; it never decides whose memory to look in."),
    ("The operator subagent", admin.BUILDER,
     "The same loop shape, eleven privileged tools, and an authorisation check "
     "that happens before the graph is ever constructed."),
    ("The judge", monitor.BUILDER,
     "Out of band, on its own clock. One run graded per superstep, so a model "
     "failure costs one verdict rather than the batch."),
    ("The background trigger", sweep.BUILDER,
     "Runs per linked learner. Most passes end at `silent`, with the reason "
     "recorded — that is the design working, not an absence of output."),
    ("The reflector", reflect.BUILDER,
     "The skip is an edge out of START: mechanical intents never reach a model."),
    ("The recap", summarize.BUILDER,
     "`digest` is deterministic and `narrate` is the only step that sees a "
     "model — which is how a recap of an unsolved problem stays hint-free."),
]


def _mermaid(builder) -> str:
    """Mermaid for one graph, without the frontmatter block.

    `draw_mermaid` prefixes a `--- config: ... ---` header. It only sets the
    edge curve, and older mermaid renderers choke on it, so it goes.
    """
    text = builder.compile().get_graph().draw_mermaid().strip()
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()


def block() -> str:
    parts = [BEGIN, ""]
    for title, builder, why in GRAPHS:
        parts += [f"#### {title}", "", why, "", "```mermaid", _mermaid(builder),
                  "```", ""]
    parts.append(END)
    return "\n".join(parts)


def _splice(readme: str, generated: str) -> str:
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(readme):
        raise SystemExit(
            f"{README} has no generated block. Add these two lines where the "
            f"diagrams belong:\n\n{BEGIN}\n{END}")
    return pattern.sub(lambda _: generated, readme)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the README is out of date")
    parser.add_argument("--stdout", action="store_true",
                        help="print the block instead of writing it")
    args = parser.parse_args()

    generated = block()
    if args.stdout:
        print(generated)
        return 0

    with open(README, encoding="utf-8") as fh:
        current = fh.read()
    updated = _splice(current, generated)

    if args.check:
        if updated != current:
            print("README diagrams are stale. Run: python scripts/draw_graph.py",
                  file=sys.stderr)
            return 1
        print("README diagrams are up to date.")
        return 0

    if updated == current:
        print("README diagrams already up to date.")
        return 0
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(updated)
    print(f"Wrote {len(GRAPHS)} diagrams to {README}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
