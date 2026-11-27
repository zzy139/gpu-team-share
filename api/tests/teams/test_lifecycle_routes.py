"""Admin lifecycle routes against the simulated head node and AWS fakes."""
import pytest

from api.teams import cluster

from . import slurm_outputs as out
from .conftest import seed_record, walk_keys
from .fakes import HEAD_NODE, FakeCluster, fake_clients

Q = "cluster_name=hpc-poc&region=us-west-2"
CREATE = {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge": 2, "g6e.2xlarge": 1}, "budget_usd": 500,
          "members": [{"email": "Carol@example.com"}], "lead": "lead@example.com"}


@pytest.fixture
def env(mocker, configured, parameter_store):
    cluster._head_node_cache.clear()
    mocker.patch("api.teams.cluster.head_node", return_value=HEAD_NODE)
    mocker.patch("api.teams.cluster.get_cluster_config_text", return_value=out.CLUSTER_CONFIG)
    head = FakeCluster()
    mocker.patch("api.teams.slurm.ssm_command", side_effect=head)
    c = fake_clients()
    mocker.patch("api.teams.lifecycle.clients", return_value=c)
    mocker.patch("api.teams.lifecycle.user_pool_id", return_value="us-west-2_pool")
    audit_logger = mocker.patch("api.teams.audit.logger", new=mocker.Mock())
    return {"head": head, "c": c, "store": parameter_store, "audit": audit_logger}


@pytest.fixture
def admin(mocker, client, mock_csrf_needed):
    mocker.patch("api.PclusterApiHandler.jwt_decode",
                 return_value={"cognito:groups": ["admin"], "username": "admin-1", "email": "admin@example.com"})
    client.set_cookie("accessToken", "access-token")
    return client


def _audit_actions(env):
    return [call.kwargs["extra"]["action"] for call in env["audit"].info.call_args_list]


def test_create_and_advance_to_active(admin, env, dynamo):
    response = admin.post(f"/team-quota/lifecycle/teams?{Q}", json=CREATE)
    assert response.status_code == 202
    assert response.json["state"] == "provisioning"
    assert [s["id"] for s in response.json["steps"]] == ["ad", "storage", "ecr", "cognito", "slurm"]
    assert [s["status"] for s in response.json["steps"]] == ["pending"] * 5
    record = env["store"].record("/pcui-teams/teams/teamc")
    assert record["spec"]["members"] == [{"email": "carol@example.com", "user": "carol", "link_existing": False}]
    assert record["spec"]["type_limits"] == {"g6e.xlarge": 2, "g6e.2xlarge": 1}
    assert dynamo.item("TEAM#teamc", "BUDGET")["budget_usd"] == 500
    assert env["head"].accounts.get("teamc") is None  # create only writes the record

    listed = admin.get(f"/team-quota/lifecycle/teams?{Q}").json
    assert listed["configured"] is True and listed["email_domains"] == ["example.com"]
    assert [r["team"] for r in listed["records"]] == ["teamc"]
    assert "spec" not in listed["records"][0]

    for _ in range(12):
        result = admin.post(f"/team-quota/lifecycle/teams/teamc/advance?{Q}", json={})
        assert result.status_code == 200
        if result.json["complete"]:
            break
    assert result.json["state"] == "active"
    assert env["head"].qos["teamc-g6e-xlarge"] == "node=2" and env["head"].qos["teamc-std"] == "node=0"
    status = admin.get(f"/team-quota/lifecycle/teams/teamc?{Q}").json
    assert all(s["status"] == "done" for s in status["steps"])
    assert "lifecycle_create" in _audit_actions(env) and "lifecycle_complete" in _audit_actions(env)
    # no password in any audit entry
    for call in env["audit"].info.call_args_list:
        for password in list(env["c"].ds.passwords.values()) + list(env["c"].cognito.temp_passwords.values()):
            assert password not in repr(call)


@pytest.mark.parametrize("body,status", [
    ({**CREATE, "name": "teama"}, 409),                                   # Slurm account exists
    ({**CREATE, "members": [{"email": "carol@other.com"}]}, 400),         # domain not allowed
    ({**CREATE, "name": "team_c"}, 400),
    ({**CREATE, "type_limits": {"p5.48xlarge": 1}}, 400),                 # not a queue of the cluster
    ({**CREATE, "type_limits": {"g6.xlarge": 1}}, 400),                   # only in the multi-type queue
    ({**CREATE, "billing_limit": 4}, 400),
])
def test_create_rejections(admin, env, body, status):
    assert admin.post(f"/team-quota/lifecycle/teams?{Q}", json=body).status_code == status
    assert env["store"].puts == []


