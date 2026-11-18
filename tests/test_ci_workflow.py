"""Guard the minimal CI workflow shape (text checks only, no YAML dependency)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def code_lines():
    return [ln for ln in CI.splitlines() if not ln.strip().startswith("#")]


def test_single_job_ubuntu_with_timeout_and_read_permissions():
    lines = code_lines()
    jobs_at = lines.index("jobs:")
    job_names = [ln for ln in lines[jobs_at + 1:] if re.match(r"^  [A-Za-z0-9_-]+:\s*$", ln)]
    assert len(job_names) == 1
    assert "runs-on: ubuntu-latest" in CI
    assert re.search(r"timeout-minutes: \d+", CI)
    assert re.search(r"^permissions:\n  contents: read$", CI, re.M)


def test_only_two_sha_pinned_official_actions():
    uses = [ln.strip() for ln in code_lines() if "uses:" in ln]
    assert len(uses) == 2
    assert re.match(r"- uses: actions/checkout@[0-9a-f]{40}( #.*)?$", uses[0])
    assert re.match(r"- uses: actions/setup-python@[0-9a-f]{40}( #.*)?$", uses[1])
    assert CI.count('python-version: "3.12"') == 1


def test_run_lines_are_the_two_commands():
    runs = [ln.strip() for ln in code_lines() if re.match(r"\s*- run:", ln)]
    assert runs == [
        '- run: python -m pip install -e ".[test]"',
        '- run: python -m pytest -q -m "not network"',
    ]


def test_forbidden_strings_absent():
    body = "\n".join(code_lines()).lower()
    for word in ("matrix", "schedule", "cache", "astral-sh", "setup-uv", "uv sync", "uv run", "install uv"):
        assert word not in body, word


def test_pyproject_has_test_extra_and_network_marker():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"\[project\.optional-dependencies\]\ntest = \[\"pytest>=8\"\]", text)
    assert re.search(r"markers = \[\"network: ", text)
