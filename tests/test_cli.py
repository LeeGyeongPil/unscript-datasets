import json
import re

import pytest

from helpers import EXAMPLES, HUB_DIR
from unscript_datasets.cli import main

VERDICT_WORDS = ["PR", "PARQUET", "PIN", "OK", "OWNER", "UNKNOWN", "SKIP", "DYNAMIC"]


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_scan_examples_offline_deterministic(capsys):
    args = ("scan", str(EXAMPLES), "--offline", str(HUB_DIR), "--lang", "en")
    code1, out1, _ = run(capsys, *args)
    code2, out2, _ = run(capsys, *args)
    assert code1 == code2 == 1
    assert out1 == out2
    for word in VERDICT_WORDS:
        assert re.search(rf"\s{word}\s", out1), word
    assert "Practice mode (mock)" in out1


def test_json_shape_and_sorting(capsys):
    code, out, _ = run(capsys, "scan", str(EXAMPLES), "--offline", str(HUB_DIR), "--json")
    data = json.loads(out)
    assert code == 1 and data["version"] == 1
    keys = {"file", "line", "col", "dataset", "canonical_id", "config", "split", "revision",
            "verdict", "reason", "replacement", "warnings"}
    assert all(keys <= set(c) for c in data["calls"])
    order = [(c["file"], c["cell"] if c["cell"] is not None else -1, c["line"], c["col"]) for c in data["calls"]]
    assert order == sorted(order)
    assert sum(data["summary"].values()) == len(data["calls"])
    assert set(data["summary"]) <= {"OK", "PIN", "PR", "PARQUET", "OWNER", "MANUAL", "UNKNOWN", "DYNAMIC", "SKIP"}
    pr = next(c for c in data["calls"] if c["verdict"] == "PR")
    assert pr["canonical_id"] == "eriktks/conll2003" and 'revision="refs/pr/15"' in pr["replacement"]


def test_json_is_language_independent(capsys):
    _, en, _ = run(capsys, "scan", str(EXAMPLES), "--offline", str(HUB_DIR), "--json", "--lang", "en")
    _, ko, _ = run(capsys, "scan", str(EXAMPLES), "--offline", str(HUB_DIR), "--json", "--lang", "ko")
    assert en == ko


def _write(tmp_path, text):
    (tmp_path / "a.py").write_text(text)
    return str(tmp_path)


def test_exit_zero_when_nothing_to_fix(tmp_path, capsys):
    folder = _write(tmp_path, 'from datasets import load_dataset\nload_dataset("nyu-mll/glue", "mrpc")\n')
    code, out, _ = run(capsys, "scan", folder, "--offline", str(HUB_DIR))
    assert code == 0 and "OK" in out


def test_strict_counts_unknown_and_dynamic(tmp_path, capsys):
    folder = _write(tmp_path, 'from datasets import load_dataset\nload_dataset("gaia-benchmark/GAIA")\nload_dataset(x)\n')
    assert run(capsys, "scan", folder, "--offline", str(HUB_DIR))[0] == 0
    assert run(capsys, "scan", folder, "--offline", str(HUB_DIR), "--strict")[0] == 1


def test_usage_errors_exit_two(tmp_path, capsys):
    code, _, err = run(capsys, "scan", str(tmp_path / "missing"), "--offline", str(HUB_DIR))
    assert code == 2 and "path not found" in err
    code, _, err = run(capsys, "scan", str(EXAMPLES), "--offline", str(tmp_path / "nohub"), "--lang", "en")
    assert code == 2 and "snapshot folder not found" in err
    code, _, err = run(capsys, "scan", str(EXAMPLES), "--lang", "fr")
    assert code == 2 and "invalid --lang" in err
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_fix_exit_code_after_write(tmp_path, capsys):
    folder = _write(tmp_path, 'from datasets import load_dataset\nload_dataset("eriktks/conll2003", trust_remote_code=True)\n')
    assert run(capsys, "fix", folder, "--offline", str(HUB_DIR), "--write")[0] == 0
    assert 'revision="refs/pr/15"' in (tmp_path / "a.py").read_text()


def test_unreadable_snapshot_is_usage_error(tmp_path, capsys):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "x.json").write_text("{not json")
    code, _, err = run(capsys, "scan", str(EXAMPLES), "--offline", str(hub))
    assert code == 2 and "unreadable snapshot" in err


def test_fix_warns_about_lost_comment_and_unreadable_files(tmp_path, capsys):
    (tmp_path / "a.py").write_text(
        'from datasets import load_dataset\n'
        'load_dataset(\n    "eriktks/conll2003",  # keep me\n    trust_remote_code=True,\n)\n'
    )
    (tmp_path / "bad.py").write_bytes(b"\xff\xfe\x00bad")
    (tmp_path / "nb.ipynb").write_text("{broken")
    code, out, err = run(capsys, "fix", str(tmp_path), "--offline", str(HUB_DIR), "--write")
    assert "comment inside the call will be lost" in err
    assert "bad.py: not UTF-8 text; re-save it as UTF-8" in err
    assert "nb.ipynb: not valid notebook JSON" in err
    code, out, err = run(capsys, "fix", str(tmp_path), "--offline", str(HUB_DIR), "--lang", "ko")
    assert "UTF-8 로 다시 저장하세요" in err


def test_non_source_file_is_rejected_with_a_hint(tmp_path, capsys):
    readme = tmp_path / "README.md"
    readme.write_text("# hi\n")
    code, _, err = run(capsys, "scan", str(readme))
    assert code == 2 and "not a .py/.ipynb file" in err and "Pass a Python file" in err
    code, _, err = run(capsys, "scan", str(readme), "--lang", "ko")
    assert code == 2 and ".py/.ipynb 파일이 아님" in err
