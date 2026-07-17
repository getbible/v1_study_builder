import zipfile

import pytest

from study_builder.security import UnsafeArchiveError, extract_zip


def test_extract_zip_rejects_traversal(tmp_path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../outside.txt", "bad")
    with pytest.raises(UnsafeArchiveError):
        extract_zip(archive, tmp_path / "result")
    assert not (tmp_path / "outside.txt").exists()


def test_extract_zip_accepts_module_tree(tmp_path) -> None:
    archive = tmp_path / "good.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("mods.d/demo.conf", "[Demo]\n")
        handle.writestr("modules/lexdict/rawld/demo/demo.dat", "content")
    result = tmp_path / "result"
    extract_zip(archive, result)
    assert (result / "mods.d/demo.conf").read_text() == "[Demo]\n"
