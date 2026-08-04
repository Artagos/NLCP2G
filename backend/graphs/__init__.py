"""Every agent in this system, as a compiled LangGraph.

The shape is one graph per agent, plus one graph that routes between them:

    turn.py       the chat turn: route -> refuse | tutor | solve | summarize | ...
    tutor.py      the guardrailed tutor and its three tools (a ReAct loop)
    solution.py   the blind pipeline: screen -> execute <-> critique -> run
    admin.py      the operator subagent and its eleven privileged tools
    monitor.py    the out-of-band judge grading shipped runs
    sweep.py      the background trigger deciding whether to speak unprompted
    reflect.py    whether this exchange is worth remembering
    summarize.py  the per-problem recap

    schema.py     the state each of them runs on
    checkpoint.py durable state, so a conversation survives a restart

Only `turn.py` is compiled with a checkpointer. The others are invoked inside a
turn or by a separate process, hold objects too large to persist, and start
fresh each time — see the note in `schema.py`.

Each graph is reached through the module it replaced (`tutor.answer`,
`translator.build_program`, `monitor.run_once`, `scheduler.sweep`, ...), whose
signatures did not change. Graphs orchestrate those functions; they do not
absorb them, which is why the pre-refactor test suite still stubs the same
seams.
"""
