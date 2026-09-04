from __future__ import annotations

from pathlib import Path

import pytest

from study_builder.models import ModuleDescriptor


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def commentary_module() -> ModuleDescriptor:
    return ModuleDescriptor(
        name="TestCom",
        conf_path="mods.d/testcom.conf",
        fields={
            "description": ("Test Commentary",),
            "lang": ("en",),
            "moddrv": ("zCom",),
            "category": ("Commentaries",),
            "distributionlicense": ("Public Domain",),
            "version": ("1.0",),
            "sourcetype": ("OSIS",),
        },
    )


@pytest.fixture
def greek_dictionary_module() -> ModuleDescriptor:
    return ModuleDescriptor(
        name="StrongsGreek",
        conf_path="mods.d/strongsgreek.conf",
        fields={
            "description": ("Strong's Greek Bible Dictionary",),
            "lang": ("en",),
            "moddrv": ("RawLD4",),
            "category": ("Lexicons / Dictionaries",),
            "distributionlicense": ("Public Domain",),
            "feature": ("GreekDef",),
            "version": ("1.0",),
        },
    )


@pytest.fixture(scope="session")
def bible_tree(tmp_path_factory) -> Path:
    from support.bible_fixture import write_bible_tree

    return write_bible_tree(tmp_path_factory.mktemp("bible-api") / "v2")


@pytest.fixture
def bible_api(bible_tree, tmp_path):
    from study_builder.bible import BibleApi

    return BibleApi(bible_tree, cache_dir=tmp_path / "bible-cache")


@pytest.fixture(scope="session")
def shared_bible_api(bible_tree, tmp_path_factory):
    from study_builder.bible import BibleApi

    return BibleApi(bible_tree, cache_dir=tmp_path_factory.mktemp("bible-cache"))


@pytest.fixture(scope="session")
def reference_engine(shared_bible_api):
    """The English, KJV-versified engine the writer tests share; it holds no per-test state."""
    from study_builder.books import BookRegistry
    from study_builder.references import ReferenceEngine

    root = Path(__file__).resolve().parents[1]
    return ReferenceEngine.for_module(
        shared_bible_api,
        BookRegistry(root / "conf/book_registry.json"),
        "en",
        "KJV",
        root / "conf/book_aliases",
    )
