import pytest

from helpers import EXAMPLES, HUB_DIR
from unscript_datasets.cli import main
from unscript_datasets.i18n import MESSAGES, resolve_lang, t
from unscript_datasets.judge import VERDICTS


def test_message_keys_are_identical():
    assert set(MESSAGES["en"]) == set(MESSAGES["ko"])
    for key in MESSAGES["en"]:
        # placeholders must match between languages
        import string
        names = lambda text: {f for _, f, _, _ in string.Formatter().parse(text) if f}  # noqa: E731
        assert names(MESSAGES["en"][key]) == names(MESSAGES["ko"][key]), key


def test_every_verdict_and_reason_is_explained():
    reasons = {"no_script", "pinned_old_revision", "open_conversion_pr", "convert_branch", "no_convert_branch",
               "config_not_in_convert", "split_not_found_in_convert", "partial_split_only", "multiple_configs",
               "unsupported_args", "split_unparseable", "rewrite_invalid", "gated", "not_found", "network",
               "not_recorded", "revision_not_found", "dynamic_arg", "packaged_builder", "local_path"}
    for lang in MESSAGES:
        for verdict in VERDICTS:
            assert f"verdict.{verdict}" in MESSAGES[lang]
        for reason in reasons:
            assert f"reason.{reason}" in MESSAGES[lang]


def test_precedence_cli_over_env_over_locale():
    env = {"UNSCRIPT_DATASETS_LANG": "ko", "LANG": "en_US.UTF-8"}
    assert resolve_lang("en", env) == "en"
    assert resolve_lang(None, env) == "ko"
    assert resolve_lang(None, {"LANG": "ko_KR.UTF-8"}) == "ko"
    assert resolve_lang(None, {"LC_ALL": "en_US.UTF-8", "LANG": "ko_KR.UTF-8"}) == "en"
    assert resolve_lang(None, {"LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US"}) == "ko"
    assert resolve_lang(None, {"LANG": "fr_FR.UTF-8"}) == "en"
    assert resolve_lang(None, {}) == "en"
    assert resolve_lang(None, {"UNSCRIPT_DATASETS_LANG": "en", "LANG": "ko_KR.UTF-8"}) == "en"


def test_help_switches_language(capsys):
    with pytest.raises(SystemExit):
        main(["--lang", "ko", "--help"])
    ko = capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--lang", "en", "--help"])
    en = capsys.readouterr().out
    assert "호출" in ko and "호출" not in en
    assert "Find load_dataset calls" in en


def test_table_headers_and_explanations_switch(capsys):
    main(["scan", str(EXAMPLES), "--offline", str(HUB_DIR), "--lang", "ko"])
    ko = capsys.readouterr().out
    main(["scan", str(EXAMPLES), "--offline", str(HUB_DIR), "--lang", "en"])
    en = capsys.readouterr().out
    assert "위치" in ko and "판정" in ko and "열린 Parquet 변환 PR #15" in ko and "LOCATION" not in ko
    assert "LOCATION" in en and "open Parquet conversion PR #15" in en and "위치" not in en
    assert " PR " in ko  # verdict codes stay English


def test_env_var_selects_language(monkeypatch, capsys):
    monkeypatch.setenv("UNSCRIPT_DATASETS_LANG", "ko")
    main(["owner-cmd", "a/b"])
    assert "데이터셋 주인" in capsys.readouterr().out
    main(["owner-cmd", "a/b", "--lang", "en"])
    assert "Ask the dataset owner" in capsys.readouterr().out


def test_t_formats_placeholders():
    assert t("en", "reason.config_not_in_convert", detail="x") == "config 'x' is not in the converted branch"


@pytest.mark.parametrize("argv", [["--help"], ["scan", "--help"], ["fix", "--help"], ["owner-cmd", "--help"],
                                   ["record", "--help"]])
def test_korean_help_has_no_argparse_english(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main([*argv, "--lang", "ko"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for english in ("positional arguments", "options:", "show this help message", "usage:"):
        assert english not in out
    assert "옵션:" in out and "사용법:" in out


def test_korean_version_help_and_errors(capsys):
    with pytest.raises(SystemExit):
        main(["--lang", "ko", "--help"])
    assert "버전을 보여 주고 종료" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["owner-cmd", "--lang", "ko"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "필요한 인자가 빠졌습니다" in err and "required" not in err
    with pytest.raises(SystemExit):
        main(["owner-cmd", "--lang", "en"])
    assert "the following arguments are required" in capsys.readouterr().err


def _tool_error_cases(tmp_path):
    """Every error the tool prints itself (not argparse): argv without --lang, and the message key."""
    bad_hub = tmp_path / "bad_hub"
    bad_hub.mkdir()
    (bad_hub / "broken.json").write_text("{not json", encoding="utf-8")
    text_file = tmp_path / "README.md"
    text_file.write_text("# hi\n", encoding="utf-8")
    return [
        (["scan", str(text_file)], "err.not_source"),
        (["scan", str(tmp_path / "missing.py")], "err.path_missing"),
        (["fix", str(EXAMPLES), "--offline", str(tmp_path / "no_hub")], "err.offline_missing"),
        (["scan", str(EXAMPLES), "--offline", str(bad_hub)], "err.snapshot"),
        (["owner-cmd", "not a valid id!"], "err.bad_id"),
    ]


@pytest.mark.parametrize("lang, prefix, foreign", [
    ("ko", "unscript-datasets: 오류: ", "error"),
    ("en", "unscript-datasets: error: ", "오류"),
])
def test_tool_errors_use_localized_prefix(lang, prefix, foreign, tmp_path, capsys):
    # 도구가 직접 내는 오류도 argparse 오류와 같은 "<prog>: 오류: " 형식이어야 한다(QA 2회차 Q-2)
    for argv, key in _tool_error_cases(tmp_path):
        assert main([*argv, "--lang", lang]) == 2, argv
        err = capsys.readouterr().err
        assert err.startswith(prefix), (argv, err)
        assert foreign not in err.split(":", 2)[1], (argv, err)
        # 본문 첫 몇 글자도 고른 언어의 메시지 표에서 온다
        assert MESSAGES[lang][key].split("{")[0][:6] in err, (argv, err)


def test_tool_errors_match_argparse_error_style(capsys):
    # 같은 언어 안에서 도구 오류와 argparse 오류의 접두가 같다
    with pytest.raises(SystemExit):
        main(["owner-cmd", "--lang", "ko"])
    argparse_err = capsys.readouterr().err.splitlines()[-1]
    main(["owner-cmd", "not a valid id!", "--lang", "ko"])
    tool_err = capsys.readouterr().err
    assert argparse_err.startswith("unscript-datasets owner-cmd: 오류: ")
    assert tool_err.startswith("unscript-datasets: 오류: ")
    assert "error" not in argparse_err and "error" not in tool_err


def test_bad_lang_error_follows_env_language(monkeypatch, capsys):
    # --lang 값 자체가 틀리면 환경변수·로케일 언어로 접두와 본문을 낸다
    monkeypatch.setenv("UNSCRIPT_DATASETS_LANG", "ko")
    assert main(["scan", "x.py", "--lang", "fr"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("unscript-datasets: 오류: 잘못된 --lang 'fr'")
    monkeypatch.setenv("UNSCRIPT_DATASETS_LANG", "en")
    assert main(["scan", "x.py", "--lang", "fr"]) == 2
    assert capsys.readouterr().err.startswith("unscript-datasets: error: invalid --lang 'fr'")
