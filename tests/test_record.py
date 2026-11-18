import json

import pytest

from helpers import EXAMPLES, FakeApi, make_hub_dir
from unscript_datasets.cli import main
from unscript_datasets.extract import extract_calls
from unscript_datasets.hub import IncompleteSnapshotError, LiveHub, RecordedHub, record
from unscript_datasets.judge import judge_call


def recorded(tmp_path, ids, revisions=None, api=None):
    hub = LiveHub(api=api or FakeApi())
    return record(hub, ids, tmp_path, revisions)


class RevisionNetFailApi(FakeApi):
    """Main state answers, but the extra revision lookup loses the connection."""

    def list_repo_files(self, repo_id, repo_type=None, revision=None):
        if revision == "netfail":
            raise ConnectionError("connection reset")
        return super().list_repo_files(repo_id, repo_type=repo_type, revision=revision)


class DownApi(FakeApi):
    """Every request fails like an unreachable Hub (e.g. an SSL trust-store problem)."""

    def dataset_info(self, repo_id, revision=None):
        raise OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")


def test_record_normalizes_snapshot_format(tmp_path):
    [outcome] = recorded(tmp_path, ["eriktks/conll2003"], ["abc123"])
    path = outcome.path
    assert path.name == "eriktks__conll2003.json"
    data = json.loads(path.read_text())
    assert data["format"] == 1 and data["status"] == "ok" and data["canonical_id"] == "eriktks/conll2003"
    assert data["main_last_modified"] == "2024-01-18T09:34:17Z"
    assert data["convert_last_modified"] == "2024-05-16T00:00:00Z"
    assert data["pull_requests"] == [
        {"num": 9, "ref": "refs/pr/9", "title": "Typo in readme", "status": "open"},
        {"num": 15, "ref": "refs/pr/15", "title": "Convert dataset to Parquet", "status": "open"},
    ]
    assert set(data["trees"]) == {"main", "refs/convert/parquet", "refs/pr/15", "abc123"}  # PR 9 is not a conversion
    assert data["synthetic"] is False


def test_canonical_redirect_is_recorded_as_alias(tmp_path):
    recorded(tmp_path, ["conll2003"])
    hub = RecordedHub(tmp_path)
    assert hub.snapshot("conll2003").canonical_id == "eriktks/conll2003"
    assert hub.snapshot("eriktks/conll2003").status == "ok"


def test_401_and_404_are_recorded_but_network_errors_are_not(tmp_path):
    outcomes = recorded(tmp_path, ["gaia-benchmark/GAIA", "nobody/nothing", "flaky/net"])
    assert [o.status for o in outcomes] == ["gated", "not_found", "network"]
    gated, missing, net = outcomes
    assert [json.loads(o.path.read_text())["status"] for o in (gated, missing)] == ["gated", "not_found"]
    # Hub 가 대답하지 않은 데이터셋은 파일을 남기지 않는다(재생 때 조용히 UNKNOWN 이 되지 않게)
    assert net.path is None and "ConnectionError: boom" in net.error
    assert not (tmp_path / "flaky__net.json").exists()
    hub = RecordedHub(tmp_path)
    [site] = extract_calls('from datasets import load_dataset\nload_dataset("nobody/nothing")\n', "t.py")
    v = judge_call(site, hub)
    assert (v.code, v.reason) == ("UNKNOWN", "not_found")
    [site] = extract_calls('from datasets import load_dataset\nload_dataset("flaky/net")\n', "t.py")
    assert judge_call(site, hub).reason == "not_recorded"  # "not recorded" is honest, "not reachable" was not


def test_live_hub_judges_with_lazy_revision_lookup():
    api = FakeApi()
    hub = LiveHub(api=api)
    src = 'from datasets import load_dataset\nload_dataset("conll2003", revision="abc123")\n'
    [site] = extract_calls(src, "t.py")
    v = judge_call(site, hub)
    assert v.code == "PR" and v.canonical_id == "eriktks/conll2003"
    # a second call reuses the cached snapshot instead of asking again
    before = len(api.calls)
    judge_call(site, hub)
    assert len(api.calls) == before
    missing = extract_calls('from datasets import load_dataset\nload_dataset("conll2003", revision="nope")\n', "t.py")[0]
    assert judge_call(missing, hub).reason == "revision_not_found"


def test_record_merges_aliases_and_trees(tmp_path):
    recorded(tmp_path, ["conll2003"])
    recorded(tmp_path, ["eriktks/conll2003"], ["abc123"])
    data = json.loads((tmp_path / "eriktks__conll2003.json").read_text())
    assert sorted(data["aliases"]) == ["conll2003", "eriktks/conll2003"]


