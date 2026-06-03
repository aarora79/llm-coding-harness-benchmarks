"""Unit tests target for the `c2_write_tests` benchmark task.

Empty by design. The `c2_write_tests` task in `scripts/bench.sh` instructs
Claude Code to create `test_path_utils.py` here, with at least 9 pytest
functions covering the three utilities in `sample/utils/path_utils.py`. The
benchmark verifier then runs `pytest` against whatever the model produced.

If you populate this directory yourself, `git clean -fd -- sample/tests/unit/`
will reset it before re-running the benchmark.
"""
