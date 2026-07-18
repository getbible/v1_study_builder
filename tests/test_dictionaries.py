import json

from study_builder.dictionaries import DictionaryWriter, canonical_strong, encoded_entry_id
from study_builder.models import NativeExport


def test_strong_keys_match_bible_api_v3() -> None:
    assert canonical_strong("3056", "G") == "G3056"
    assert canonical_strong("00430", "H") == "H0430"
    assert canonical_strong("H07225", None) == "H07225"
    assert canonical_strong("00011", "G") == "G11"
    assert canonical_strong("agape", "G") is None


def test_generic_entry_ids_are_url_safe_and_reversible_for_normal_keys() -> None:
    assert encoded_entry_id("Aaron") == "k-Aaron"
    entry_id = encoded_entry_id("ἀγάπη / love")
    assert entry_id.startswith("k-")
    assert "/" not in entry_id


def test_dictionary_emits_direct_strong_lookup(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    export = NativeExport(
        metadata={"feature": "GreekDef"},
        entries=[
            {
                "record_type": "entry",
                "key": "03056",
                "raw": "See John.1.1",
                "plain": "logos: a word",
                "html": "<p><em>logos</em>: a word</p>",
            }
        ],
    )
    summary = DictionaryWriter(
        tmp_path, project_root / "schemas/dictionary-entry.schema.json"
    ).write(greek_dictionary_module, export)
    path = tmp_path / "strongsgreek/G3056.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert summary["strong_prefix"] == "G"
    assert document["id"] == "G3056"
    assert document["occurrence"] == 1
    assert document["aliases"] == ["03056", "G3056"]
    assert document["references"] == ["John.1.1"]


def test_dictionary_preserves_repeated_keys_as_distinct_definitions(
    tmp_path, project_root, greek_dictionary_module
) -> None:
    export = NativeExport(
        metadata={"feature": "GreekDef"},
        entries=[
            {"key": "03056", "raw": "First", "plain": "First", "html": ""},
            {"key": "03056", "raw": "Second", "plain": "Second", "html": ""},
        ],
    )
    summary = DictionaryWriter(
        tmp_path, project_root / "schemas/dictionary-entry.schema.json"
    ).write(greek_dictionary_module, export)

    first = json.loads((tmp_path / "strongsgreek/G3056.json").read_text(encoding="utf-8"))
    second = json.loads((tmp_path / "strongsgreek/G3056--2.json").read_text(encoding="utf-8"))
    keys = json.loads((tmp_path / "strongsgreek/keys.json").read_text(encoding="utf-8"))

    assert first["text"] == "First"
    assert first["occurrence"] == 1
    assert second["text"] == "Second"
    assert second["occurrence"] == 2
    assert [entry["url"] for entry in keys] == ["G3056.json", "G3056--2.json"]
    assert summary["entry_count"] == 2
    assert summary["unique_key_count"] == 1