def test_record_command_uses_hub(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("unscript_datasets.cli.LiveHub", lambda: LiveHub(api=FakeApi()))
    assert main(["record", "eriktks/conll2003", "--out", str(tmp_path / "o"), "--lang", "en"]) == 0
    assert "Recorded eriktks/conll2003" in capsys.readouterr().out
    assert (tmp_path / "o" / "eriktks__conll2003.json").exists()


# --- F-1: record must not save "no answer" as a Hub state ---------------------------


@pytest.mark.parametrize("lang, phrase", [("en", "could not record flaky/net"), ("ko", "녹화하지 못함")])
def test_record_command_fails_when_hub_unreachable(tmp_path, monkeypatch, capsys, lang, phrase):
    monkeypatch.setattr("unscript_datasets.cli.LiveHub", lambda: LiveHub(api=FakeApi()))
    out = tmp_path / "o"
    assert main(["record", "flaky/net", "--out", str(out), "--lang", lang]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # no "Recorded ..." line
    assert phrase in captured.err and "ConnectionError: boom" in captured.err
    assert "SSL_CERT_FILE=/etc/ssl/cert.pem" in captured.err and "HF_ENDPOINT" in captured.err
    assert not out.exists()  # not even an empty folder that --offline would accept


def test_record_command_partial_failure_keeps_good_snapshots(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("unscript_datasets.cli.LiveHub", lambda: LiveHub(api=FakeApi()))
    out = tmp_path / "o"
    assert main(["record", "eriktks/conll2003", "flaky/net", "--out", str(out), "--lang", "en"]) == 2
    captured = capsys.readouterr()
    assert "Recorded eriktks/conll2003" in captured.out
    assert sorted(p.name for p in out.iterdir()) == ["eriktks__conll2003.json"]


def test_failed_rerecord_leaves_existing_snapshot_untouched(tmp_path, monkeypatch, capsys):
    out = tmp_path / "o"
    recorded(out, ["eriktks/conll2003"])
    before = (out / "eriktks__conll2003.json").read_bytes()
    monkeypatch.setattr("unscript_datasets.cli.LiveHub", lambda: LiveHub(api=DownApi()))
    assert main(["record", "eriktks/conll2003", "--out", str(out), "--lang", "en"]) == 2
    err = capsys.readouterr().err
    assert "CERTIFICATE_VERIFY_FAILED" in err and "left unchanged" in err
    assert (out / "eriktks__conll2003.json").read_bytes() == before


def test_revision_network_failure_is_not_recorded(tmp_path):
    [outcome] = recorded(tmp_path, ["eriktks/conll2003"], ["netfail"], api=RevisionNetFailApi())
    assert outcome.status == "network" and outcome.path is None and "connection reset" in outcome.error
    assert not (tmp_path / "eriktks__conll2003.json").exists()


@pytest.mark.parametrize("ids, revisions, warning", [
    (["gaia-benchmark/GAIA"], [], "Set HF_TOKEN"),
    (["nobody/nothing"], [], "Check the id and HF_ENDPOINT"),
    (["eriktks/conll2003"], ["--revision", "nope"], "revision nope of eriktks/conll2003 was not found"),
])
def test_record_command_warns_with_exit_1(tmp_path, monkeypatch, capsys, ids, revisions, warning):
    monkeypatch.setattr("unscript_datasets.cli.LiveHub", lambda: LiveHub(api=FakeApi()))
    out = tmp_path / "o"
    assert main(["record", *ids, *revisions, "--out", str(out), "--lang", "en"]) == 1
    captured = capsys.readouterr()
    assert "Recorded" in captured.out and warning in captured.err
    assert len(list(out.iterdir())) == 1


@pytest.mark.network
def test_record_without_network_uses_real_client_and_writes_nothing(tmp_path, capsys):
    # 진짜 HfApi 로 실행한다. conftest 가 소켓을 막아 두었으므로 "Hub 에 닿지 못하는" 상황 그대로다.
    out = tmp_path / "o"
    assert main(["record", "eriktks/conll2003", "--out", str(out), "--lang", "en"]) == 2
    assert "SSL_CERT_FILE" in capsys.readouterr().err
    assert not out.exists()


def test_offline_replay_rejects_snapshot_recorded_without_hub(tmp_path, capsys):
    # 예전 버전이 남긴 {"status": "network", "trees": {}} 파일
    def unreachable(data):
        data.update(status="network", trees={}, pull_requests=[])

    hub_dir = make_hub_dir(tmp_path, eriktks__conll2003=unreachable)
    with pytest.raises(IncompleteSnapshotError):
        RecordedHub(hub_dir)
    code = main(["scan", str(EXAMPLES / "eval_ner.py"), "--offline", str(hub_dir), "--lang", "en"])
    err = capsys.readouterr().err
    assert code == 2
    assert "eriktks__conll2003.json (status: network)" in err and "record it again" in err


def test_live_scan_explains_unreachable_hub(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("unscript_datasets.cli.LiveHub", lambda: LiveHub(api=DownApi()))
    src = tmp_path / "a.py"
    src.write_text('from datasets import load_dataset\nload_dataset("eriktks/conll2003")\n', encoding="utf-8")
    assert main(["scan", str(src), "--lang", "en"]) == 0  # documented: UNKNOWN alone does not fail ...
    captured = capsys.readouterr()
    assert "Hub not reachable" in captured.out
    assert "did not answer for 1 dataset(s)" in captured.err and "CERTIFICATE_VERIFY_FAILED" in captured.err
    assert "SSL_CERT_FILE=/etc/ssl/cert.pem" in captured.err
    assert main(["scan", str(src), "--strict", "--lang", "ko"]) == 1  # ... but --strict does
    assert "응답하지 않아" in capsys.readouterr().err