def test_create_existing_record_is_409(admin, env):
    seed_record(env["store"], "teamc", state="provisioning")
    assert admin.post(f"/team-quota/lifecycle/teams?{Q}", json=CREATE).status_code == 409


def test_unmanaged_cluster_is_400(admin, env):
    response = admin.post("/team-quota/lifecycle/teams?cluster_name=other&region=us-west-2", json=CREATE)
    assert response.status_code == 400


def test_advance_without_record_is_404(admin, env):
    assert admin.post(f"/team-quota/lifecycle/teams/teamz/advance?{Q}", json={}).status_code == 404


def test_delete_team_reports_retained_resources(admin, env):
    seed_record(env["store"], "teama", ad_group="teamA", team_path="/efs/teamA",
                ad_users_created=["carol"], ad_secrets=["hpc-poc/user-carol-password"],
                cognito_users_created=["carol@example.com"], pcui_secrets=["hpc-poc/user-carol-pcui-temp-password"])
    env["c"].cognito.users = {"lead@example.com": "uuid-lead", "carol@example.com": "uuid-carol"}
    env["c"].cognito.groups["team-teama"] = {"lead@example.com"}
    env["c"].cognito.groups["member-teama"] = {"carol@example.com"}
    response = admin.delete(f"/team-quota/teams/teama?{Q}")
    assert response.status_code == 200
    body = response.json
    assert body["removed"] == ["slurm-user:alice", "slurm-account", "slurm-qos", "slurm-qos:teama-g6e-xlarge",
                               "cognito-group:team-teama", "cognito-group:member-teama"]
    assert body["retained"] == {
        "ad_group": "teamA", "ad_users": ["alice", "carol"], "team_path": "/efs/teamA",
        "ecr_repo": "hpc-poc-teama-envs",
        "secrets": ["hpc-poc/user-carol-password", "hpc-poc/user-carol-pcui-temp-password"],
        "cognito_users": ["carol@example.com", "lead@example.com"],
    }
    assert "team-teama" not in env["c"].cognito.groups and "member-teama" not in env["c"].cognito.groups
    assert env["c"].cognito.users  # users are kept
    assert "teama" not in env["head"].accounts and "teama-std" not in env["head"].qos
    assert "teama-g6e-xlarge" not in env["head"].qos and env["head"].gen_type_quota_runs == 1
    assert "alice" in env["head"].ad_users and "/efs/teamA" in env["head"].dirs  # AD and EFS untouched
    record = env["store"].record("/pcui-teams/teams/teama")
    assert record["state"] == "deleted" and record["deleted_at"]


def test_add_member_by_email_creates_ad_and_pcui_users(admin, env):
    seed_record(env["store"], "teamb", ad_group="teamB", team_path="/efs/teamB")
    env["c"].cognito.groups["member-teamb"] = set()
    response = admin.post(f"/team-quota/teams/teamb/members?{Q}", json={"email": "carol@example.com"})
    assert response.status_code == 201, response.json
    assert response.json["user"] == "carol" and response.json["email"] == "carol@example.com"
    assert [s["id"] for s in response.json["steps"]] == ["ad", "cognito", "slurm"]
    assert env["head"].ad_users["carol"] == "carol@example.com"
    assert "carol" in env["head"].ad_members["teamB"]
    assert "hpc-poc/user-carol-password" in env["c"].secrets.values
    assert "carol" in env["c"].ds.passwords
    assert env["c"].cognito.groups["member-teamb"] == {"carol@example.com"}
    assert "carol@example.com" in env["c"].cognito.temp_passwords
    assert ("teamb", "carol") in env["head"].users
    assert env["head"].user_qos[("teamb", "carol")] == "teamb-std,borrow,envbuild,teamb-g6e-12xlarge"
    for password in [env["c"].ds.passwords["carol"], env["c"].cognito.temp_passwords["carol@example.com"]]:
        assert password not in response.get_data(as_text=True)
    # idempotent: adding again reuses everything
    again = admin.post(f"/team-quota/teams/teamb/members?{Q}", json={"email": "carol@example.com"})
    assert again.status_code == 201
    assert len(env["c"].ds.calls) == 1


def test_add_member_collision_is_409(admin, env):
    seed_record(env["store"], "teamb", ad_group="teamB", team_path="/efs/teamB")
    env["head"].ad_users["carol"] = "carol@elsewhere.example.com"
    response = admin.post(f"/team-quota/teams/teamb/members?{Q}", json={"email": "carol@example.com"})
    assert response.status_code == 409
    assert "已属于其他邮箱" in response.json["message"]
    assert ("teamb", "carol") not in env["head"].users
    assert env["c"].ds.passwords == {}


