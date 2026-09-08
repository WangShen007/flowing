import asyncio

import pytest
from fastapi import HTTPException

from app.api.routes import scenarios
from app.api.routes.auth import AccountInfo
from app.core.feishu_permissions import (
    CAPABILITIES,
    SCENARIO_CAPABILITIES,
    SCENARIO_UNREVIEWED,
    scenario_access,
)
from app.core.scenario_templates import SCENARIO_TEMPLATES


def test_every_builtin_scenario_has_an_explicit_enterprise_policy() -> None:
    template_ids = {str(template["id"]) for template in SCENARIO_TEMPLATES}
    ready_ids = set(SCENARIO_CAPABILITIES)

    assert not ready_ids & SCENARIO_UNREVIEWED
    assert template_ids == ready_ids | SCENARIO_UNREVIEWED
    assert all(capability in CAPABILITIES for values in SCENARIO_CAPABILITIES.values() for capability in values)


def test_scenario_access_is_role_aware_and_fails_closed() -> None:
    assert scenario_access("create_doc", "employee").ready
    employee_meeting = scenario_access("recommend_group_meeting_times", "employee")
    assert not employee_meeting.ready
    assert "创建项目群组" in employee_meeting.reason
    assert scenario_access("recommend_group_meeting_times", "lead").ready
    assert not scenario_access("weekly_minutes_decision_dashboard", "admin").ready
    assert not scenario_access("new-template-without-policy", "admin").ready


def test_enterprise_scenario_api_labels_and_blocks_unreviewed_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scenarios.feishu_tokens, "enterprise_configured", lambda: True)
    monkeypatch.setattr(scenarios, "list_accessible_templates", lambda account: [])
    monkeypatch.setattr(scenarios, "get_template_for_render", lambda template_id, account: None)
    employee = AccountInfo(account="employee", name="Employee", role="employee")

    payload = asyncio.run(scenarios.list_scenarios(employee))["data"]
    by_id = {template["id"]: template for template in payload}
    assert by_id["create_doc"]["enterprise_ready"] is True
    assert by_id["weekly_minutes_decision_dashboard"]["enterprise_ready"] is False
    assert by_id["recommend_group_meeting_times"]["enterprise_ready"] is False

    request = scenarios.ScenarioRenderRequest(
        template_id="weekly_minutes_decision_dashboard",
        values={},
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(scenarios.render_scenario(request, employee))
    assert error.value.status_code == 403
    assert "尚未纳入企业安全清单" in str(error.value.detail)

    ready = asyncio.run(scenarios.render_scenario(
        scenarios.ScenarioRenderRequest(
            template_id="create_doc",
            values={"title": "测试", "content": "正文"},
            enable_ai_content_generation=False,
        ),
        employee,
    ))
    assert ready["data"]["executable"] is True
