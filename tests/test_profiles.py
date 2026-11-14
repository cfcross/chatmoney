from tests.conftest import profile, published


def test_tenants_versions_permissions_and_replay(env):
    a = published(env)
    b = published(env, "toc")
    assert env.req("GET", f"/agents/{a}", "toc-business").status_code == 404
    assert (
        env.req("GET", f"/agents/{b}", "toc-business").json()["data"]["config"][
            "persona"
        ]
        == "售后客服"
    )
    assert env.req("POST", "/agents", "tob-business", profile()).status_code == 403
    assert (
        env.req("POST", "/agents", body=profile() | {"tenant_id": "toc"}).status_code
        == 422
    )
    path = f"/agents/{a}"
    change = {"expected_revision": 1, "changes": {"config": {"tone": "直接"}}}
    r = env.req("PATCH", path, body=change, key="x")
    assert r.status_code == 200
    assert env.req("PATCH", path, body=change, key="x").json() == r.json()
    assert (
        env.req(
            "PATCH", path, body=change | {"expected_revision": 2}, key="x"
        ).status_code
        == 409
    )
    assert (
        env.req("POST", path + "/publish", body={"expected_revision": 2}).status_code
        == 201
    )
    assert (
        env.req("GET", path + "?version=1").json()["data"]["config"]["tone"] == "专业"
    )
    assert env.req("GET", path).json()["data"]["config"]["tone"] == "直接"


def test_configuration_requires_approved_references(env):
    spec = profile()
    spec["config"]["company_ref"] = "toc@1"
    a = env.req("POST", "/agents", body=spec).json()["data"]["agent_id"]
    assert (
        env.req(
            "POST", f"/agents/{a}/publish", body={"expected_revision": 1}
        ).status_code
        == 422
    )
    assert env.client.get("/api/v1/agents/" + a).status_code == 401
    assert (
        env.client.post(
            "/api/v1/agents",
            json=profile(),
            headers={"Authorization": "Bearer " + env.tokens["tob-admin"]},
        ).status_code
        == 422
    )