def test_add_legacy_user_to_lifecycle_team_is_400(admin, env):
    seed_record(env["store"], "teamb", ad_group="teamB", team_path="/efs/teamB")
    assert admin.post(f"/team-quota/teams/teamb/members?{Q}", json={"user": "carol"}).status_code == 400


def test_link_existing_legacy_user(admin, env):
    seed_record(env["store"], "teama", ad_group="teamA", team_path="/efs/teamA")
    env["c"].cognito.groups["member-teama"] = set()
    env["head"].users.discard(("teama", "alice"))
    denied = admin.post(f"/team-quota/teams/teama/members?{Q}", json={"email": "alice@example.com"})
    assert denied.status_code == 409
    linked = admin.post(f"/team-quota/teams/teama/members?{Q}",
                        json={"email": "alice@example.com", "link_existing": True})
    assert linked.status_code == 201
    assert env["c"].ds.passwords == {}  # an existing AD user never gets a new password


def test_remove_member_removes_group_memberships_and_keeps_users(admin, env):
    seed_record(env["store"], "teamb", ad_group="teamB", team_path="/efs/teamB")
    env["c"].cognito.users = {"bob@example.com": "uuid-bob"}
    env["c"].cognito.groups["member-teamb"] = {"bob@example.com"}
    response = admin.delete(f"/team-quota/teams/teamb/members/bob?{Q}")
    assert response.status_code == 200
    assert [s["id"] for s in response.json["steps"]] == ["slurm", "ad", "cognito"]
    assert "bob" not in env["head"].ad_members["teamB"]
    assert env["c"].cognito.groups["member-teamb"] == set()
    assert "bob" in env["head"].ad_users and "bob@example.com" in env["c"].cognito.users


# --- offboarding ------------------------------------------------------------------------------------------------


@pytest.fixture
def team_b(env):
    seed_record(env["store"], "teamb", ad_group="teamB", team_path="/efs/teamB")
    env["c"].cognito.users = {"bob@example.com": "uuid-bob", "lead@example.com": "uuid-lead"}
    env["c"].cognito.groups["member-teamb"] = {"bob@example.com"}
    env["c"].cognito.groups["team-teamb"] = {"lead@example.com"}
    return env


def test_offboard_cancel_mode_runs_the_full_sequence(admin, team_b, dynamo):
    env = team_b
    env["head"].jobs[("teamb", "bob")] = 2
    response = admin.post(f"/team-quota/teams/teamb/members/bob/offboard?{Q}", json={"jobs": "cancel"})
    assert response.status_code == 200, response.json
    body = response.json
    assert [s["id"] for s in body["steps"]] == ["cognito", "ad", "slurm", "envs"]
    assert body["state"] == "offboarded"
    c, head = env["c"], env["head"]
    names = [name for name, _ in c.cognito.calls]
    assert names.index("admin_disable_user") < names.index("admin_user_global_sign_out")
    assert "uuid-bob" in c.cognito.disabled and c.cognito.signed_out == ["uuid-bob"]
    assert "bob" in head.ad_disabled
    assert head.cancelled == [("teamb", "bob")]
    assert ("teamb", "bob") not in head.users
    assert head.env_owner_runs == [("/efs/envs/teamb", "bob", "lead")]
    # command order on the head node: AD disable, then scancel && delete user, then the env hand-over
    writes = [cmd for cmd in head.commands if "disable:bob" in cmd or "scancel" in cmd or "pcui-env-owner" in cmd]
    assert "disable:bob" in writes[0] and "scancel -u bob -A teamb && " in writes[1] and "pcui-env-owner" in writes[2]
    assert dynamo.item("USER#bob", "OFFBOARD#teamb")["state"] == "offboarded"
    assert dynamo.item("SYS", "OFFBOARD#teamb#bob")["mode"] == "cancel"
    assert "offboard_member" in _audit_actions(env)
    assert "bob@example.com" in env["c"].cognito.users  # data and users are kept


def test_offboard_finish_mode_blocks_new_jobs_only(admin, team_b, dynamo):
    env = team_b
    response = admin.post(f"/team-quota/teams/teamb/members/bob/offboard?{Q}", json={"jobs": "finish"})
    assert response.status_code == 200
    assert response.json["state"] == "finishing"
    head = env["head"]
    assert head.cancelled == [] and ("teamb", "bob") in head.users
    assert head.max_submit[("teamb", "bob")] == 0
    assert dynamo.item("SYS", "OFFBOARD#teamb#bob")["state"] == "finishing"
    teams = admin.get(f"/team-quota/teams?{Q}").json["teams"]
    assert next(t for t in teams if t["name"] == "teamb")["offboarded"] == {"bob": "finishing"}


