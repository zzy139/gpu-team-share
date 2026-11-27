import json
import logging

import pytest

from api.teams import cluster, lifecycle, lifecycle_store

from . import slurm_outputs as out
from .conftest import CONFIG, seed_record
from .fakes import ENV_DIR, HEAD_NODE, TEAM_DIR, FakeCluster, client_error, fake_clients

TEAM = "teamc"
RECORD = "/pcui-teams/teams/teamc"


@pytest.fixture
def env(mocker, configured, parameter_store):
    cluster._head_node_cache.clear()
    mocker.patch("api.teams.cluster.head_node", return_value=HEAD_NODE)
    mocker.patch("api.teams.cluster.get_cluster_config_text", return_value=out.CLUSTER_CONFIG)
    head = FakeCluster()
    mocker.patch("api.teams.slurm.ssm_command", side_effect=head)
    mocker.patch("api.teams.lifecycle.user_pool_id", return_value="us-west-2_pool")
    audit = mocker.patch("api.teams.lifecycle.audit")
    mocker.patch("api.teams.lifecycle.logger", logging.getLogger("test-lifecycle"))
    c = fake_clients()
    return {"head": head, "c": c, "store": parameter_store, "audit": audit}


def _new(env, members=(("carol@example.com", False),), lead="lead@example.com", **spec):
    spec = {"fairshare": 10, "type_limits": {"g6e.xlarge": 2, "g6e.2xlarge": 1, "g6e.12xlarge": 0},
            "members": [{"email": e, "user": e.split("@")[0], "link_existing": link} for e, link in members],
            "lead": lead, **spec}
    record = lifecycle_store.new_record(CONFIG, TEAM, spec)
    lifecycle_store.put_record(CONFIG, record)
    return record


def _advance(env, team=TEAM):
    record = lifecycle_store.get_record(CONFIG, team)
    return lifecycle.advance(CONFIG, record, "hpc-poc", "us-west-2", env["c"])


def _statuses(result):
    return {s["id"]: s["status"] for s in result["steps"]}


def _run_to_completion(env, max_calls=12):
    results = []
    for _ in range(max_calls):
        result = _advance(env)
        results.append(result)
        if result["complete"]:
            return results
    raise AssertionError(f"not complete: {results[-1]}")


