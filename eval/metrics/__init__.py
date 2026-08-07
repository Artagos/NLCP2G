"""Scorers.

`rank.py` is arithmetic over a golden set — free, exact, reproducible.
`judge.py` asks a model — one call per score, cached so a re-run is free and
identical.

Both are plain functions over plain data. Neither knows where the retrieval came
from, neither reads a config file, and neither writes anything: the runners in
`eval/` supply the inputs and own the output. That is what makes them testable,
and `tests/test_eval_metrics.py` tests them against fixtures whose values were
computed by hand rather than by running the code and recording what it said.
"""
