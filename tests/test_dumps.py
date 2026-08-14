"""Unit tests for HTML diagnostic dump writing."""

import os
import tempfile
from pathlib import Path

import pytest

_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ["DEBUG_DIR"] = str(Path(_IMPORT_TMP.name) / "debug_dumps")
os.environ["LOG_TO_FILE"] = "false"

from core import dumps


def test_write_dump_creates_sanitized_cycle_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dump writing creates a sanitized filename containing page and cycle context."""
    monkeypatch.setenv("DEBUG_DIR", str(tmp_path))
    monkeypatch.setattr(dumps.run_context, "get_cycle_id", lambda: "cycle-1")

    path = dumps.write_dump("amazon", "My / List", "<html>ok</html>", page_index=2)

    assert path is not None
    assert path.parent == tmp_path
    assert path.name.startswith("amazon_My_List_page2_")
    assert path.name.endswith("_cycle-1.html")
    assert path.read_text(encoding="utf-8") == "<html>ok</html>"


def test_write_dump_handles_unknown_name_without_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Missing optional naming context still produces a useful dump filename."""
    monkeypatch.setenv("DEBUG_DIR", str(tmp_path))
    monkeypatch.setattr(dumps.run_context, "get_cycle_id", lambda: "")

    path = dumps.write_dump("throne", None, "body")

    assert path is not None
    assert path.name.startswith("throne_unknown_")
    assert path.name.endswith(".html")


def test_write_dump_returns_none_on_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Filesystem errors are swallowed and reported as a missing dump path."""
    monkeypatch.setenv("DEBUG_DIR", str(tmp_path))
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )

    assert dumps.write_dump("amazon", "List", "body") is None