def test_happy_path_creates_everything_in_order(env):
    _new(env)
    first = _advance(env)
    assert [s["id"] for s in first["steps"]] == lifecycle.STEPS == ["ad", "storage", "ecr", "cognito", "slurm"]
    assert _statuses(first) == {"ad": "done", "storage": "pending", "ecr": "pending", "cognito": "pending",
                                "slurm": "pending"}
    head, c = env["head"], env["c"]
    assert head.ad_users["carol"] == "carol@example.com"
    assert "carol" in head.ad_members["teamc"]
    assert set(c.ds.passwords) == {"carol"}
    assert c.secrets.values["hpc-poc/user-carol-password"] == c.ds.passwords["carol"]
    assert {"Key": "Team", "Value": "teamc"} in c.secrets.tags["hpc-poc/user-carol-password"]
    assert {"Key": "Project", "Value": "hpc-poc"} in c.secrets.tags["hpc-poc/user-carol-password"]

    results = [first] + _run_to_completion(env)
    final = results[-1]
    assert final["complete"] is True and final["state"] == "active"
    assert head.dirs["/efs/teamc"] == (TEAM_DIR, "teamc")
    assert head.dirs["/efs/teamc/envs"] == (ENV_DIR, "teamc")
    repo = c.ecr.repos["hpc-poc-teamc-envs"]
    assert repo["scan"] == {"scanOnPush": True} and repo["encryption"] == {"encryptionType": "AES256"}
    assert {t["Key"]: t["Value"] for t in repo["tags"]} == {"Project": "hpc-poc", "CostCenter": "hpc-poc",
                                                             "Team": "teamc"}
    assert json.loads(repo["lifecycle"])["rules"][0]["selection"]["tagStatus"] == "untagged"
    assert c.cognito.groups["team-teamc"] == {"lead@example.com"}
    assert c.cognito.groups["member-teamc"] == {"carol@example.com"}
    assert set(c.cognito.temp_passwords) == {"lead@example.com", "carol@example.com"}
    assert c.secrets.values["hpc-poc/user-carol-pcui-temp-password"] == c.cognito.temp_passwords["carol@example.com"]
    # Slurm: account, fail-closed std QOS, one QOS per allowed type (0 = none), QOS lists, allow file
    assert head.accounts["teamc"] == 10
    assert head.qos["teamc-std"] == "node=0"
    assert head.qos["teamc-g6e-xlarge"] == "node=2" and head.qos["teamc-g6e-2xlarge"] == "node=1"
    assert "teamc-g6e-12xlarge" not in head.qos
    assert head.qos_lists["teamc"] == "teamc-std,borrow,envbuild,teamc-g6e-2xlarge,teamc-g6e-xlarge"
    assert head.user_qos[("teamc", "carol")] == "teamc-std,borrow,envbuild,teamc-g6e-2xlarge,teamc-g6e-xlarge"
    assert ("teamc", "carol") in head.users
    assert head.gen_type_quota_runs == 1
    assert not any("billing" in command for command in head.commands)
    record = env["store"].record(RECORD)
    assert record["state"] == "active" and record["spec"] is None
    assert record["ecr_ready"] is True
    assert record["ad_users_created"] == ["carol"] and record["password_set"] == ["carol"]
    assert sorted(record["cognito_users_created"]) == ["carol@example.com", "lead@example.com"]
    actions = [call.args[0] for call in env["audit"].call_args_list]
    assert actions[:3] == ["lifecycle_ad", "lifecycle_storage", "lifecycle_ecr"]
    assert actions[-1] == "lifecycle_complete"


def test_retry_after_ecr_failure_skips_completed_steps(env):
    _new(env)
    env["c"].ecr.fail["create_repository"] = client_error("AccessDeniedException", "denied")
    _advance(env)
    _advance(env)
    failed = _advance(env)
    assert _statuses(failed)["ecr"] == "failed"
    assert "AccessDeniedException" in next(s for s in failed["steps"] if s["id"] == "ecr")["message"]
    commands_before = len(env["head"].commands)
    ad_writes_before = [c for c in env["head"].commands if "create-user:" in c]
    del env["c"].ecr.fail["create_repository"]
    retried = _advance(env)
    assert _statuses(retried)["ecr"] == "done"
    assert _statuses(retried)["ad"] == "done" and _statuses(retried)["storage"] == "done"
    # the retry only probed (one head-node call) and did not re-run AD or storage writes
    assert len(env["head"].commands) == commands_before + 1
    assert [c for c in env["head"].commands if "create-user:" in c] == ad_writes_before
    assert len(env["c"].ds.passwords) == 1


def test_existing_ecr_repository_is_tagged_and_adopted(env):
    env["c"].ecr.repos["hpc-poc-teamc-envs"] = {"tags": [], "scan": {}, "encryption": {}}
    _new(env)
    _run_to_completion(env)
    assert any(name == "tag_resource" for name, _ in env["c"].ecr.calls)
    assert {"Key": "Team", "Value": "teamc"} in env["c"].ecr.repos["hpc-poc-teamc-envs"]["tags"]


def test_unknown_instance_type_fails_the_slurm_step(env):
    _new(env, type_limits={"p5.48xlarge": 1})
    results = _run_to_completion_or_fail(env)
    slurm = next(s for s in results[-1]["steps"] if s["id"] == "slurm")
    assert slurm["status"] == "failed" and "p5.48xlarge" in slurm["message"]
    assert "teamc" not in env["head"].accounts


