"""Verdict rules: what is the right fix for one ``load_dataset`` call?

The rules run in a fixed order (see README, "What it checks"), so the same
snapshot and the same call always give the same verdict.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .extract import CallSite
from .hub import CONVERT_REF, HubSnapshot, parse_time
from .rewrite import build_parquet_call, build_revision_call, strip_trust_remote_code

VERDICTS = ("OK", "PIN", "PR", "PARQUET", "OWNER", "MANUAL", "UNKNOWN", "DYNAMIC", "SKIP")
FIXABLE = frozenset({"PIN", "PR", "PARQUET", "OWNER", "MANUAL"})


class HubLike(Protocol):
    offline: bool

    def snapshot(self, dataset_id: str) -> HubSnapshot: ...

    def tree(self, snap: HubSnapshot, revision: str) -> list[str] | None: ...


@dataclass
class Verdict:
    code: str
    reason: str
    canonical_id: str | None = None
    replacement: str | None = None
    warnings: list[str] = field(default_factory=list)  # "code" or "code=detail"
    detail: str = ""                                    # extra text for the reason (PR number, candidates)


def _root_files(tree: list[str]) -> set[str]:
    return {path for path in tree if "/" not in path}


def parse_convert_tree(tree: list[str]) -> dict[str, dict[str, list[str]]]:
    """``{config: {split_dir: [files]}}`` from ``<config>/<split>/<file>.parquet`` paths."""
    configs: dict[str, dict[str, list[str]]] = {}
    for path in tree:
        parts = path.split("/")
        if len(parts) == 3 and parts[2].endswith(".parquet"):
            configs.setdefault(parts[0], {}).setdefault(parts[1], []).append(path)
    return configs


def split_names(split: str) -> list[str] | None:
    """Base split names of a split expression: ``"train[:10%]+test"`` -> ``["train", "test"]``."""
    names: list[str] = []
    for part in split.split("+"):
        match = re.fullmatch(r"\s*([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*", part)
        if not match:
            return None
        names.append(match.group(1))
    return names


def _select_pr(hub: HubLike, snap: HubSnapshot, script: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for pr in snap.pull_requests:
        if pr.get("status") != "open" or "parquet" not in str(pr.get("title", "")).lower():
            continue
        tree = hub.tree(snap, pr["ref"])
        if not tree or script in _root_files(tree):
            continue
        if not any(path.endswith(".parquet") for path in tree):
            continue
        if best is None or pr["num"] > best["num"]:
            best = pr
    return best


def _finish(site: CallSite, verdict: Verdict, replacement: str | None) -> Verdict:
    """Attach a replacement after checking that it still parses as Python."""
    if replacement is not None:
        import ast

        try:
            ast.parse(replacement, mode="eval")
        except SyntaxError:
            verdict.code, verdict.reason, verdict.replacement = "MANUAL", "rewrite_invalid", None
            return verdict
        if replacement != site.original:
            verdict.replacement = replacement
            if site.has_comment:
                verdict.warnings.append("comment_in_call")
    return verdict


def judge_call(site: CallSite, hub: HubLike) -> Verdict:
    """Judge one call.  ``DYNAMIC`` / ``SKIP`` calls are passed through unchanged."""
    if site.kind in ("DYNAMIC", "SKIP"):
        return Verdict(site.kind, site.reason)
    assert site.dataset is not None
    snap = hub.snapshot(site.dataset)
    if snap.status != "ok":
        return Verdict("UNKNOWN", snap.status, canonical_id=snap.canonical_id)
    canonical = snap.canonical_id
    script = canonical.split("/")[-1] + ".py"
    rev = site.revision or "main"
    result = lambda code, reason, **kw: Verdict(code, reason, canonical_id=canonical, **kw)  # noqa: E731

    # Rule 3: the hand-written "revision=refs/convert/parquet" trap (config is merged into "default").
    trap = rev == CONVERT_REF and site.config not in (None, "default")
    if not trap:
        tree = hub.tree(snap, rev)
        if tree is None:
            return result("UNKNOWN", "not_recorded" if hub.offline else "revision_not_found")
        # Rule 4: no <name>.py at that revision -> already works.
        if script not in _root_files(tree):
            return _finish(site, result("OK", "no_script"), strip_trust_remote_code(site))
        # Rule 5: pinned to an old commit/branch that still has the script, main does not.
        if rev != "main":
            main_tree = hub.tree(snap, "main")
            if main_tree is not None and script not in _root_files(main_tree):
                verdict = result("PIN", "pinned_old_revision", detail=rev)
                if site.config and snap.card_configs is not None:
                    names = {c.get("config_name") for c in snap.card_configs}
                    if site.config not in names:
                        verdict.warnings.append(f"config_not_in_card={site.config}")
                return _finish(site, verdict, build_revision_call(site, None))
        # Rule 6: an open conversion PR exists.
        pr = _select_pr(hub, snap, script)
        if pr is not None:
            verdict = result("PR", "open_conversion_pr", detail=f"#{pr['num']}")
            verdict.warnings.append(f"selected_pr=https://huggingface.co/datasets/{canonical}/discussions/{pr['num']}")
            return _finish(site, verdict, build_revision_call(site, pr["ref"]))

    # Rule 7: the automatic conversion branch.
    convert_tree = hub.tree(snap, CONVERT_REF)
    if convert_tree is None:
        return result("OWNER", "no_convert_branch")
    configs = parse_convert_tree(convert_tree)
    if not configs:
        return result("OWNER", "no_convert_branch")
    config = site.config
    if config is None:
        if len(configs) == 1:
            config = next(iter(configs))
        else:
            return result("MANUAL", "multiple_configs", detail=", ".join(sorted(configs)))
    if config not in configs:
        return result("OWNER", "config_not_in_convert", detail=config)
    splits = configs[config]
    if site.split_dynamic:
        return result("MANUAL", "split_unparseable")
    if site.split is None:
        wanted = sorted(name for name in splits if not name.startswith("partial-"))
        if not wanted:
            return result("OWNER", "partial_split_only", detail=", ".join(sorted(splits)))
    else:
        wanted_or_none = split_names(site.split)
        if wanted_or_none is None:
            return result("MANUAL", "split_unparseable", detail=site.split)
        wanted = wanted_or_none
        for name in wanted:
            if name not in splits:
                if f"partial-{name}" in splits:
                    return result("OWNER", "partial_split_only", detail=f"partial-{name}")
                return result("OWNER", "split_not_found_in_convert", detail=name)
    if site.has_arg("data_dir") or site.has_arg("data_files") or site.has_star or \
            sum(1 for key, _ in site.args if key is None) > 5:
        return result("MANUAL", "unsupported_args")

    globs = {name: f"hf://datasets/{canonical}@refs%2Fconvert%2Fparquet/{config}/{name}/*.parquet" for name in wanted}
    verdict = result("PARQUET", "convert_branch")
    last_convert, last_main = parse_time(snap.convert_last_modified), parse_time(snap.main_last_modified)
    if last_convert and last_main and last_convert < last_main:
        verdict.warnings.append("stale_convert_branch")
    return _finish(site, verdict, build_parquet_call(site, globs))


def glob_matches(glob: str, tree: list[str]) -> list[str]:
    """Files of a convert-branch ``tree`` matched by a generated ``hf://`` glob."""
    pattern = glob.split("@refs%2Fconvert%2Fparquet/", 1)[1]
    return [path for path in tree if fnmatch.fnmatch(path, pattern)]


@dataclass
class Analysis:
    files: int
    items: list[tuple[CallSite, Verdict]]
    warnings: list[str]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, verdict in self.items:
            counts[verdict.code] = counts.get(verdict.code, 0) + 1
        return counts


def analyze(paths: list[str], excludes: list[str], hub: HubLike) -> Analysis:
    """Scan ``paths`` and judge every call (the Hub is asked once per dataset)."""
    from .extract import scan_paths

    scanned = scan_paths(paths, excludes)
    items = [(site, judge_call(site, hub)) for site in scanned.calls]
    return Analysis(files=scanned.files, items=items, warnings=scanned.warnings)
