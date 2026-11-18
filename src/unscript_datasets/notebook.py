"""Write fixed code back into ``.ipynb`` files, changing only the edited cells."""

from __future__ import annotations

import json
import re

from .extract import split_lines


def detect_indent(text: str) -> int:
    match = re.search(r"\n( +)\"", text)
    return len(match.group(1)) if match else 1


def update_notebook(original_text: str, new_sources: dict[int, str]) -> str:
    """Return the notebook JSON with the given cells' ``source`` replaced.

    Cells not listed, their outputs and the notebook metadata are untouched.  The
    JSON is dumped with the indentation found in the original file.
    """
    notebook = json.loads(original_text)
    for index, text in new_sources.items():
        cell = notebook["cells"][index]
        if isinstance(cell.get("source"), list):
            cell["source"] = [line.decode("utf-8") for line in split_lines(text.encode("utf-8"))]
        else:
            cell["source"] = text
    dumped = json.dumps(notebook, indent=detect_indent(original_text), ensure_ascii=False)
    return dumped + ("\n" if original_text.endswith("\n") else "")
