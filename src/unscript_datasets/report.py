"""Render an :class:`~unscript_datasets.judge.Analysis` as a table or JSON."""

from __future__ import annotations

import json
import unicodedata

from .i18n import t
from .judge import VERDICTS, Analysis, Verdict

JSON_VERSION = 1


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * (width - _width(text))


def _split_code(item: str) -> tuple[str, str]:
    code, _, detail = item.partition("=")
    return code, detail


def explain(verdict: Verdict, lang: str) -> str:
    return t(lang, f"reason.{verdict.reason}", detail=verdict.detail)


def explain_warning(item: str, lang: str) -> str:
    code, detail = _split_code(item)
    if code == "unreadable" and "@" in detail:  # "path@reason[:extra]"
        path, _, reason = detail.rpartition("@")
        key, _, extra = reason.partition(":")
        return t(lang, "warn.unreadable", detail=f"{path}: " + t(lang, f"reason.{key}", detail=extra))
    return t(lang, f"warn.{code}", detail=detail)


def render_table(analysis: Analysis, lang: str, offline_dir: str | None = None) -> str:
    lines: list[str] = []
    if offline_dir:
        lines.append(t(lang, "mode.offline", dir=offline_dir))
        lines.append("")
    for item in analysis.warnings:
        lines.append("! " + explain_warning(item, lang))
    if not analysis.items:
        lines.append(t(lang, "summary.none"))
        return "\n".join(lines)
    header = (t(lang, "col.location"), t(lang, "col.dataset"), t(lang, "col.verdict"), t(lang, "col.detail"))
    rows = []
    for site, verdict in analysis.items:
        dataset = site.dataset or "-"
        if verdict.canonical_id and verdict.canonical_id != site.dataset:
            dataset = f"{dataset} -> {verdict.canonical_id}"
        rows.append((f"{site.location}:{site.col}", dataset, verdict.code, explain(verdict, lang), verdict))
    widths = [max(_width(header[i]), *(_width(r[i]) for r in rows)) for i in range(3)]
    lines.append("  ".join(_pad(header[i], widths[i]) for i in range(3)) + "  " + header[3])
    for location, dataset, code, detail, verdict in rows:
        lines.append("  ".join(_pad(v, widths[i]) for i, v in enumerate((location, dataset, code))) + "  " + detail)
        for warning in verdict.warnings:
            lines.append(" " * (sum(widths) + 4) + "! " + explain_warning(warning, lang))
    lines.append("")
    lines.append(t(lang, "summary.counts", files=analysis.files, calls=len(analysis.items)))
    counts = analysis.counts()
    verdicts = ", ".join(f"{code} {counts[code]}" for code in VERDICTS if code in counts)
    lines.append(t(lang, "summary.verdicts", verdicts=verdicts))
    return "\n".join(lines)


def render_json(analysis: Analysis) -> str:
    calls = []
    for site, verdict in analysis.items:
        calls.append({
            "file": site.file,
            "line": site.line,
            "col": site.col,
            "cell": site.cell,
            "dataset": site.dataset,
            "canonical_id": verdict.canonical_id,
            "config": site.config,
            "split": site.split,
            "revision": site.revision,
            "verdict": verdict.code,
            "reason": verdict.reason,
            "replacement": verdict.replacement,
            "warnings": verdict.warnings,
        })
    summary = {code: n for code, n in sorted(analysis.counts().items())}
    payload = {"version": JSON_VERSION, "calls": calls, "summary": summary, "warnings": analysis.warnings}
    return json.dumps(payload, indent=2, ensure_ascii=False)
