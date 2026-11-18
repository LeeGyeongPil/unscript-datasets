from helpers import judge_one, judge_source, make_hub_dir

HEAD = "from datasets import load_dataset\n"


def one(call: str, hub_dir=None):
    kw = {"hub_dir": hub_dir} if hub_dir else {}
    return judge_one(HEAD + call + "\n", **kw)


def test_conll2003_open_pr():
    v = one('load_dataset("eriktks/conll2003", split="validation")')
    assert (v.code, v.reason) == ("PR", "open_conversion_pr")
    assert 'revision="refs/pr/15"' in v.replacement
    assert any(w.endswith("/discussions/15") for w in v.warnings)


def test_bare_name_resolves_to_canonical_id():
    v = one('load_dataset("conll2003", "conll2003")')
    assert v.code == "PR" and v.canonical_id == "eriktks/conll2003"


def test_abalone_pin_and_main():
    pinned = one('load_dataset("mstz/abalone", "binary", revision="56bbaf9671")')
    assert pinned.code == "PIN" and "revision" not in pinned.replacement
    assert one('load_dataset("mstz/abalone", "binary")').code == "OK"


def test_pin_warns_when_config_not_in_card():
    v = one('load_dataset("mstz/abalone", "nope", revision="56bbaf9671")')
    assert v.code == "PIN" and "config_not_in_card=nope" in v.warnings


def test_glue_and_other_named_py_are_ok():
    assert one('load_dataset("nyu-mll/glue", "mrpc")').code == "OK"
    p3 = one('load_dataset("bigscience/P3", "x")')
    assert (p3.code, p3.reason) == ("OK", "no_script")


def test_gated_and_owner():
    gated = one('load_dataset("gaia-benchmark/GAIA", "2023_all")')
    assert (gated.code, gated.reason) == ("UNKNOWN", "gated")
    owner = one('load_dataset("MahmoodLab/Patho-Bench")')
    assert (owner.code, owner.reason) == ("OWNER", "no_convert_branch")


def test_not_recorded_offline():
    v = one('load_dataset("some/unrecorded")')
    assert (v.code, v.reason) == ("UNKNOWN", "not_recorded")


def test_trap_pattern_becomes_parquet():
    v = one('load_dataset("eriktks/conll2003", "conll2003", split="validation", revision="refs/convert/parquet")')
    assert v.code == "PARQUET"


def test_convert_revision_without_config_is_ok():
    assert one('load_dataset("eriktks/conll2003", revision="refs/convert/parquet")').code == "OK"


def test_closed_pr_variant_gives_parquet(tmp_path):
    def close(data):
        data["pull_requests"][0]["status"] = "closed"
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": close})
    v = one('load_dataset("eriktks/conll2003", split="validation")', hub)
    assert (v.code, v.reason) == ("PARQUET", "convert_branch")


def test_pr_selection_prefers_highest_number_with_parquet(tmp_path):
    def more_prs(data):
        data["pull_requests"] += [
            {"num": 20, "ref": "refs/pr/20", "title": "Convert to PARQUET", "status": "open"},
            {"num": 30, "ref": "refs/pr/30", "title": "Convert to parquet", "status": "draft"},
            {"num": 40, "ref": "refs/pr/40", "title": "Convert to parquet", "status": "open"},
            {"num": 50, "ref": "refs/pr/50", "title": "unrelated", "status": "open"},
        ]
        data["trees"]["refs/pr/20"] = ["README.md", "conll2003/a.parquet"]
        data["trees"]["refs/pr/40"] = ["conll2003.py", "x.parquet"]   # still has the script
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": more_prs})
    v = one('load_dataset("eriktks/conll2003")', hub)
    assert v.code == "PR" and v.detail == "#20"


def test_partial_only_variant_is_owner(tmp_path):
    def partial(data):
        data["pull_requests"] = []
        data["trees"]["refs/convert/parquet"] = ["conll2003/partial-train/0000.parquet"]
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": partial})
    v = one('load_dataset("eriktks/conll2003", "conll2003", split="train")', hub)
    assert (v.code, v.reason) == ("OWNER", "partial_split_only")


def test_missing_config_and_split_are_owner(tmp_path):
    def no_pr(data):
        data["pull_requests"] = []
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": no_pr})
    assert one('load_dataset("eriktks/conll2003", "other")', hub).reason == "config_not_in_convert"
    assert one('load_dataset("eriktks/conll2003", split="dev")', hub).reason == "split_not_found_in_convert"


def test_multi_config_without_name_is_manual(tmp_path):
    def multi(data):
        data["pull_requests"] = []
        data["trees"]["refs/convert/parquet"] = ["a/train/0.parquet", "b/train/0.parquet"]
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": multi})
    v = one('load_dataset("eriktks/conll2003")', hub)
    assert (v.code, v.reason, v.detail) == ("MANUAL", "multiple_configs", "a, b")


def test_manual_for_data_files_and_split_expression(tmp_path):
    def no_pr(data):
        data["pull_requests"] = []
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": no_pr})
    v = one('load_dataset("eriktks/conll2003", "conll2003", data_files="x")', hub)
    assert (v.code, v.reason) == ("MANUAL", "unsupported_args")
    v = one('load_dataset("eriktks/conll2003", "conll2003", split=get())', hub)
    assert v.reason == "split_unparseable"


def test_split_slice_and_union_map_to_split_dirs(tmp_path):
    def no_pr(data):
        data["pull_requests"] = []
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": no_pr})
    v = one('load_dataset("eriktks/conll2003", "conll2003", split="train[:10%]+test")', hub)
    assert v.code == "PARQUET"
    assert '"train": "hf://' in v.replacement and '"test": "hf://' in v.replacement
    assert 'split="train[:10%]+test"' in v.replacement


def test_stale_convert_branch_warns(tmp_path):
    def stale(data):
        data["pull_requests"] = []
        data["convert_last_modified"] = "2020-01-01T00:00:00Z"
    hub = make_hub_dir(tmp_path, **{"eriktks__conll2003": stale})
    v = one('load_dataset("eriktks/conll2003", "conll2003", split="train")', hub)
    assert v.code == "PARQUET" and "stale_convert_branch" in v.warnings


def test_unrecorded_revision_is_unknown():
    v = one('load_dataset("eriktks/conll2003", revision="v1")')
    assert (v.code, v.reason) == ("UNKNOWN", "not_recorded")


def test_dynamic_and_skip_pass_through():
    codes = [v.code for v in judge_source(HEAD + 'load_dataset(x)\nload_dataset("json", data_files="a")\n')]
    assert codes == ["DYNAMIC", "SKIP"]
