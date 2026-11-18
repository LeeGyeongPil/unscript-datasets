"""Build replacement calls and apply them to source text.

Only the span of the call expression is replaced.  The called expression
(``load_dataset``, ``ld``, ``datasets.load_dataset``) and every keyword that is
not touched are copied verbatim from the original source.
"""

from __future__ import annotations

import difflib
import json

from .extract import POSITIONAL_PARAMS, CallSite

REMOVED_ALWAYS = frozenset({"trust_remote_code"})


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _render(site: CallSite, items: list[tuple[str | None, str]]) -> str:
    parts = [src if key is None else f"{key}={src}" for key, src in items]
    return f"{site.func_src}({', '.join(parts)})"


def strip_trust_remote_code(site: CallSite) -> str:
    """The call without ``trust_remote_code`` (ignored, then rejected, by datasets>=4)."""
    if not any(key == "trust_remote_code" for key, _ in site.args):
        return site.original
    return _render(site, [(k, v) for k, v in site.args if k not in REMOVED_ALWAYS])


def build_revision_call(site: CallSite, ref: str | None) -> str:
    """Set ``revision=ref`` (or drop the revision when ``ref`` is ``None``)."""
    items = [(k, v) for k, v in site.args if k not in REMOVED_ALWAYS]
    if ref is None:
        return _render(site, [(k, v) for k, v in items if k != "revision"])
    value = _quote(ref)
    if any(k == "revision" for k, _ in items):
        return _render(site, [(k, value if k == "revision" else v) for k, v in items])
    return _render(site, items + [("revision", value)])


def build_parquet_call(site: CallSite, globs: dict[str, str]) -> str:
    """``load_dataset("parquet", data_files={split: hf://...}, ...)`` keeping config and split."""
    data_files = "{" + ", ".join(f"{_quote(name)}: {_quote(glob)}" for name, glob in globs.items()) + "}"
    items: list[tuple[str | None, str]] = [(None, '"parquet"'), ("data_files", data_files)]
    position = 0
    for key, src in site.args:
        if key is None:
            name = POSITIONAL_PARAMS[position] if position < len(POSITIONAL_PARAMS) else None
            position += 1
            if name == "split":
                items.append(("split", src))
            continue
        if key in {"path", "name", "revision"} | REMOVED_ALWAYS:
            continue
        items.append((key, src))
    return _render(site, items)


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Replace byte spans of ``text``; overlapping edits are rejected."""
    raw = text.encode("utf-8")
    out = raw
    last_start = len(raw) + 1
    for start, end, new in sorted(edits, reverse=True):
        if end > last_start:
            raise ValueError("overlapping edits")
        out = out[:start] + new.encode("utf-8") + out[end:]
        last_start = start
    return out.decode("utf-8")


def unified_diff(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{label}", tofile=f"b/{label}",
        )
    )
