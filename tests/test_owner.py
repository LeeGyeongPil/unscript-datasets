import pytest

from unscript_datasets.cli import main
from unscript_datasets.owner import InvalidDatasetId, owner_command

EXPECTED = 'uvx --python 3.12 --from "datasets==3.6.0" datasets-cli convert_to_parquet eriktks/conll2003 --trust_remote_code'


def test_owner_cmd_line(capsys):
    assert main(["owner-cmd", "eriktks/conll2003", "--lang", "en"]) == 0
    assert EXPECTED in capsys.readouterr().out.splitlines()


def test_revision_and_validation():
    assert owner_command("a/b", "refs/pr/3").endswith("convert_to_parquet a/b --revision refs/pr/3 --trust_remote_code")
    for bad in ("a/b; rm -rf /", "a b", "../x", ""):
        with pytest.raises(InvalidDatasetId):
            owner_command(bad)
    with pytest.raises(InvalidDatasetId):
        owner_command("a/b", "x;y")


def test_bad_id_exits_two(capsys):
    assert main(["owner-cmd", "a b", "--lang", "en"]) == 2
    assert "invalid dataset id" in capsys.readouterr().err
