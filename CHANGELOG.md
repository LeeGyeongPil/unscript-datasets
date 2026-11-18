# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.2] - 2026-11-18

### Fixed
- CI: use only GitHub's own actions (`actions/checkout`, `actions/setup-python`) and install uv 0.13.6 with pip.
- CI: test on Python 3.10 and 3.12 (the `requires-python` floor and the current main version).
- CI: run pytest with `-ra` so skipped and failed tests are listed in detail.

## [0.1.1] - 2026-11-18

### Fixed
- CI: move to `actions/checkout@v6` and `astral-sh/setup-uv@v7` (Node 24 runtimes) so the test workflow runs again.
- CI: pin the test interpreter to Python 3.12 so a new CPython release cannot change the tested version or force source builds.

## [0.1.0] - 2026-11-18

### Added
- `scan`: finds `load_dataset` calls in `.py` and `.ipynb` files and judges each Hub dataset (`OK`, `PIN`, `PR`, `PARQUET`, `OWNER`, `MANUAL`, `UNKNOWN`, plus `DYNAMIC` and `SKIP`).
- `fix`: prints a diff by default, `--write` applies it; only the call span changes, notebooks keep other cells, outputs and metadata.
- `owner-cmd`: prints the isolated `datasets-cli convert_to_parquet` command for dataset owners.
- `record` and `--offline`: record Hub state as snapshot JSON files and replay it without network. `record` saves only datasets the Hub answered for (exit codes 0/1/2), and `--offline` rejects snapshots written without a Hub answer.
- Real-mode `scan` and `fix` print the cause and a fix hint on stderr when the Hub does not answer.
- `--json` output, exit codes 0/1/2 and `--strict`.
- English and Korean UI (`--lang`, `UNSCRIPT_DATASETS_LANG`, `LANG`).
