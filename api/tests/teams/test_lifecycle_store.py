import json

import pytest

from api.teams import lifecycle_store
from api.teams.lifecycle_store import LifecycleConfigError

from .conftest import CONFIG, seed_record


def test_unconfigured_is_none(parameter_store):
    assert lifecycle_store.load_config() is None


def test_config_is_loaded_and_cached(configured, parameter_store):
    assert lifecycle_store.load_config() == CONFIG
    parameter_store.params.clear()
    assert lifecycle_store.load_config() == CONFIG  # cached for 300 s


@pytest.mark.parametrize("field,value", [
    ("region", "us-west-2; id"),
    ("account_id", "12345"),
    ("clusters", []),
    ("clusters", ["hpc;id"]),
    ("directory_id", "d-xyz"),
    ("ad_domain", "corp internal"),
    ("ad_domain_controllers", ["10.0.0.1 && id"]),
    ("ad_admin_secret_arn", "hpc-poc/ad-admin-password"),
    ("version", 1),
    ("efs_id", "fs-x"),
    ("storage_mount", "/efs/../etc"),
    ("efs_dns", "fs-0aaaaaaaaaaaaaaaa.efs.us-west-2.amazonaws.com"),  # must match efs_id
    ("efs_dns", "evil.example.com"),
    ("ecr_repo_prefix", "Hpc Poc"),
    ("data_table", "x;y"),
    ("portal_topic_arn", "arn:aws:sqs:us-west-2:123456789012:q"),
    ("admin_topic_arn", None),
    ("env_build_project", ""),
    ("codebuild_log_group", "a b"),
    ("record_prefix", "pcui-teams"),
    ("tags", {"Project": "hpc-poc"}),
    ("email_domains", ["Example.com"]),
    ("email_domains", []),
    ("member_exec_role_arn", "arn:aws:iam::123:role/x"),
])
def test_invalid_config_is_rejected(parameter_store, field, value):
    parameter_store.params[lifecycle_store.CONFIG_PARAMETER] = json.dumps({**CONFIG, field: value})
    with pytest.raises(LifecycleConfigError):
        lifecycle_store.load_config()


def test_default_names_and_new_record():
    record = lifecycle_store.new_record(CONFIG, "teamc", {"members": [], "lead": None, "type_limits": {}})
    assert record["ad_group"] == "teamc"
    assert record["team_path"] == "/efs/teamc"
    assert record["ecr_repo"] == "hpc-poc-teamc-envs" and record["ecr_ready"] is False
    assert record["version"] == 2 and record["env_storage_cap_gb"] == 0
    assert not {"bucket", "dra_id", "fsx_path"} & set(record)
    assert (record["cognito_group"], record["member_group"], record["qos"]) == ("team-teamc", "member-teamc", "teamc-std")
    assert record["state"] == "provisioning"


def test_recreated_record_keeps_names(parameter_store):
    old = seed_record(parameter_store, "teama", ad_group="teamA", team_path="/efs/teamA", state="deleted",
                      ad_users_created=["carol"], env_storage_cap_gb=50)
    record = lifecycle_store.new_record(CONFIG, "teama", {"members": [], "lead": None, "type_limits": {}}, previous=old)
    assert (record["ad_group"], record["team_path"], record["ecr_ready"]) == ("teamA", "/efs/teamA", True)
    assert record["env_storage_cap_gb"] == 50
    assert record["ad_users_created"] == ["carol"]
    assert record["state"] == "provisioning"


def test_put_get_list_round_trip(parameter_store):
    record = lifecycle_store.new_record(CONFIG, "teamc", {"members": [
        {"email": "carol@example.com", "user": "carol", "link_existing": False}], "lead": "lead@example.com",
        "fairshare": 10, "type_limits": {"g6e.xlarge": 2}})
    lifecycle_store.put_record(CONFIG, record)
    assert parameter_store.puts == ["/pcui-teams/teams/teamc"]
    assert lifecycle_store.get_record(CONFIG, "teamc")["spec"]["members"][0]["user"] == "carol"
    seed_record(parameter_store, "teama")
    assert [r["team"] for r in lifecycle_store.list_records(CONFIG)] == ["teama", "teamc"]
    assert lifecycle_store.get_record(CONFIG, "nosuch") is None
    view = lifecycle_store.public_view(record)
    assert "spec" not in view and "lead@example.com" not in json.dumps(view)


def test_oversized_record_is_rejected(parameter_store):
    record = lifecycle_store.new_record(CONFIG, "teamc", {"members": [], "lead": None, "type_limits": {}})
    record["ad_secrets"] = [f"hpc-poc/user-{'x' * 200}{i}" for i in range(30)]
    with pytest.raises(LifecycleConfigError):
        lifecycle_store.put_record(CONFIG, record)
    assert parameter_store.puts == []


@pytest.mark.parametrize("overrides", [
    {"team_path": "/efs/../etc"},
    {"team_path": "/etc/teamc"},
    {"team_path": "/efs/teamc/sub"},
    {"team_path": "/efs/shared"},
    {"ecr_repo": "hpc-poc-other-envs"},
    {"ecr_ready": "yes"},
    {"env_storage_cap_gb": -1},
    {"version": 1},
    {"spec": {"members": [], "type_limits": {"g6e.xlarge;id": 1}}},
    {"spec": {"members": [], "type_limits": {"g6e.xlarge": 1001}}},
    {"ad_group": "team c"},
    {"ad_group": "teamc;id"},
    {"cognito_group": "admin"},
    {"member_group": "member-teamz"},
    {"state": "unknown"},
    {"ad_users_created": ["$(id)"]},
    {"ad_secrets": ["other/secret"]},
    {"spec": {"members": [{"email": "carol@example.com", "user": "bob", "link_existing": False}]}},
])
def test_tampered_record_is_rejected(parameter_store, overrides):
    seed_record(parameter_store, "teamc", **overrides)
    with pytest.raises(LifecycleConfigError):
        lifecycle_store.get_record(CONFIG, "teamc")


def test_malformed_records_are_skipped_in_list(parameter_store):
    seed_record(parameter_store, "teama")
    parameter_store.params["/pcui-teams/teams/teamz"] = "not json"
    assert [r["team"] for r in lifecycle_store.list_records(CONFIG)] == ["teama"]
