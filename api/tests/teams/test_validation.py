import pytest

from api.teams.validation import (
    AddMember,
    CreateTeam,
    Trend,
    UpdateTeam,
    Usage,
    validate_int,
    validate_team_name,
    validate_user_name,
)

INJECTIONS = ["teama;rm -rf /", "$(id)", "`id`", "TeamA", "a" * 33, "", " teama", "teama\n", "team a",
              "1team", "-team", "teama'", 'teama"', "teama|x", "../etc", "teama&&id"]


@pytest.mark.parametrize("name", ["teama", "teamb", "a", "team_x-1", "a" * 32])
def test_valid_team_names(name):
    assert validate_team_name(name) == name


@pytest.mark.parametrize("name", INJECTIONS + [None, 5])
def test_invalid_team_names_are_rejected(name):
    with pytest.raises(ValueError):
        validate_team_name(name)


@pytest.mark.parametrize("name", ["root", "pcdefault", "normal", "borrow", "envbuild", "shared", "envs"])
def test_reserved_team_names_are_rejected(name):
    with pytest.raises(ValueError):
        validate_team_name(name)
    assert validate_team_name(name, allow_reserved=True) == name


@pytest.mark.parametrize("name", INJECTIONS + ["root", "slurm"])
def test_invalid_user_names_are_rejected(name):
    with pytest.raises(ValueError):
        validate_user_name(name)


def test_valid_user_name():
    assert validate_user_name("alice") == "alice"


@pytest.mark.parametrize("value", [True, "5", 5.0, None])
def test_validate_int_rejects_non_integers(value):
    with pytest.raises(ValueError):
        validate_int(value, 0, 10)


def test_validate_int_bounds():
    assert validate_int(10, 0, 10) == 10
    with pytest.raises(ValueError):
        validate_int(11, 0, 10)
    with pytest.raises(ValueError):
        validate_int(-1, 0, 10)


TL = {"g6e.xlarge": 2, "g6e.2xlarge": 0}


def test_create_team_schema():
    assert CreateTeam.validate({"name": "teamc", "fairshare": 10, "type_limits": TL}) == {}
    assert CreateTeam.validate({"name": "teamc", "fairshare": 10, "type_limits": TL, "budget_usd": 1500.5}) == {}
    assert CreateTeam.validate({"name": "teamc", "fairshare": 10, "type_limits": TL, "budget_usd": None}) == {}


def test_fairshare_is_optional_with_default_50():
    assert CreateTeam.load({"name": "teamc", "type_limits": TL})["fairshare"] == 50
    assert CreateTeam.load({"name": "teamc", "fairshare": 7, "type_limits": TL})["fairshare"] == 7
    body = {"name": "teamc", "type_limits": TL}
    assert CreateLifecycleTeam.load(body)["fairshare"] == 50