def test_slurm_step_reruns_when_a_type_limit_differs(env):
    _new(env)
    _run_to_completion(env)
    record = lifecycle_store.get_record(CONFIG, TEAM)
    record["spec"] = {"fairshare": 10, "type_limits": {"g6e.xlarge": 3}, "members": [], "lead": None}
    steps, ctx = lifecycle.probe(CONFIG, record, "hpc-poc", "us-west-2", env["c"])
    assert steps["slurm"]["status"] == "pending"
    lifecycle.run_step("slurm", CONFIG, record, "hpc-poc", "us-west-2", env["c"], ctx)
    assert env["head"].qos["teamc-g6e-xlarge"] == "node=3"


def _run_to_completion_or_fail(env, max_calls=12):
    results = []
    for _ in range(max_calls):
        result = _advance(env)
        results.append(result)
        if result["complete"] or "failed" in _statuses(result).values():
            return results
    raise AssertionError(results[-1])


def test_existing_ad_user_with_same_mail_is_reused_without_password_reset(env):
    env["head"].ad_users["carol"] = "carol@example.com"
    _new(env)
    assert _statuses(_advance(env))["ad"] == "done"
    assert env["c"].ds.passwords == {}
    assert "carol" in env["head"].ad_members["teamc"]
    assert env["store"].record(RECORD)["ad_users_created"] == []


def test_existing_ad_user_with_other_mail_fails(env):
    env["head"].ad_users["carol"] = "carol@other.example.com"
    _new(env)
    result = _advance(env)
    ad = next(s for s in result["steps"] if s["id"] == "ad")
    assert ad["status"] == "failed" and "已属于其他邮箱" in ad["message"]
    assert "carol" not in env["head"].ad_members.get("teamc", set())
    assert env["c"].ds.passwords == {}


def test_legacy_user_without_mail_needs_link_existing(env):
    _new(env, members=(("alice@example.com", False),))
    result = _advance(env)
    assert _statuses(result)["ad"] == "failed"
    assert "关联已有 AD 用户" in next(s for s in result["steps"] if s["id"] == "ad")["message"]


def test_legacy_user_without_mail_is_linked(env):
    _new(env, members=(("alice@example.com", True),))
    assert _statuses(_advance(env))["ad"] == "done"
    assert "alice" in env["head"].ad_members["teamc"]
    assert env["c"].ds.passwords == {}


def test_existing_cognito_user_is_not_repassworded(env):
    env["c"].cognito.users["carol@example.com"] = "uuid-existing"
    _new(env, lead=None)
    _run_to_completion(env)
    assert "carol@example.com" not in env["c"].cognito.temp_passwords
    assert "hpc-poc/user-carol-pcui-temp-password" not in env["c"].secrets.values
    assert env["c"].cognito.groups["member-teamc"] == {"carol@example.com"}


def test_passwords_never_leak(env, caplog):
    caplog.set_level(logging.DEBUG)
    _new(env)
    env["c"].ecr.fail["put_lifecycle_policy"] = client_error("AccessDenied", "denied")
    results = [_advance(env) for _ in range(4)]
    del env["c"].ecr.fail["put_lifecycle_policy"]
    results += _run_to_completion(env)
    passwords = list(env["c"].ds.passwords.values()) + list(env["c"].cognito.temp_passwords.values())
    assert len(passwords) == 3
    blobs = [caplog.text, json.dumps(results), json.dumps(env["store"].params), "\n".join(env["head"].commands),
             repr(env["audit"].call_args_list), repr(env["c"].secrets.calls), repr(env["c"].cognito.calls),
             repr(env["c"].ds.calls)]
    for password in passwords:
        assert len(password) == 20
        for blob in blobs:
            assert password not in blob


def test_password_generator_meets_complexity():
    for _ in range(50):
        pw = lifecycle.generate_password()
        assert len(pw) == 20
        assert any(c.isupper() for c in pw) and any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw) and any(c in "!#%+-=_" for c in pw)


