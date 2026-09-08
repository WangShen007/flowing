import re

import pytest

from app.core.scenario_templates import SCENARIO_TEMPLATES, missing_required_fields, render_template


@pytest.mark.parametrize("template", SCENARIO_TEMPLATES, ids=lambda t: t["id"])
def test_builtin_template_fields_match_placeholders(template: dict) -> None:
    keys = [field["key"] for field in template["fields"]]
    assert len(keys) == len(set(keys))
    assert set(re.findall(r"\{\{(\w+)\}\}", template["prompt"])) == set(keys)
    values = {key: f"test-{key}" for key in keys}
    assert not missing_required_fields(template, values)
    rendered = render_template(template["id"], values)
    assert "{{" not in rendered
    for key in keys:
        assert values[key] in rendered
        missing = missing_required_fields(template, {**values, key: "  "})
        assert [field["key"] for field in missing] == [key]
