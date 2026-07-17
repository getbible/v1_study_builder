from study_builder.catalog import classify, parse_sword_conf, select_modules


def test_parse_repeated_and_continued_fields() -> None:
    module = parse_sword_conf(
        "mods.d/demo.conf",
        """[Demo]
Description=First line\\
  second line
Lang=NL
Feature=GreekDef
Feature=StrongsNumbers
ModDrv=RawLD4
Category=Lexicons / Dictionaries
""",
    )
    assert module.name == "Demo"
    assert module.description == "First line second line"
    assert module.language == "nl"
    assert module.fields["feature"] == ("GreekDef", "StrongsNumbers")
    assert classify(module) == "dictionaries"


def test_classify_and_select(commentary_module, greek_dictionary_module) -> None:
    assert classify(commentary_module) == "commentaries"
    selected = select_modules(
        [commentary_module, greek_dictionary_module], "commentaries", {"testcom"}
    )
    assert selected == [("commentaries", commentary_module)]


def test_select_rejects_missing_module(commentary_module) -> None:
    try:
        select_modules([commentary_module], "all", {"missing"})
    except ValueError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("missing module was not rejected")
