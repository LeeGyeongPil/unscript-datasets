"""The command a dataset owner runs to convert a script dataset to Parquet."""

from __future__ import annotations

import re

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*(/[A-Za-z0-9][A-Za-z0-9_.\-]*)?")
_REV = re.compile(r"[A-Za-z0-9_.\-/]+")


class InvalidDatasetId(ValueError):
    """The dataset id or revision contains characters a Hub id cannot have."""


def owner_command(dataset_id: str, revision: str | None = None) -> str:
    """Isolated ``datasets-cli convert_to_parquet`` command (``datasets==3.6.0`` still ships it)."""
    if not _ID.fullmatch(dataset_id):
        raise InvalidDatasetId(dataset_id)
    if revision is not None and not _REV.fullmatch(revision):
        raise InvalidDatasetId(revision)
    rev = f" --revision {revision}" if revision else ""
    return (
        'uvx --python 3.12 --from "datasets==3.6.0" '
        f"datasets-cli convert_to_parquet {dataset_id}{rev} --trust_remote_code"
    )
