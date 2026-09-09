from study_builder.cli import parser


def test_build_cli_defaults_to_both_resources() -> None:
    args = parser().parse_args(["build", "--dry-run"])
    assert args.resource == "all"
    assert args.dry_run
    assert args.engine is None
    assert args.commentaries_repo.endswith("getbible/commentaries.git")
    assert args.dictionaries_repo.endswith("getbible/dictionaries.git")
    assert args.commentaries_branch == "main"
    assert args.dictionaries_branch == "main"


def test_partial_build_returns_zero_and_warns(tmp_path, monkeypatch, caplog, capsys):
    import json
    import logging
    from unittest.mock import Mock

    from study_builder import cli
    from study_builder.models import BuildReport

    report = BuildReport("2026-09-09T00:00:00Z", "all", "https://example.invalid/catalog")
    report.status = "partial"
    report.failed = [
        {
            "resource": "commentaries",
            "module": "Example",
            "stage": "export",
            "error_type": "RuntimeError",
            "reason": "Invalid stream",
        }
    ]
    pipeline = Mock()
    pipeline.run.return_value = report
    monkeypatch.setattr(cli, "BuildPipeline", Mock(return_value=pipeline))
    with caplog.at_level(logging.WARNING):
        result = cli.main(["build", "--work-dir", str(tmp_path)])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "partial"
    assert "Partial build completed" in caplog.text
    assert str(tmp_path / "reports/latest.json") in caplog.text
