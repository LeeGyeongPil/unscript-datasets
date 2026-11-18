"""Hub state: live lookups (``LiveHub``) and recorded snapshots (``RecordedHub``).

Both classes hand ``judge.py`` the same :class:`HubSnapshot`, so the verdict
logic is identical online and offline.  Only this module talks to
``huggingface_hub``.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CONVERT_REF = "refs/convert/parquet"
SNAPSHOT_FORMAT = 1


@dataclass
class HubSnapshot:
    """Public metadata of one dataset repository (no data content)."""

    requested_id: str
    canonical_id: str
    status: str = "ok"              # ok | gated | not_found | network | not_recorded
    recorded_at: str = ""
    main_sha: str | None = None
    main_last_modified: str | None = None
    convert_last_modified: str | None = None
    pull_requests: list[dict[str, Any]] = field(default_factory=list)  # num, ref, title, status
    trees: dict[str, list[str]] = field(default_factory=dict)          # revision -> file paths
    card_configs: list[dict[str, Any]] | None = None
    synthetic: bool = False
    aliases: list[str] = field(default_factory=list)
    source: str = ""

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["format"] = SNAPSHOT_FORMAT
        data["trees"] = {rev: sorted(files) for rev, files in sorted(self.trees.items())}
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "HubSnapshot":
        known = {name for name in cls.__dataclass_fields__}
        snap = cls(**{k: v for k, v in data.items() if k in known})
        if not snap.aliases:
            snap.aliases = [snap.requested_id]
        return snap


class SnapshotFormatError(ValueError):
    """A recorded snapshot file is unusable."""


class IncompleteSnapshotError(SnapshotFormatError):
    """A snapshot file says nothing about the Hub (e.g. recorded while the Hub was unreachable)."""


# 녹화 파일에 남겨도 되는 상태: Hub 가 실제로 대답한 것만. "network" 는 대답이 없었다는 뜻이라 저장하지 않는다.
RECORDABLE_STATUSES = frozenset({"ok", "gated", "not_found"})


def _short_error(exc: BaseException) -> str:
    """One line such as ``ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] ...`` (shown to the user)."""
    text = " ".join(str(exc).split())
    detail = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return detail if len(detail) <= 200 else detail[:197] + "..."


def snapshot_filename(dataset_id: str) -> str:
    return dataset_id.replace("/", "__") + ".json"


def classify_error(exc: BaseException) -> str:
    """Map an exception from the Hub client to a snapshot status."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if code in (401, 403):
        return "gated"
    if code == 404:
        return "not_found"
    name = type(exc).__name__
    if name == "GatedRepoError":
        return "gated"
    if name in ("RepositoryNotFoundError", "RevisionNotFoundError"):
        return "not_found"
    return "network"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def parse_time(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


class LiveHub:
    """Reads repository state through ``huggingface_hub.HfApi`` (cached per run)."""

    offline = False

    def __init__(self, api: Any = None, token: str | None = None) -> None:
        if api is None:
            from huggingface_hub import HfApi

            api = HfApi(token=token)
        self.api = api
        self._cache: dict[str, HubSnapshot] = {}
        # 실패한 조회의 원인(한 줄). 화면에 "Hub not reachable" 만 보이면 SSL 같은 진짜 원인을 알 수 없어서 남긴다.
        self.errors: dict[str, str] = {}
        self.tree_errors: dict[tuple[str, str], tuple[str, str]] = {}  # (canonical id, revision) -> (status, detail)

    def snapshot(self, dataset_id: str) -> HubSnapshot:
        if dataset_id not in self._cache:
            self._cache[dataset_id] = self._fetch(dataset_id)
        return self._cache[dataset_id]

    def _fetch(self, dataset_id: str) -> HubSnapshot:
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        snap = HubSnapshot(requested_id=dataset_id, canonical_id=dataset_id, recorded_at=now,
                           aliases=[dataset_id])
        try:
            info = self.api.dataset_info(dataset_id)
            snap.canonical_id = getattr(info, "id", None) or dataset_id
            snap.main_sha = getattr(info, "sha", None)
            snap.main_last_modified = _iso(getattr(info, "last_modified", None))
            card = getattr(info, "card_data", None)
            if card is not None:
                snap.card_configs = (card.to_dict() if hasattr(card, "to_dict") else dict(card)).get("configs")
            refs = self.api.list_repo_refs(snap.canonical_id, repo_type="dataset")
            snap.trees["main"] = self._files(snap.canonical_id, "main")
            converts = [c.name for c in getattr(refs, "converts", [])]
            if "parquet" in converts:
                snap.trees[CONVERT_REF] = self._files(snap.canonical_id, CONVERT_REF)
                snap.convert_last_modified = self._last_modified(snap.canonical_id, CONVERT_REF)
            snap.pull_requests = self._open_prs(snap.canonical_id)
            for pr in snap.pull_requests:
                if "parquet" in pr["title"].lower():
                    snap.trees[pr["ref"]] = self._files(snap.canonical_id, pr["ref"])
        except Exception as exc:  # noqa: BLE001 - every failure becomes a verdict reason
            snap.status = classify_error(exc)
            self.errors[dataset_id] = _short_error(exc)
        return snap

    def unreachable(self) -> dict[str, str]:
        """Datasets whose lookup failed without a Hub answer (status ``network``) and why."""
        return {ds: self.errors.get(ds, "") for ds, snap in sorted(self._cache.items()) if snap.status == "network"}

    def _files(self, repo: str, revision: str) -> list[str]:
        return sorted(self.api.list_repo_files(repo, repo_type="dataset", revision=revision))

    def _last_modified(self, repo: str, revision: str) -> str | None:
        try:
            return _iso(self.api.dataset_info(repo, revision=revision).last_modified)
        except Exception:  # noqa: BLE001 - only used for a staleness warning
            return None

    def _open_prs(self, repo: str) -> list[dict[str, Any]]:
        prs = []
        for item in self.api.get_repo_discussions(
            repo, repo_type="dataset", discussion_type="pull_request", discussion_status="open"
        ):
            prs.append({"num": item.num, "ref": f"refs/pr/{item.num}", "title": item.title,
                        "status": item.status})
        return sorted(prs, key=lambda p: p["num"])

    def tree(self, snap: HubSnapshot, revision: str) -> list[str] | None:
        """File list of ``revision``; fetched on demand, ``None`` if it does not exist."""
        if revision in snap.trees:
            return snap.trees[revision]
        try:
            snap.trees[revision] = self._files(snap.canonical_id, revision)
        except Exception as exc:  # noqa: BLE001
            # 없는 리비전(404)과 연결 실패를 구분해 둔다 — record 가 연결 실패를 조용히 넘기지 않게
            self.tree_errors[(snap.canonical_id, revision)] = (classify_error(exc), _short_error(exc))
            return None
        return snap.trees[revision]


class RecordedHub:
    """Replays snapshot JSON files recorded by ``unscript-datasets record``."""

    offline = True

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._index: dict[str, HubSnapshot] = {}
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                snap = HubSnapshot.from_json(data)
            except (OSError, ValueError, TypeError) as exc:
                raise SnapshotFormatError(f"{path}: {exc}") from exc
            if snap.status not in RECORDABLE_STATUSES:
                # 예: 예전 버전이 Hub 에 닿지 못한 채 저장한 파일. 재생하면 영원히 UNKNOWN 으로 조용히 통과하므로 거부한다.
                raise IncompleteSnapshotError(f"{path} (status: {snap.status})")
            for key in {snap.canonical_id, snap.requested_id, *snap.aliases}:
                self._index[key] = snap

    def snapshot(self, dataset_id: str) -> HubSnapshot:
        snap = self._index.get(dataset_id)
        if snap is None:
            return HubSnapshot(requested_id=dataset_id, canonical_id=dataset_id, status="not_recorded",
                               aliases=[dataset_id])
        return snap

    def tree(self, snap: HubSnapshot, revision: str) -> list[str] | None:
        return snap.trees.get(revision)


@dataclass
class RecordOutcome:
    """What ``record`` did for one dataset id."""

    dataset_id: str
    status: str                       # ok | gated | not_found | network
    path: Path | None = None          # None: nothing was written
    error: str = ""                   # one-line cause when the Hub did not answer
    missing_revisions: list[str] = field(default_factory=list)  # --revision values the Hub does not have


def record(hub: LiveHub, dataset_ids: list[str], out_dir: str | Path,
           revisions: list[str] | None = None) -> list[RecordOutcome]:
    """Record the live state of each dataset as ``<owner>__<name>.json`` in ``out_dir``.

    Only real Hub answers are written.  When the Hub cannot be reached (status
    ``network``, also for a failed ``--revision`` lookup) nothing is written for
    that dataset and an existing file is left untouched, so an ``--offline``
    replay never mistakes "no answer" for a recorded state.
    """
    out = Path(out_dir)
    outcomes: list[RecordOutcome] = []
    for dataset_id in dataset_ids:
        snap = hub.snapshot(dataset_id)
        outcome = RecordOutcome(dataset_id, snap.status, error=hub.errors.get(dataset_id, ""))
        outcomes.append(outcome)
        if snap.status == "ok":
            for revision in revisions or []:
                if hub.tree(snap, revision) is not None:
                    continue
                status, detail = hub.tree_errors.get((snap.canonical_id, revision), ("not_found", ""))
                if status == "not_found":
                    outcome.missing_revisions.append(revision)
                else:
                    # 리비전 하나라도 연결 실패면 불완전한 녹화 — 통째로 저장하지 않는다
                    outcome.status, outcome.error = "network", detail
                    break
        if outcome.status not in RECORDABLE_STATUSES:
            continue
        out.mkdir(parents=True, exist_ok=True)
        target = out / snapshot_filename(snap.canonical_id)
        if target.exists():
            try:
                previous = HubSnapshot.from_json(json.loads(target.read_text(encoding="utf-8")))
                for alias in previous.aliases:
                    if alias not in snap.aliases:
                        snap.aliases.append(alias)
                for revision, files in previous.trees.items():
                    snap.trees.setdefault(revision, files)
            except (OSError, ValueError, TypeError):
                pass
        snap.aliases = sorted(set(snap.aliases) | {snap.requested_id})
        target.write_text(json.dumps(snap.to_json(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        outcome.path = target
    return outcomes
