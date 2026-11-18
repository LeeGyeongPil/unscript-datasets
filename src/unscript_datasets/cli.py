"""Command line interface: ``scan``, ``fix``, ``owner-cmd`` and ``record``."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

from . import __version__
from .extract import mask_magics
from .hub import IncompleteSnapshotError, LiveHub, RecordedHub, SnapshotFormatError, record, snapshot_filename
from .i18n import LANGS, resolve_lang, t
from .judge import FIXABLE, Analysis, HubLike, analyze
from .notebook import update_notebook
from .owner import InvalidDatasetId, owner_command
from .report import explain_warning, render_json, render_table
from .rewrite import apply_edits, unified_diff

PROG = "unscript-datasets"
STRICT_EXTRA = frozenset({"UNKNOWN", "DYNAMIC"})


def _lang_from_argv(argv: list[str]) -> str | None:
    """Find ``--lang X`` / ``--lang=X`` anywhere in ``argv`` (help text needs it early)."""
    for index, arg in enumerate(argv):
        if arg == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


class _LocalizedParser(argparse.ArgumentParser):
    """ArgumentParser whose usage prefix and common errors follow ``--lang``."""

    lang = "en"

    def format_usage(self) -> str:
        return _translate_prefix(super().format_usage(), self.lang)

    def format_help(self) -> str:
        return _translate_prefix(super().format_help(), self.lang)

    def error(self, message: str):  # type: ignore[override]
        lang = self.lang
        patterns = (
            (r"the following arguments are required: (.*)", "ap.required"),
            (r"unrecognized arguments: (.*)", "ap.unrecognized"),
            (r"argument (.*?): expected one argument", "ap.expected"),
            (r"argument .*?: invalid choice: (.*)", "ap.invalid_choice"),
        )
        for pattern, key in patterns:
            if match := re.fullmatch(pattern, message):
                text = t(lang, key, detail=match.group(1))
                break
        else:
            text = t(lang, "ap.other", detail=message)
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: {t(lang, 'ap.error')}{text}\n")


def _translate_prefix(text: str, lang: str) -> str:
    return re.sub(r"^usage: ", lambda _m: t(lang, "ap.usage"), text, count=1, flags=re.IGNORECASE)


def build_parser(lang: str) -> argparse.ArgumentParser:
    parser_class = type("Parser", (_LocalizedParser,), {"lang": lang})
    parser = parser_class(
        prog=PROG, description=t(lang, "prog.desc"), epilog=t(lang, "prog.epilog"),
        add_help=False,
    )
    parser._positionals.title = t(lang, "ap.positional")
    parser._optionals.title = t(lang, "ap.options")
    parser.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help=t(lang, "ap.help"))
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}", help=t(lang, "ap.version"))
    parser.add_argument("--lang", metavar="en|ko", default=argparse.SUPPRESS, help=t(lang, "arg.lang"))
    sub = parser.add_subparsers(dest="command", metavar="{scan,fix,owner-cmd,record}", parser_class=parser_class)
    sub.required = True

    def sub_parser(name: str, key: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=t(lang, key), description=t(lang, key), add_help=False)
        p._positionals.title = t(lang, "ap.positional")
        p._optionals.title = t(lang, "ap.options")
        p.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help=t(lang, "ap.help"))
        return p

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("paths", nargs="+", metavar="PATH", help=t(lang, "arg.paths"))
        p.add_argument("--offline", metavar="DIR", help=t(lang, "arg.offline"))
        p.add_argument("--exclude", action="append", default=[], metavar="GLOB", help=t(lang, "arg.exclude"))
        p.add_argument("--lang", metavar="en|ko", default=argparse.SUPPRESS, help=t(lang, "arg.lang"))

    scan = sub_parser("scan", "cmd.scan")
    add_common(scan)
    scan.add_argument("--json", action="store_true", help=t(lang, "arg.json"))
    scan.add_argument("--strict", action="store_true", help=t(lang, "arg.strict"))

    fix = sub_parser("fix", "cmd.fix")
    add_common(fix)
    fix.add_argument("--write", action="store_true", help=t(lang, "arg.write"))

    owner = sub_parser("owner-cmd", "cmd.owner")
    owner.add_argument("dataset_id", metavar="DATASET_ID", help=t(lang, "arg.owner_id"))
    owner.add_argument("--revision", metavar="REV", help=t(lang, "arg.owner_revision"))
    owner.add_argument("--lang", metavar="en|ko", default=argparse.SUPPRESS, help=t(lang, "arg.lang"))

    rec = sub_parser("record", "cmd.record")
    rec.epilog = t(lang, "record.epilog")
    rec.add_argument("dataset_ids", nargs="+", metavar="DATASET_ID", help=t(lang, "arg.record_ids"))
    rec.add_argument("--out", required=True, metavar="DIR", help=t(lang, "arg.out"))
    rec.add_argument("--revision", action="append", default=[], metavar="REV", help=t(lang, "arg.record_revision"))
    rec.add_argument("--lang", metavar="en|ko", default=argparse.SUPPRESS, help=t(lang, "arg.lang"))
    return parser


def _error(lang: str, key: str, **values: object) -> int:
    # argparse 오류와 같은 형식("<prog>: 오류: ...")으로 맞춘다 — 접두까지 고른 언어로 나온다
    print(f"{PROG}: {t(lang, 'ap.error')}{t(lang, key, **values)}", file=sys.stderr)
    return 2


def _make_hub(args: argparse.Namespace, lang: str) -> HubLike | int:
    if args.offline:
        if not Path(args.offline).is_dir():
            return _error(lang, "err.offline_missing", detail=args.offline)
        try:
            return RecordedHub(args.offline)
        except IncompleteSnapshotError as exc:
            return _error(lang, "err.snapshot_incomplete", detail=exc)
        except SnapshotFormatError as exc:
            return _error(lang, "err.snapshot", detail=exc)
    return LiveHub()


def _check_paths(paths: list[str], lang: str) -> int | None:
    for path in paths:
        if not Path(path).exists():
            return _error(lang, "err.path_missing", detail=path)
        if Path(path).is_file() and Path(path).suffix not in (".py", ".ipynb"):
            return _error(lang, "err.not_source", detail=path)
    return None


def _warn_unreachable(hub: HubLike, lang: str) -> None:
    """Live mode only: say on stderr why calls are UNKNOWN when the Hub did not answer, and how to fix it."""
    unreachable = getattr(hub, "unreachable", None)  # RecordedHub 에는 없다(녹화 재생은 네트워크를 쓰지 않음)
    failed = unreachable() if unreachable is not None else {}
    if not failed:
        return
    first = next((detail for detail in failed.values() if detail), "-")
    print("! " + t(lang, "hub.unreachable", n=len(failed), detail=first), file=sys.stderr)
    print("! " + t(lang, "hub.network_hint"), file=sys.stderr)


def _exit_code(analysis: Analysis, strict: bool) -> int:
    codes = set(analysis.counts())
    relevant = FIXABLE | (STRICT_EXTRA if strict else frozenset())
    return 1 if codes & relevant else 0


def _scan(args: argparse.Namespace, lang: str) -> int:
    if (bad := _check_paths(args.paths, lang)) is not None:
        return bad
    hub = _make_hub(args, lang)
    if isinstance(hub, int):
        return hub
    analysis = analyze(args.paths, args.exclude, hub)
    if args.json:
        print(render_json(analysis))
    else:
        print(render_table(analysis, lang, args.offline))
    _warn_unreachable(hub, lang)
    return _exit_code(analysis, args.strict)


def _plan_changes(analysis: Analysis) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """Group replacements by source unit and return ``(file, label, old, new)`` plus warnings.

    Each changed unit is a whole ``.py`` file or one notebook cell.
    """
    warnings: list[str] = []
    units: dict[tuple[str, int | None], list] = {}
    for site, verdict in analysis.items:
        if verdict.replacement is not None:
            units.setdefault((site.file, site.cell), []).append((site, verdict))
    changes = []
    for (file, cell), entries in units.items():
        entries.sort(key=lambda e: e[0].start)
        edits, last_end = [], -1
        for site, verdict in entries:
            if site.start < last_end:  # a call nested inside another edited call
                warnings.append(f"overlap={site.location}")
                continue
            edits.append((site.start, site.end, verdict.replacement))
            last_end = site.end
        old_unit = entries[0][0].unit_text
        new_unit = apply_edits(old_unit, edits)
        try:

            ast.parse(mask_magics(new_unit) if cell is not None else new_unit)
        except SyntaxError as exc:
            warnings.append(f"syntax_error={file}:{exc.lineno}")
            continue
        label = file if cell is None else f"{file}#cell{cell}"
        changes.append((file, label, old_unit, new_unit))
    return changes, warnings


def _fix(args: argparse.Namespace, lang: str) -> int:
    if (bad := _check_paths(args.paths, lang)) is not None:
        return bad
    hub = _make_hub(args, lang)
    if isinstance(hub, int):
        return hub
    analysis = analyze(args.paths, args.exclude, hub)
    _warn_unreachable(hub, lang)
    changes, warnings = _plan_changes(analysis)
    for item in [*analysis.warnings, *warnings]:
        print("! " + explain_warning(item, lang), file=sys.stderr)
    changed_files = {(c[0], c[1]) for c in changes}
    for site, verdict in analysis.items:
        label = site.file if site.cell is None else f"{site.file}#cell{site.cell}"
        if verdict.replacement is not None and (site.file, label) in changed_files:
            for item in verdict.warnings:
                print(f"! {site.location}: " + explain_warning(item, lang), file=sys.stderr)
    if not changes:
        print(t(lang, "fix.nothing"))
    for _, label, old, new in changes:
        print(unified_diff(old, new, label), end="")
    pending = sum(1 for _, v in analysis.items if v.code in ("OWNER", "MANUAL"))
    if not args.write:
        if changes:
            print(t(lang, "fix.dry_run"))
        return _exit_code(analysis, strict=False)
    _write_changes(changes)
    if changes:
        n_calls = sum(1 for _, v in analysis.items if v.replacement is not None)
        print(t(lang, "fix.written", files=len({c[0] for c in changes}), calls=n_calls))
    if pending:
        print(t(lang, "fix.remaining", n=pending))
    return 1 if pending else 0


def _write_changes(changes: list[tuple[str, str, str, str]]) -> None:
    by_file: dict[str, dict[int | None, str]] = {}
    for file, label, _old, new in changes:
        cell = int(label.split("#cell")[1]) if "#cell" in label else None
        by_file.setdefault(file, {})[cell] = new
    for file, units in by_file.items():
        path = Path(file)
        if path.suffix == ".ipynb":
            original = path.read_bytes().decode("utf-8")
            sources = {cell: text for cell, text in units.items() if cell is not None}
            path.write_bytes(update_notebook(original, sources).encode("utf-8"))
        else:
            path.write_bytes(units[None].encode("utf-8"))


def _owner(args: argparse.Namespace, lang: str) -> int:
    try:
        command = owner_command(args.dataset_id, args.revision)
    except InvalidDatasetId as exc:
        return _error(lang, "err.bad_id", detail=exc)
    print(t(lang, "owner.intro"))
    print()
    print(command)
    print()
    print(t(lang, "owner.note"))
    return 0


def _record(args: argparse.Namespace, lang: str) -> int:
    """Exit 0 all recorded, 1 recorded with warnings, 2 the Hub did not answer for some dataset."""
    hub = LiveHub()
    out = Path(args.out)
    outcomes = record(hub, args.dataset_ids, out, args.revision)
    code = 0
    for outcome in outcomes:
        dataset_id = outcome.dataset_id
        if outcome.path is None:
            # Hub 가 응답하지 않음: 빈 스냅숏을 남기면 --offline 재생이 조용히 UNKNOWN 으로 통과하므로 저장하지 않고 실패로 끝낸다
            _error(lang, "record.failed", id=dataset_id, detail=outcome.error or outcome.status)
            existing = out / snapshot_filename(dataset_id)
            if existing.exists():
                print("! " + t(lang, "record.kept", path=existing), file=sys.stderr)
            code = 2
            continue
        print(t(lang, "record.done", id=dataset_id, path=outcome.path, status=outcome.status))
        if outcome.status in ("gated", "not_found"):
            print("! " + t(lang, f"record.warn_{outcome.status}", id=dataset_id), file=sys.stderr)
            code = max(code, 1)
        for revision in outcome.missing_revisions:
            print("! " + t(lang, "record.warn_revision", id=dataset_id, rev=revision), file=sys.stderr)
            code = max(code, 1)
    if code == 2:
        print("! " + t(lang, "hub.network_hint"), file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cli_lang = _lang_from_argv(argv)
    if cli_lang is not None and cli_lang not in LANGS:
        return _error(resolve_lang(None), "err.bad_lang", detail=cli_lang)
    lang = resolve_lang(cli_lang)
    parser = build_parser(lang)
    args = parser.parse_args(argv)
    handlers = {"scan": _scan, "fix": _fix, "owner-cmd": _owner, "record": _record}
    return handlers[args.command](args, lang)


if __name__ == "__main__":
    raise SystemExit(main())
