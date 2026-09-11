#!/usr/bin/env python3
"""Resume a strict CareLoop judge run.

This compatibility entry point intentionally delegates to
``run_final_trajectory_judge.py``.  That runner reuses only canonical records
that still pass the current packet hash, protocol version, strict schema,
citation, and deterministic rubric checks.  Legacy CSV rows and merely
parseable 1--5 values are never treated as valid completed judgments.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_RUNNER = Path(__file__).with_name("run_final_trajectory_judge.py")
_SPEC = importlib.util.spec_from_file_location("careloop_strict_judge_runner", _RUNNER)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot_load_strict_judge_runner")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

if __name__ == "__main__":
    raise SystemExit(_MODULE.main())