def test_reenable_restores_logins_and_association(admin, team_b, dynamo):
    env = team_b
    admin.post(f"/team-quota/teams/teamb/members/bob/offboard?{Q}", json={"jobs": "cancel"})
    response = admin.post(f"/team-quota/teams/teamb/members/bob/reenable?{Q}", json={})
    assert response.status_code == 200, response.json
    c, head = env["c"], env["head"]
    assert "uuid-bob" not in c.cognito.disabled and "bob" not in head.ad_disabled
    assert ("teamb", "bob") in head.users
    assert head.user_qos[("teamb", "bob")] == "teamb-std,borrow,envbuild,teamb-g6e-12xlarge"
    assert dynamo.item("USER#bob", "OFFBOARD#teamb") is None and dynamo.item("SYS", "OFFBOARD#teamb#bob") is None
    assert "reenable_member" in _audit_actions(env)


def test_reenable_finish_mode_clears_the_hold(admin, team_b):
    admin.post(f"/team-quota/teams/teamb/members/bob/offboard?{Q}", json={"jobs": "finish"})
    assert admin.post(f"/team-quota/teams/teamb/members/bob/reenable?{Q}", json={}).status_code == 200
    assert team_b["head"].max_submit[("teamb", "bob")] == -1


@pytest.mark.parametrize("url,body,status", [
    ("/team-quota/teams/teamb/members/carol/offboard", {"jobs": "cancel"}, 404),   # not a PCUI member
    ("/team-quota/teams/nosuch/members/bob/offboard", {"jobs": "cancel"}, 404),
    ("/team-quota/teams/teamb/members/bob/offboard", {"jobs": "later"}, 400),
    ("/team-quota/teams/teamb/members/bob/offboard", {}, 400),
    ("/team-quota/teams/teamb/members/root/offboard", {"jobs": "cancel"}, 400),
    ("/team-quota/teams/teamb/members/bob;id/offboard", {"jobs": "cancel"}, 400),
])
def test_offboard_rejections(admin, team_b, url, body, status):
    assert admin.post(f"{url}?{Q}", json=body).status_code == status
    assert team_b["c"].cognito.disabled == set()


def test_update_team_type_limits_and_budget_cap(admin, env, dynamo):
    seed_record(env["store"], "teama", ad_group="teamA", team_path="/efs/teamA")
    response = admin.patch(f"/team-quota/teams/teama?{Q}", json={
        "type_limits": {"g6e.xlarge": 0, "g6e.2xlarge": 3}, "budget_usd": 250.5, "env_storage_cap_gb": 100})
    assert response.status_code == 200, response.json
    head = env["head"]
    assert "teama-g6e-xlarge" not in head.qos and head.qos["teama-g6e-2xlarge"] == "node=3"
    assert head.qos_lists["teama"] == "teama-std,borrow,envbuild,teama-g6e-2xlarge"
    assert dynamo.item("TEAM#teama", "BUDGET")["budget_usd"] == 250.5
    assert env["store"].record("/pcui-teams/teams/teama")["env_storage_cap_gb"] == 100
    row = next(t for t in admin.get(f"/team-quota/teams?{Q}").json["teams"] if t["name"] == "teama")
    assert row["budget_usd"] == 250.5 and row["env_storage_cap_gb"] == 100
    assert row["type_limits"] == {"g6e.xlarge": 0, "g6e.2xlarge": 3, "g6e.12xlarge": 0}
    cleared = admin.patch(f"/team-quota/teams/teama?{Q}", json={"budget_usd": None})
    assert cleared.status_code == 200 and dynamo.item("TEAM#teama", "BUDGET") is None


def test_lifecycle_responses_have_no_billing(admin, env):
    admin.post(f"/team-quota/lifecycle/teams?{Q}", json=CREATE)
    bodies = [admin.get(f"/team-quota/lifecycle/teams?{Q}").json,
              admin.get(f"/team-quota/lifecycle/teams/teamc?{Q}").json,
              admin.post(f"/team-quota/lifecycle/teams/teamc/advance?{Q}", json={}).json,
              admin.get(f"/team-quota/teams?{Q}").json]
    for body in bodies:
        assert not [k for k in walk_keys(body) if "billing" in k.lower()]
