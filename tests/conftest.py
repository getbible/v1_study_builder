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