def test_storage_regression_is_rerun(env):
    _new(env)
    _run_to_completion(env)
    del env["head"].dirs["/efs/teamc/envs"]
    result = _advance(env)
    assert _statuses(result)["storage"] == "done"
    assert env["head"].dirs["/efs/teamc/envs"] == (ENV_DIR, "teamc")


def test_seeded_teama_record_uses_mapped_names(env):
    seed_record(env["store"], "teama", ad_group="teamA", team_path="/efs/teamA")
    env["c"].ecr.repos["hpc-poc-teama-envs"] = {"tags": [], "scan": {}, "encryption": {}}
    env["c"].cognito.groups["member-teama"] = set()
    record = lifecycle_store.get_record(CONFIG, "teama")
    steps, _ = lifecycle.probe(CONFIG, record, "hpc-poc", "us-west-2", env["c"])
    assert all(s["status"] == "done" for s in steps.values()), steps
    probe_command = env["head"].commands[-1]
    assert "/efs/teamA" in probe_command and " teamA " in probe_command
    assert "/efs/teama" not in probe_command


def test_adcli_missing_is_reported(env):
    env["head"].adcli_installed = False
    _new(env)
    result = _advance(env)
    ad = next(s for s in result["steps"] if s["id"] == "ad")
    assert ad["status"] == "failed" and "adcli" in ad["message"]


def test_resolve_ad_user_rule():
    assert lifecycle.resolve_ad_user({"state": "absent"}, "c@example.com", "c", False) == "create"
    assert lifecycle.resolve_ad_user({"state": "mail", "mail": "c@example.com"}, "C@example.com", "c", False) == "reuse"
    with pytest.raises(lifecycle.ADConflict):
        lifecycle.resolve_ad_user({"state": "mail", "mail": "c@other.com"}, "c@example.com", "c", True)
    with pytest.raises(lifecycle.ADConflict):
        lifecycle.resolve_ad_user({"state": "nomail"}, "c@example.com", "c", False)
    assert lifecycle.resolve_ad_user({"state": "nomail"}, "c@example.com", "c", True) == "reuse"


def test_welcome_email_off_by_default_stores_the_temporary_password(env, monkeypatch):
    monkeypatch.delenv(lifecycle.WELCOME_EMAIL_ENV, raising=False)
    _new(env, lead=None)
    _run_to_completion(env)
    c = env["c"]
    assert c.cognito.welcome_emails == []
    assert c.secrets.values["hpc-poc/user-carol-pcui-temp-password"] == c.cognito.temp_passwords["carol@example.com"]
    assert env["store"].record(RECORD)["pcui_secrets"] == ["hpc-poc/user-carol-pcui-temp-password"]


def test_welcome_email_on_lets_cognito_send_the_password(env, monkeypatch):
    monkeypatch.setenv(lifecycle.WELCOME_EMAIL_ENV, "true")
    _new(env, lead=None)
    _run_to_completion(env)
    c = env["c"]
    assert c.cognito.welcome_emails == ["carol@example.com"]
    assert "carol@example.com" not in c.cognito.temp_passwords
    assert "hpc-poc/user-carol-pcui-temp-password" not in c.secrets.values
    assert "hpc-poc/user-carol-password" in c.secrets.values  # the AD (cluster) password stays in Secrets Manager
    record = env["store"].record(RECORD)
    assert record["pcui_secrets"] == [] and record["cognito_users_created"] == ["carol@example.com"]
    assert c.cognito.groups["member-teamc"] == {"carol@example.com"}


def test_welcome_email_flag_parsing(monkeypatch):
    for value, expected in (("true", True), ("TRUE ", True), ("false", False), ("", False), ("yes", False)):
        monkeypatch.setenv(lifecycle.WELCOME_EMAIL_ENV, value)
        assert lifecycle.welcome_email_enabled() is expected
