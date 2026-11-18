import json
import shutil

from helpers import EXAMPLES, HUB_DIR
from unscript_datasets.cli import main


def test_notebook_fix_changes_only_edited_cells(tmp_path, capsys):
    nb = tmp_path / "explore.ipynb"
    shutil.copy(EXAMPLES / "explore.ipynb", nb)
    original_text = nb.read_text()
    original = json.loads(original_text)
    assert main(["fix", str(nb), "--offline", str(HUB_DIR), "--write", "--lang", "en"]) == 0
    fixed_text = nb.read_text()
    fixed = json.loads(fixed_text)                       # still valid JSON
    assert fixed["metadata"] == original["metadata"]
    assert fixed["nbformat"] == original["nbformat"]
    for index in (0, 1):                                 # untouched cells are identical
        assert fixed["cells"][index] == original["cells"][index]
    assert fixed["cells"][2]["outputs"] == original["cells"][2]["outputs"]
    assert fixed["cells"][2]["execution_count"] == 2
    src2 = "".join(fixed["cells"][2]["source"])
    assert 'load_dataset("mstz/abalone", "binary", streaming=True)' in src2
    assert 'revision="refs/pr/15"' in "".join(fixed["cells"][3]["source"])
    # formatting kept: same indent, trailing newline, only source lines differ
    assert fixed_text.endswith("\n") and fixed_text.startswith('{\n "cells"')
    diff = [l for l in set(fixed_text.splitlines()) ^ set(original_text.splitlines())]
    assert all("load_dataset" in line for line in diff)
    capsys.readouterr()


def test_notebook_second_run_is_noop(tmp_path, capsys):
    nb = tmp_path / "explore.ipynb"
    shutil.copy(EXAMPLES / "explore.ipynb", nb)
    main(["fix", str(nb), "--offline", str(HUB_DIR), "--write", "--lang", "en"])
    first = nb.read_bytes()
    capsys.readouterr()
    main(["fix", str(nb), "--offline", str(HUB_DIR), "--write", "--lang", "en"])
    assert nb.read_bytes() == first
    assert "Nothing to change." in capsys.readouterr().out
