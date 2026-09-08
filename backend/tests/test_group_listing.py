from app.skills.lark_cli.skill import LarkCLISkill


def test_group_listing_uses_verified_shortcut_and_user_identity():
    skill = LarkCLISkill.__new__(LarkCLISkill)
    query = "我有哪些群呢"
    plan = skill._build_my_groups_plan(query)
    assert plan and not plan["need_confirmation"]
    command = plan["commands"][0]["command"]
    assert command.startswith("lark-cli im +chat-list --as user")
    assert "--page-all" in command
    step = skill._build_heuristic_step(query, [])
    assert step["command"] == command
    assert skill._build_heuristic_step(query, [{"success": True, "command": command}])["done"]
    assert skill._build_my_groups_plan("我有哪些群，给所有群发送消息") is None
