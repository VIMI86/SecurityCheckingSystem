RULES = (
    ("6f0c1a10-7c2e-4a1b-9d11-0a1b2c3d4e01", "record_only", "ALLOW", "low"),
    ("6f0c1a10-7c2e-4a1b-9d11-0a1b2c3d4e02", "block_host", "REQUIRE_APPROVAL", "high"),
    ("6f0c1a10-7c2e-4a1b-9d11-0a1b2c3d4e03", "isolate_host", "REQUIRE_APPROVAL", "high"),
)

_AUDIT = {
    "ALLOW": "policy.allow",
    "DENY": "policy.deny",
    "REQUIRE_APPROVAL": "policy.require_approval",
}


def decide(action: str | None) -> tuple[str, str]:
    name = (action or "").strip()
    for _rule_id, rule_action, effect, _max_risk in RULES:
        if name == rule_action:
            return effect, _AUDIT[effect]
    return "DENY", "policy.deny"


def decide_stored(conn, action: str | None) -> tuple[str, str]:
    name = (action or "").strip()
    row = None
    if name:
        row = conn.execute(
            "SELECT effect FROM policies WHERE action = %s",
            (name,),
        ).fetchone()
    if row is None or row["effect"] not in _AUDIT:
        return "DENY", "policy.deny"
    return row["effect"], _AUDIT[row["effect"]]
