"""Shared test helpers: recorded snapshots, synthetic variants and a fake Hub API."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from unscript_datasets.extract import extract_calls
from unscript_datasets.hub import RecordedHub
from unscript_datasets.judge import Verdict, judge_call

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
HUB_DIR = EXAMPLES / "hub"

PARQUET_CONLL = (
    "hf://datasets/eriktks/conll2003@refs%2Fconvert%2Fparquet/conll2003/validation/*.parquet"
)


def make_hub_dir(tmp_path: Path, **edits) -> Path:
    """Copy the recorded snapshots to ``tmp_path/hub`` and apply ``edits``.

    ``edits`` maps a snapshot file stem to a function that changes the loaded dict;
    changed snapshots are marked ``synthetic: true``.
    """
    target = tmp_path / "hub"
    shutil.copytree(HUB_DIR, target)
    for stem, edit in edits.items():
        path = target / f"{stem}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        edit(data)
        data["synthetic"] = True
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return target


def judge_source(source: str, hub_dir: Path = HUB_DIR) -> list[Verdict]:
    hub = RecordedHub(hub_dir)
    sites = extract_calls(source, "t.py")
    return [judge_call(site, hub) for site in sites]


def judge_one(source: str, hub_dir: Path = HUB_DIR) -> Verdict:
    verdicts = judge_source(source, hub_dir)
    assert len(verdicts) == 1
    return verdicts[0]


# --- fake huggingface_hub.HfApi ------------------------------------------------


class FakeHttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


class FakeApi:
    """Answers like HfApi for a few repositories; ``conll2003`` is a 307 alias."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.files = {
            ("eriktks/conll2003", "main"): ["README.md", "conll2003.py"],
            ("eriktks/conll2003", "refs/convert/parquet"): [".gitattributes", "conll2003/validation/0000.parquet"],
            ("eriktks/conll2003", "refs/pr/15"): ["README.md", "conll2003/validation-0.parquet"],
            ("eriktks/conll2003", "abc123"): ["conll2003.py"],
        }

    def dataset_info(self, repo_id, revision=None):
        self.calls.append(f"dataset_info {repo_id} {revision}")
        if repo_id in ("conll2003", "eriktks/conll2003"):
            when = dt.datetime(2024, 5, 16, tzinfo=dt.timezone.utc) if revision else dt.datetime(2024, 1, 18, 9, 34, 17)
            return SimpleNamespace(id="eriktks/conll2003", sha="deadbeef", last_modified=when, card_data=None)
        if repo_id == "gaia-benchmark/GAIA":
            raise FakeHttpError(401)
        if repo_id == "flaky/net":
            raise ConnectionError("boom")
        raise FakeHttpError(404)

    def list_repo_refs(self, repo_id, repo_type=None):
        return SimpleNamespace(converts=[SimpleNamespace(name="parquet")])

    def list_repo_files(self, repo_id, repo_type=None, revision=None):
        self.calls.append(f"files {repo_id} {revision}")
        try:
            return list(self.files[(repo_id, revision)])
        except KeyError:
            raise FakeHttpError(404) from None

    def get_repo_discussions(self, repo_id, **kwargs):
        yield SimpleNamespace(num=15, title="Convert dataset to Parquet", status="open")
        yield SimpleNamespace(num=9, title="Typo in readme", status="open")
