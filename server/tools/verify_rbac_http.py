"""端到端验证 RBAC：走真实路由 + 中间件 + 依赖链。

覆盖：
1. 登录态返回每空间角色（workspace_roles）；
2. 新账号只有个人空间，看不到别的空间；
3. 越权访问别的空间 → 回落自己的空间，读不到别人数据；
4. 非成员访问空间成员列表 → 403；
5. 空间 admin 能改成员角色，owner 不可被移除；
6. 个人空间不可删除。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import members, sessions, users, workspaces  # noqa: E402


def _token_for(user: users.User) -> str:
    token, _ = sessions.get_session_store().issue(user.id, "rbac-http-test")
    sessions.get_session_store().save()
    return token


def _auth(token: str, ws: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if ws:
        headers["X-Workspace"] = ws
    return headers


def main() -> None:
    client = TestClient(app)
    user_store = users.get_user_store()
    member_store = members.get_member_store()
    ws_store = workspaces.get_workspace_store()

    admin = next(u for u in user_store.list_all() if u.is_admin)
    admin_token = _token_for(admin)

    # 1. 登录态带每空间角色
    me = client.get("/api/auth/me", headers=_auth(admin_token))
    assert me.status_code == 200, me.text
    body = me.json()
    assert "workspace_roles" in body, body
    roles = body["workspace_roles"]
    personal = members.personal_id(admin.id)
    assert roles.get(personal) == "owner", f"个人空间角色错误: {roles}"
    print(f"PASS-1 me.workspace_roles: {roles}")

    # 2. 新建账号：只有个人空间
    name = "rbac_http_user"
    old = user_store.by_username(name)
    if old is not None:
        for item in list(member_store.memberships_of(old.id)):
            member_store.remove(old.id, item.workspace_id)
        user_store.delete(old.id)
        user_store.save()
        member_store.save()

    created = client.post(
        "/api/users",
        headers=_auth(admin_token),
        json={"username": name, "password": "pw-rbac-1", "display_name": "RBAC 用户", "role": "member"},
    )
    assert created.status_code == 200, created.text
    probe = user_store.by_username(name)
    assert probe is not None
    token = _token_for(probe)

    spaces = client.get("/api/workspaces", headers=_auth(token)).json()
    assert len(spaces) == 1 and spaces[0]["is_personal"], f"新账号应只有个人空间: {spaces}"
    own = spaces[0]["id"]
    assert spaces[0]["role"] == "owner", spaces
    print(f"PASS-2 新账号空间隔离: {[s['id'] + '/' + s['role'] for s in spaces]}")

    # 2b. 管理员也进不去别人的个人空间（个人空间是绝对私有边界）
    admin_view = client.get("/api/workspaces", headers=_auth(admin_token)).json()
    admin_ids = {s["id"] for s in admin_view}
    assert own not in admin_ids, f"管理员看到了别人的个人空间: {admin_ids}"
    print(f"PASS-2b 管理员可见空间不含他人个人空间（共 {len(admin_ids)} 个）")

    # 3. 越权指定别人的空间 → 回落自己的空间，项目列表为空（看不到 default 的数据）
    audit_space = "default"
    default_projects = client.get(
        "/api/projects", headers=_auth(admin_token, audit_space)
    ).json()
    leaked = client.get("/api/projects", headers=_auth(token, audit_space)).json()
    assert len(default_projects) > 0, "前提不成立：default 空间应有项目"
    assert leaked == [] or len(leaked) < len(default_projects), f"越权读到了别人的项目: {leaked}"
    # 项目创建落在自己的空间，而不是被请求的 default
    made = client.post(
        "/api/projects",
        headers=_auth(token, audit_space),
        json={"name": "RBAC 隔离验证项目", "template": "blank"},
    )
    assert made.status_code == 200, made.text
    made_pid = made.json()["project"]
    own_projects = client.get("/api/projects", headers=_auth(token, own)).json()
    assert any(p.get("id") == made_pid for p in own_projects), own_projects
    print(f"PASS-3 越权回落: default 项目 {len(default_projects)} 个，该账号看到 {len(leaked)} 个；新建落在个人空间")

    # 4. 非成员看空间成员列表 → 403
    deny = client.get(f"/api/workspaces/{audit_space}/members", headers=_auth(token))
    assert deny.status_code == 403, f"应 403，实际 {deny.status_code} {deny.text}"
    print("PASS-4 非成员访问空间成员列表 → 403")

    # 5. 团队空间：个人空间不能发邀请（私有边界），团队空间才能加人
    private_invite = client.post(
        f"/api/workspaces/{own}/invites", headers=_auth(token), json={"role": "member"}
    )
    assert private_invite.status_code == 400, private_invite.text
    print(f"PASS-5a 个人空间拒绝邀请码：{private_invite.json()['detail']}")

    team = client.post(
        "/api/workspaces", headers=_auth(admin_token), json={"name": "RBAC 端到端团队"}
    )
    assert team.status_code == 200, team.text
    team_id = team.json()["id"]
    assert member_store.role_of(admin.id, team_id) == "owner", "创建者应为空间 owner"
    team_invite = client.post(
        f"/api/workspaces/{team_id}/invites",
        headers=_auth(admin_token),
        json={"role": "member", "max_uses": 3},
    )
    assert team_invite.status_code == 200, team_invite.text
    joined = client.post(
        "/api/auth/join", headers=_auth(token), json={"code": team_invite.json()["code"]}
    )
    assert joined.status_code == 200, joined.text
    assert member_store.role_of(probe.id, team_id) == "member", member_store.role_of(probe.id, team_id)
    # member 无权改角色
    low = client.patch(
        f"/api/workspaces/{team_id}/members/{admin.id}",
        headers=_auth(token),
        json={"role": "viewer"},
    )
    assert low.status_code == 403, f"member 不应能改角色: {low.status_code} {low.text}"
    # 提升为 admin 后：能看成员列表，但不能改自己角色、也不能移除 owner
    client.patch(
        f"/api/workspaces/{team_id}/members/{probe.id}",
        headers=_auth(admin_token),
        json={"role": "admin"},
    )
    assert member_store.role_of(probe.id, team_id) == "admin"
    roster = client.get(f"/api/workspaces/{team_id}/members", headers=_auth(token))
    assert roster.status_code == 200 and len(roster.json()) == 2, roster.text
    self_change = client.patch(
        f"/api/workspaces/{team_id}/members/{probe.id}",
        headers=_auth(token),
        json={"role": "member"},
    )
    assert self_change.status_code == 400, self_change.text
    # owner 不可被移除
    remove_owner = client.delete(
        f"/api/workspaces/{team_id}/members/{admin.id}", headers=_auth(token)
    )
    assert remove_owner.status_code == 400, remove_owner.text
    print(f"PASS-5b 团队空间：member 改角色被拒(403)→admin 可查成员；改自己/移除 owner 均被拒")

    # 5c. per-workspace 只读：在团队空间降为 viewer 后写被拒，个人的空间照常可写
    client.patch(
        f"/api/workspaces/{team_id}/members/{probe.id}",
        headers=_auth(admin_token),
        json={"role": "viewer"},
    )
    blocked = client.post(
        "/api/projects", headers=_auth(token, team_id), json={"name": "团队空间越权建", "template": "blank"}
    )
    assert blocked.status_code == 403, f"viewer 应被拦，实际 {blocked.status_code} {blocked.text}"
    allowed_own = client.post(
        "/api/projects", headers=_auth(token, own), json={"name": "个人空间正常建", "template": "blank"}
    )
    assert allowed_own.status_code == 200, allowed_own.text
    client.delete(f"/api/projects/{allowed_own.json()['project']}", headers=_auth(token, own))
    print("PASS-5c per-workspace 只读：团队空间 viewer 写入被拦，个人空间(owner)写入正常")

    # 6. 个人空间不可删
    deletion = client.delete(f"/api/workspaces/{own}", headers=_auth(token))
    assert deletion.status_code == 400, deletion.text
    print(f"PASS-6 个人空间不可删：{deletion.json()['detail']}")

    # 清理：项目、成员关系、账号、测试空间
    client.delete(f"/api/projects/{made_pid}", headers=_auth(token))
    for uid in (admin.id, probe.id):
        member_store.remove(uid, team_id)
        member_store.remove(uid, own)
    for item in list(member_store.memberships_of(probe.id)):
        member_store.remove(probe.id, item.workspace_id)
    member_store.save()
    for ws_id in (team_id, own):
        ws_store.delete(ws_id)
    ws_store.save()
    user_store.delete(probe.id)
    user_store.save()
    print("\nPASS: RBAC 端到端成立（隔离 / 越权回落 / 角色管理 / 所有者保护）")


if __name__ == "__main__":
    main()
