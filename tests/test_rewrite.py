import ast
import json
import shutil

from helpers import EXAMPLES, HUB_DIR, PARQUET_CONLL, judge_one
from unscript_datasets.cli import main
from unscript_datasets.judge import glob_matches

HEAD = "from datasets import load_dataset\n"


def test_parquet_keeps_config_and_split_exactly():
    v = judge_one(HEAD + 'load_dataset("eriktks/conll2003", "conll2003", split="validation", revision="refs/convert/parquet")\n')
    assert v.replacement == f'load_dataset("parquet", data_files={{"validation": "{PARQUET_CONLL}"}}, split="validation")'


def test_never_emits_bare_convert_revision():
    sources = [
        'load_dataset("eriktks/conll2003", "conll2003", split="train", revision="refs/convert/parquet")',
        'load_dataset("eriktks/conll2003", split="train")',
        'load_dataset("mstz/abalone", "binary", revision="56bbaf9671")',
    ]
    for source in sources:
        v = judge_one(HEAD + source + "\n")
        assert v.replacement and "revision=\"refs/convert/parquet\"" not in v.replacement


def test_generated_code_parses_and_globs_match_recorded_tree():
    snapshot = json.loads((HUB_DIR / "eriktks__conll2003.json").read_text())
    tree = snapshot["trees"]["refs/convert/parquet"]
    v = judge_one(HEAD + 'load_dataset("eriktks/conll2003", "conll2003", revision="refs/convert/parquet")\n')
    ast.parse(v.replacement, mode="eval")
    call = ast.parse(v.replacement, mode="eval").body
    data_files = next(k.value for k in call.keywords if k.arg == "data_files")
    globs = [c.value for c in data_files.values]
    assert len(globs) == 3  # no split given -> every converted split
    for glob in globs:
        assert glob_matches(glob, tree), glob


def test_pr_replaces_existing_revision_in_place():
    from helpers import make_hub_dir
    import tempfile, pathlib

    def pin_pr(data):
        data["trees"]["v1"] = ["conll2003.py"]
    with tempfile.TemporaryDirectory() as tmp:
        hub = make_hub_dir(pathlib.Path(tmp), **{"eriktks__conll2003": pin_pr})
        v = judge_one(HEAD + 'load_dataset("eriktks/conll2003", revision="v1", streaming=True)\n', hub)
    assert v.replacement == 'load_dataset("eriktks/conll2003", revision="refs/pr/15", streaming=True)'


def test_alias_and_untouched_kwargs_stay_verbatim():
    src = 'from datasets import load_dataset as ld\nx = ld("conll2003", "conll2003", split="train", streaming=True, trust_remote_code=True)\n'
    from unscript_datasets.extract import extract_calls
    from unscript_datasets.hub import RecordedHub
    from unscript_datasets.judge import judge_call

    [site] = extract_calls(src, "t.py")
    v = judge_call(site, RecordedHub(HUB_DIR))
    assert v.replacement == 'ld("conll2003", "conll2003", split="train", streaming=True, revision="refs/pr/15")'


def _copy_examples(tmp_path):
    work = tmp_path / "ex"
    shutil.copytree(EXAMPLES, work, ignore=shutil.ignore_patterns("hub"))
    return work


def test_fix_dry_run_changes_nothing(tmp_path, capsys):
    work = _copy_examples(tmp_path)
    before = (work / "eval_ner.py").read_bytes()
    assert main(["fix", str(work), "--offline", str(HUB_DIR), "--lang", "en"]) == 1
    assert (work / "eval_ner.py").read_bytes() == before
    assert "+conll = load_dataset(" in capsys.readouterr().out


def test_fix_write_changes_only_call_spans_and_is_idempotent(tmp_path, capsys):
    work = _copy_examples(tmp_path)
    target = work / "eval_ner.py"
    original = target.read_text()
    code = main(["fix", str(work), "--offline", str(HUB_DIR), "--write", "--lang", "en"])
    assert code == 1  # the OWNER call (Patho-Bench) still needs the dataset owner
    fixed = target.read_text()
    ast.parse(fixed)
    changed = [(a, b) for a, b in zip(original.splitlines(), fixed.splitlines()) if a != b]
    assert len(changed) == 4                     # PR, PR, PARQUET, PIN
    assert "trust_remote_code" not in fixed.split("conll = ")[1].split("\n")[0]
    assert 'ld("conll2003", "conll2003", split="train", streaming=True, revision="refs/pr/15")' in fixed
    assert 'load_dataset("nyu-mll/glue", "mrpc", split="validation")' in fixed   # untouched call
    assert 'datasets.load_dataset("MahmoodLab/Patho-Bench", trust_remote_code=True)' in fixed  # OWNER: untouched
    capsys.readouterr()
    before_second = target.read_bytes()
    assert main(["fix", str(work), "--offline", str(HUB_DIR), "--write", "--lang", "en"]) == 1
    assert target.read_bytes() == before_second
    assert "Nothing to change." in capsys.readouterr().out


def test_comment_inside_call_warns(tmp_path):
    src = HEAD + 'load_dataset(\n    "eriktks/conll2003",  # NER\n    split="train",\n)\n'
    v = judge_one(src)
    assert v.code == "PR" and "comment_in_call" in v.warnings
