import io
import os
import subprocess
import sys
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from quality_gates import marker_budget, marker_preservation, markers
from quality_gates.source import UnreadableSource

root = Path(os.environ["TEMPORARY"])
source = root / "source.js"

cases = (
    ("python worker exception", RuntimeError("worker could not start")),
    ("worker timeout", subprocess.TimeoutExpired([], 5)),
    ("worker nonzero exit", subprocess.CompletedProcess([], 1, b"", b"worker exception")),
    ("worker signal exit", subprocess.CompletedProcess([], -11, b"", b"")),
    ("malformed worker output", subprocess.CompletedProcess([], 0, b"not JSON", b"")),
)

for label, result in cases:
    with patch(
        "quality_gates.markers._run_tree_sitter_worker",
        side_effect=result if isinstance(result, Exception) else None,
        return_value=None if isinstance(result, Exception) else result,
    ):
        try:
            markers.count_blocks_in(source)
        except UnreadableSource as exc:
            assert "worker" in exc.reason, (label, exc.reason)
        else:
            raise AssertionError(f"{label} was accepted")

with patch(
    "quality_gates.markers._run_tree_sitter_worker",
    return_value=subprocess.CompletedProcess([], -11, b"", b""),
):
    counts, unreadable = marker_budget._marker_scan(root)
assert counts == {"source.js": 0}
assert len(unreadable) == 1 and "worker" in unreadable[0], unreadable

stderr = io.StringIO()
with (
    patch(
        "quality_gates.markers._run_tree_sitter_worker",
        return_value=subprocess.CompletedProcess([], 1, b"", b"worker exception"),
    ),
    patch.object(sys, "argv", ["check-marker-preservation", "--root", str(root)]),
    redirect_stderr(stderr),
):
    assert marker_preservation.main() == 1
assert "worker" in stderr.getvalue(), stderr.getvalue()

print("ok Tree-sitter worker failures are contained")