@pytest.mark.parametrize("body", [
    {"name": "root", "fairshare": 10, "type_limits": TL},
    {"name": "teamc;id", "fairshare": 10, "type_limits": TL},
    {"name": "teamc", "fairshare": 0, "type_limits": TL},
    {"name": "teamc", "fairshare": 10001, "type_limits": TL},
    {"name": "teamc", "fairshare": "10", "type_limits": TL},
    {"name": "teamc", "fairshare": True, "type_limits": TL},
    {"name": "teamc", "fairshare": 10},                                          # type_limits required
    {"name": "teamc", "fairshare": 10, "type_limits": {}},                       # nothing allowed
    {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge": 0}},
    {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge;id": 1}},
    {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge": 1001}},
    {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge": -1}},
    {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge": 1.5}},
    {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge": True}},
    {"name": "teamc", "fairshare": 10, "type_limits": {f"g6e.{i}xlarge": 1 for i in range(65)}},
    {"name": "teamc", "fairshare": 10, "type_limits": TL, "budget_usd": -1},
    {"name": "teamc", "fairshare": 10, "type_limits": TL, "budget_usd": "10"},
    {"name": "teamc", "fairshare": 10, "type_limits": TL, "budget_usd": float("nan")},
    {"name": "teamc", "fairshare": 10, "type_limits": TL, "billing_limit": 4},   # billing is gone
    {"name": "teamc", "fairshare": 10, "type_limits": TL, "gpu_limits": {"gres/gpu": 2}},
    {"fairshare": 10, "type_limits": TL},
])
def test_create_team_schema_rejects(body):
    assert CreateTeam.validate(body) != {}


def test_update_team_schema_requires_one_field():
    assert UpdateTeam.validate({}) != {}
    assert UpdateTeam.validate({"type_limits": {}}) != {}
    assert UpdateTeam.validate({"billing_limit": 6}) != {}
    assert UpdateTeam.validate({"fairshare": 50}) == {}
    assert UpdateTeam.validate({"type_limits": {"g6e.xlarge": 0}}) == {}   # 0 removes the type
    assert UpdateTeam.validate({"budget_usd": None}) == {}                 # clears the budget
    assert UpdateTeam.validate({"env_storage_cap_gb": 500}) == {}
    assert UpdateTeam.validate({"env_storage_cap_gb": 1.5}) != {}


def test_add_member_schema():
    assert AddMember.validate({"user": "carol"}) == {}
    assert AddMember.validate({"user": "$(id)"}) != {}
    assert AddMember.validate({"user": "root"}) != {}


def test_usage_schema():
    base = {"cluster_name": "hpc-poc", "region": "us-west-2"}
    assert Usage.validate({**base, "start": "2026-11-01", "end": "2026-12-01"}) == {}
    assert Usage.validate({**base, "start": "2026-11-01", "end": "2026-11-01"}) != {}
    assert Usage.validate({**base, "start": "2026-12-01", "end": "2026-11-01"}) != {}
    assert Usage.validate({**base, "start": "2025-01-01", "end": "2026-11-01"}) != {}
    assert Usage.validate({**base, "start": "2026-11-01;id", "end": "2026-12-01"}) != {}
    assert Usage.validate({**base, "start": "2026-13-01", "end": "2026-12-01"}) != {}
    assert Usage.validate({**base, "start": "2026-11-01", "end": "2026-12-01", "team": "teama;id"}) != {}
    assert Usage.validate({**base, "start": "2026-11-01", "end": "2026-12-01", "project": "hpc poc"}) != {}
    assert Usage.validate({"start": "2026-11-01", "end": "2026-12-01"}) != {}
    assert Usage.validate({**base, "region": "mars-1", "start": "2026-11-01", "end": "2026-12-01"}) != {}


def test_trend_schema():
    base = {"cluster_name": "hpc-poc"}
    assert Trend.validate({**base, "months": "6"}) == {}
    assert Trend.validate({**base, "months": "0"}) != {}
    assert Trend.validate({**base, "months": "13"}) != {}
    assert Trend.validate({**base, "months": "x"}) != {}


# --- lifecycle and identity validation --------------------------------------------------------------------------

from api.teams.validation import (  # noqa: E402
    CreateLifecycleTeam,
    SubmitJob,
    MemberFile,
    MemberHistory,
    normalize_email,
    validate_lifecycle_team_name,
)

LIFECYCLE_BODY = {"name": "teamc", "fairshare": 10, "type_limits": {"g6e.xlarge": 2}}


@pytest.mark.parametrize("name", ["team_c", "TeamC", "a" * 33, "root", "borrow", "1team", "team c", "teamc;id"])
def test_invalid_lifecycle_team_names(name):
    with pytest.raises(ValueError):
        validate_lifecycle_team_name(name)
    assert CreateLifecycleTeam.validate({**LIFECYCLE_BODY, "name": name}) != {}


def test_valid_lifecycle_team_name():
    assert validate_lifecycle_team_name("team-c2") == "team-c2"
    assert CreateLifecycleTeam.validate(LIFECYCLE_BODY) == {}


@pytest.mark.parametrize("email,expected", [
    ("carol@example.com", ("carol@example.com", "carol")),
    ("Carol@Example.COM", ("carol@example.com", "carol")),
    ("a.b-c_d@example.com", ("a.b-c_d@example.com", "a.b-c_d")),
    ("a" * 20 + "@example.com", ("a" * 20 + "@example.com", "a" * 20)),
])
def test_valid_emails(email, expected):
    assert normalize_email(email, ["example.com"]) == expected


@pytest.mark.parametrize("email", [
    "a" * 21 + "@example.com",      # prefix longer than sAMAccountName allows
    "1abc@example.com",             # prefix starts with a digit
    "carol+x@example.com",
    "carol@x@example.com",
    "carol@example",
    "carol@other.com",              # domain not allowed
    "root@example.com",
    "carol;id@example.com",
    "carol @example.com",
    "",
    None,
    "c" * 120 + "@example.com",
])
def test_invalid_emails(email):
    with pytest.raises(ValueError):
        normalize_email(email, ["example.com"])


def test_lifecycle_members():
    ok = {**LIFECYCLE_BODY, "members": [{"email": "carol@example.com"}, {"email": "dave@example.com",
                                                                          "link_existing": True}],
          "lead": "lead@example.com"}
    assert CreateLifecycleTeam.validate(ok) == {}
    dup = {**LIFECYCLE_BODY, "members": [{"email": "carol@example.com"}, {"email": "CAROL@example.com"}]}
    assert CreateLifecycleTeam.validate(dup) != {}
    too_many = {**LIFECYCLE_BODY, "members": [{"email": f"u{i}@example.com"} for i in range(11)]}
    assert CreateLifecycleTeam.validate(too_many) != {}
    assert CreateLifecycleTeam.validate({**LIFECYCLE_BODY, "members": [{"email": "x@example.com", "pw": "x"}]}) != {}
    assert CreateLifecycleTeam.validate({**LIFECYCLE_BODY, "lead": "not-an-email"}) != {}
    assert CreateLifecycleTeam.validate({**LIFECYCLE_BODY, "password": "x"}) != {}
    assert CreateLifecycleTeam.validate({**LIFECYCLE_BODY, "billing_limit": 4}) != {}
    assert CreateLifecycleTeam.validate({**LIFECYCLE_BODY, "budget_usd": 100, "env_storage_cap_gb": 200}) == {}


def test_add_member_email_or_user():
    assert AddMember.validate({"email": "carol@example.com", "link_existing": True}) == {}
    assert AddMember.validate({"email": "carol@example.com", "user": "carol"}) != {}
    assert AddMember.validate({}) != {}
    assert AddMember.validate({"email": "1x@example.com"}) != {}
    assert AddMember.validate({"user": "a.b"}) == {}  # dotted prefixes are valid Slurm user names


SUBMIT = {"instance_type": "g6e.xlarge", "nodes": 2, "gpus_per_node": 1, "cpus": 4, "mem_gb": 16,
          "time_minutes": 60, "script_path": "/efs/teama/a.sh"}
ENV = {"scope": "team", "team": "teama", "name": "cuda", "version": "12.4"}


def test_submit_schema():
    assert SubmitJob.validate(SUBMIT) == {}
    assert SubmitJob.validate({"instance_type": "g6e.xlarge", "time_minutes": 5, "script_path": "/efs/a.sh"}) == {}
    assert SubmitJob.validate({**SUBMIT, "script_content": "#!/bin/bash\n"}) != {}
    no_script = {k: v for k, v in SUBMIT.items() if k != "script_path"}
    assert SubmitJob.validate(no_script) != {}
    assert SubmitJob.validate({**no_script, "script_content": "#!/bin/bash\necho hi\n"}) == {}
    assert SubmitJob.validate({**no_script, "script_content": "x" * 65537}) != {}
    assert SubmitJob.validate({**no_script, "script_content": "a\x00b"}) != {}
    # time is optional; a borrowed job above 4 hours is capped when it is submitted, not rejected
    assert SubmitJob.validate({"instance_type": "g6e.xlarge", "script_path": "/efs/a.sh"}) == {}
    assert SubmitJob.validate({**SUBMIT, "borrow": True, "time_minutes": 241}) == {}
    assert SubmitJob.validate({**SUBMIT, "borrow": True}) == {}
    assert SubmitJob.validate({**SUBMIT, "time_minutes": 0}) != {}
    assert SubmitJob.validate({**SUBMIT, "time_minutes": 10081}) != {}
    assert SubmitJob.validate({**SUBMIT, "gpus_per_node": 9}) != {}
    assert SubmitJob.validate({**SUBMIT, "gpus_per_node": True}) != {}
    assert SubmitJob.validate({**SUBMIT, "nodes": 0}) != {}
    assert SubmitJob.validate({**SUBMIT, "nodes": 65}) != {}
    assert SubmitJob.validate({**SUBMIT, "instance_type": "g6e.xlarge;id"}) != {}
    assert SubmitJob.validate({**SUBMIT, "partition": "g6e-xlarge"}) != {}   # derived server-side
    assert SubmitJob.validate({**SUBMIT, "job_name": "a b"}) != {}
    assert SubmitJob.validate({**SUBMIT, "script_path": "relative.sh"}) != {}
    assert SubmitJob.validate({**SUBMIT, "script_path": "/efs/a\nb"}) != {}
    assert SubmitJob.validate({**SUBMIT, "user": "bob"}) != {}
    assert SubmitJob.validate({**SUBMIT, "env": ENV}) == {}
    assert SubmitJob.validate({**SUBMIT, "env": None}) == {}
    assert SubmitJob.validate({**SUBMIT, "env": {**ENV, "scope": "other"}}) != {}
    assert SubmitJob.validate({**SUBMIT, "env": {**ENV, "name": "../x"}}) != {}
    assert SubmitJob.validate({**SUBMIT, "env": {**ENV, "version": "1;id"}}) != {}
    assert SubmitJob.validate({**SUBMIT, "env": {**ENV, "path": "/etc"}}) != {}


def test_member_query_schemas():
    base = {"cluster_name": "hpc-poc"}
    assert MemberHistory.validate({**base, "start": "2026-11-01", "end": "2026-12-01"}) == {}
    assert MemberHistory.validate({**base, "start": "2026-10-01", "end": "2026-12-01"}) != {}
    assert MemberFile.validate({**base, "path": "/efs/teama/x.out"}) == {}
    assert MemberFile.validate({**base, "path": "/efs/teama/x.out", "limit": "16385"}) != {}
    assert MemberFile.validate({**base, "path": "/efs/teama/x.out", "mode": "head"}) != {}
    assert MemberFile.validate({**base, "path": "x.out"}) != {}


from api.teams.validation import validate_instance_type  # noqa: E402


@pytest.mark.parametrize("value", [
    "g6e.4xlarge", "p5.48xlarge", "p5en.48xlarge",
    "p6-b200.48xlarge", "p6-b300.48xlarge", "p6e-gb200.36xlarge",
])
def test_instance_type_accepts_current_gpu_families(value):
    assert validate_instance_type(value) == value


@pytest.mark.parametrize("value", [
    "", "g6", "-g6.xlarge", "p6-.48xlarge", "p6--b200.48xlarge", "G6.xlarge", "g6.xlarge;id", "g6 .xlarge", None,
])
def test_instance_type_rejects_malformed_values(value):
    with pytest.raises(ValueError):
        validate_instance_type(value)
