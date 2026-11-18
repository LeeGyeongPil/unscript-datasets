import json
import textwrap

from unscript_datasets.extract import extract_calls, mask_magics, scan_paths


def calls(source: str, **kw):
    return extract_calls(textwrap.dedent(source), "t.py", **kw)


def test_alias_import_and_positional_config():
    [site] = calls("""
        from datasets import load_dataset as ld
        ld("eriktks/conll2003", "conll2003", split="validation", revision="abc", trust_remote_code=True)
    """)
    assert (site.kind, site.dataset, site.config, site.split, site.revision) == (
        "HUB", "eriktks/conll2003", "conll2003", "validation", "abc")
    assert site.func_src == "ld"


def test_module_attribute_and_keyword_path():
    sites = calls("""
        import datasets as ds
        import datasets
        a = ds.load_dataset(path="mstz/abalone", name="binary")
        b = datasets.load_dataset("nyu-mll/glue", "mrpc", None, None, "train")
    """)
    assert [s.dataset for s in sites] == ["mstz/abalone", "nyu-mll/glue"]
    assert sites[0].config == "binary"
    assert sites[1].split == "train"  # fifth positional argument


def test_unrelated_load_dataset_is_ignored():
    assert calls("""
        def load_dataset(x): ...
        load_dataset("a/b")
    """) == []


def test_dynamic_and_skip_kinds(tmp_path):
    (tmp_path / "data").mkdir()
    sites = calls(f"""
        from datasets import load_dataset
        name = "a/b"
        load_dataset(name)
        load_dataset("a/b", revision=rev)
        load_dataset("json", data_files="x.json")
        load_dataset("./local")
        load_dataset("data")
        load_dataset(**kwargs)
    """, roots=[tmp_path])
    kinds = [(s.kind, s.reason) for s in sites]
    assert kinds == [
        ("DYNAMIC", "dynamic_arg"), ("DYNAMIC", "dynamic_arg"), ("SKIP", "packaged_builder"),
        ("SKIP", "local_path"), ("SKIP", "local_path"), ("DYNAMIC", "dynamic_arg"),
    ]


def test_span_and_non_ascii_column():
    source = 'from datasets import load_dataset\nx = "한글"; y = load_dataset("a/b")\n'
    [site] = extract_calls(source, "t.py")
    assert site.original == 'load_dataset("a/b")'
    assert (site.line, site.col) == (2, 15)  # character column, not byte column


def test_multiline_call_and_comments():
    [site] = calls("""
        from datasets import load_dataset
        d = load_dataset(
            "a/b",  # keep me
            split="train",
        )
        e = load_dataset("a/b", name="x#y")
    """)[:1]
    assert site.has_comment and site.line == 3
    last = calls("""
        from datasets import load_dataset
        e = load_dataset("a/b", name="x#y")
    """)[0]
    assert not last.has_comment and last.config == "x#y"


def test_notebook_magics_masked_and_line_numbers_kept(tmp_path):
    cells = [
        {"cell_type": "code", "metadata": {}, "outputs": [], "source": [
            "!pip install datasets\n", "%time x = 1\n", "from datasets import load_dataset\n"]},
        {"cell_type": "markdown", "metadata": {}, "source": ["load_dataset('x/y')"]},
        {"cell_type": "code", "metadata": {}, "outputs": [], "source": [
            "!ls\n", "ds = load_dataset('gaia-benchmark/GAIA', '2023_all')\n"]},
    ]
    nb = tmp_path / "n.ipynb"
    nb.write_text(json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}))
    result = scan_paths([str(nb)])
    [site] = result.calls
    assert (site.cell, site.line, site.dataset, site.config) == (2, 2, "gaia-benchmark/GAIA", "2023_all")
    assert site.location.endswith("n.ipynb#cell2:2")
    assert mask_magics("!ls\nx = 1\n") == "\nx = 1\n"


def test_syntax_error_is_warned_and_skipped(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n")
    (tmp_path / "good.py").write_text('from datasets import load_dataset\nload_dataset("a/b")\n')
    result = scan_paths([str(tmp_path)])
    assert [c.dataset for c in result.calls] == ["a/b"]
    assert len(result.warnings) == 1 and result.warnings[0].startswith("syntax_error=")
    assert result.files == 2


def test_default_excludes_and_custom_exclude(tmp_path):
    for folder in (".venv", "node_modules", "src"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "m.py").write_text('from datasets import load_dataset\nload_dataset("a/b")\n')
    assert len(scan_paths([str(tmp_path)]).calls) == 1
    assert len(scan_paths([str(tmp_path)], ["src"]).calls) == 0
