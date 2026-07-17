from study_builder.cli import parser


def test_build_cli_defaults_to_both_resources() -> None:
    args = parser().parse_args(["build", "--dry-run"])
    assert args.resource == "all"
    assert args.dry_run
    assert args.engine is None
    assert args.commentaries_repo.endswith("getbible/v1_commentaries.git")
    assert args.dictionaries_repo.endswith("getbible/v1_dictionaries.git")
