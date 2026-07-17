import json

from study_builder.models import ModuleDescriptor
from study_builder.policy import ModulePolicy


def test_policy_is_fail_closed(tmp_path, commentary_module) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "approved_license_values": ["Public Domain"],
                "approved_modules": {},
                "denied_modules": {"blocked": "contract restriction"},
            }
        ),
        encoding="utf-8",
    )
    policy = ModulePolicy(path)
    assert policy.decide(commentary_module).allowed
    unknown = ModuleDescriptor("Unknown", {"distributionlicense": ("Copyrighted",)}, "unknown.conf")
    assert not policy.decide(unknown).allowed
    blocked = ModuleDescriptor(
        "Blocked", {"distributionlicense": ("Public Domain",)}, "blocked.conf"
    )
    assert not policy.decide(blocked).allowed
