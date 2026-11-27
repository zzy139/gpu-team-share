import shlex

import pytest

from api.teams import slurm
from api.teams.slurm import (
    SlurmCommandError,
    add_member_cmd,
    assoc_cmd,
    cancel_user_jobs_cmd,
    chain_commands,
    create_account_cmd,
    create_std_qos_cmd,
    create_type_qos_cmd,
    delete_account_cmd,
    delete_qos_cmd,
    delete_type_qos_cmd,
    gen_type_quota_argv,
    grant_qos_cmds,
    hold_member_cmd,
    jobs_cmd,
    join_commands,
    qos_list,
    queue_cmd,
    release_member_cmd,
    remove_member_cmd,
    set_fairshare_cmd,
    set_type_qos_cmd,
    team_jobs_cmd,
    type_qos_name,
)

SACCTMGR = "/opt/slurm/bin/sacctmgr"


def roundtrip(argv):
    return shlex.split(chain_commands([argv]))


def test_type_qos_commands():
    argv = create_type_qos_cmd("teama", "g6e-xlarge", 4)
    assert argv == [SACCTMGR, "-i", "add", "qos", "teama-g6e-xlarge", "Priority=100", "GrpTRES=node=4"]
    assert roundtrip(argv) == argv
    assert set_type_qos_cmd("teama", "g6e-xlarge", 2) == [SACCTMGR, "-i", "modify", "qos", "where",
                                                          "name=teama-g6e-xlarge", "set", "GrpTRES=node=2"]
    assert delete_type_qos_cmd("teama", "g6e-xlarge") == [SACCTMGR, "-i", "delete", "qos", "where",
                                                          "name=teama-g6e-xlarge"]
    assert create_std_qos_cmd("teama") == [SACCTMGR, "-i", "add", "qos", "teama-std", "Priority=100",
                                           "GrpTRES=node=0"]


def test_qos_name_must_fit_and_not_clash_with_std():
    assert type_qos_name("teama", "g6e-48xlarge") == "teama-g6e-48xlarge"
    with pytest.raises(ValueError):
        type_qos_name("a" * 32, "b" * 30 + "x")  # 63 characters max
    with pytest.raises(ValueError):
        type_qos_name("teama", "std")
    with pytest.raises(ValueError):
        type_qos_name("teama", "G6e;id")


def test_qos_list_and_grants_match_slurm_policy():
    assert qos_list("teama", {"g6e-xlarge": 2, "g6e-2xlarge": 1}) == \
        "teama-std,borrow,envbuild,teama-g6e-2xlarge,teama-g6e-xlarge"
    account, users = grant_qos_cmds("teama", {"g6e-xlarge": 2})
    assert account == [SACCTMGR, "-i", "modify", "account", "where", "name=teama", "set",
                       "QOS=teama-std,borrow,envbuild,teama-g6e-xlarge", "DefaultQOS=teama-std"]
    assert users == [SACCTMGR, "-i", "modify", "user", "where", "account=teama", "set",
                     "QOS=teama-std,borrow,envbuild,teama-g6e-xlarge", "DefaultQOS=teama-std"]
    assert len(grant_qos_cmds("teama", {}, with_users=False)) == 1


def test_gen_type_quota_runs_only_when_installed():
    argv = gen_type_quota_argv()
    assert argv[:2] == ["bash", "-c"] and argv[-1] == "/opt/slurm/etc/gen-type-quota.sh"
    assert '[ -x "$1" ]' in argv[2]


@pytest.mark.parametrize("value", [0, 1001, -1, True, "2"])
def test_type_qos_bounds(value):
    with pytest.raises(ValueError):
        create_type_qos_cmd("teama", "g6e-xlarge", value)


def test_set_fairshare_command():
    assert set_fairshare_cmd("teama", 60) == [SACCTMGR, "-i", "modify", "account", "where", "name=teama", "set",
                                              "Fairshare=60"]


def test_create_account_command():
    assert create_account_cmd("qatest", 10) == [SACCTMGR, "-i", "add", "account", "qatest", "Description=qatest",
                                                "Organization=hpc", "Fairshare=10"]


def test_delete_and_member_commands():
    assert delete_account_cmd("qatest") == [SACCTMGR, "-i", "delete", "account", "where", "name=qatest"]
    assert delete_qos_cmd("qatest") == [SACCTMGR, "-i", "delete", "qos", "where", "name=qatest-std"]
    assert add_member_cmd("qatest", "qauser", {"g6e-xlarge": 1}) == [
        SACCTMGR, "-i", "add", "user", "qauser", "Account=qatest",
        "QOS=qatest-std,borrow,envbuild,qatest-g6e-xlarge", "DefaultQOS=qatest-std"]
    assert remove_member_cmd("qatest", "qauser") == [SACCTMGR, "-i", "delete", "user", "where", "name=qauser",
                                                     "account=qatest"]


def test_offboarding_commands():
    assert cancel_user_jobs_cmd("teama", "carol") == ["/opt/slurm/bin/scancel", "-u", "carol", "-A", "teama"]
    assert hold_member_cmd("teama", "carol") == [SACCTMGR, "-i", "modify", "user", "where", "name=carol",
                                                 "account=teama", "set", "MaxSubmitJobs=0"]
    assert release_member_cmd("teama", "carol")[-1] == "MaxSubmitJobs=-1"


def test_read_commands():
    assert jobs_cmd("2026-11-01", "2026-12-01")[5:9] == ["-S", "2026-11-01", "-E", "2026-12-01"]
    assert queue_cmd() == ["/opt/slurm/bin/squeue", "-a", "-h", "-t", "R,CF,PD", "-o", "%a|%u|%P|%D|%q|%T"]
    lead = team_jobs_cmd("teama")
    assert lead[:5] == ["/opt/slurm/bin/squeue", "-a", "-h", "-A", "teama"]
    # metadata columns only: no command (%o), work dir (%Z), stdout/stderr or comment fields
    assert not any(code in lead[-1] for code in ("%o", "%Z", "%k", "%E"))


@pytest.mark.parametrize("bad", ["teama;rm -rf /", "$(id)", "TeamA", "a" * 33, "root", "borrow"])
def test_builders_reject_bad_team_names(bad):
    for builder in (
        lambda: set_fairshare_cmd(bad, 1),
        lambda: create_account_cmd(bad, 1),
        lambda: create_std_qos_cmd(bad),
        lambda: create_type_qos_cmd(bad, "g6e-xlarge", 1),
        lambda: grant_qos_cmds(bad, {}),
        lambda: delete_account_cmd(bad),
        lambda: delete_qos_cmd(bad),
        lambda: add_member_cmd(bad, "alice", {}),
        lambda: remove_member_cmd(bad, "alice"),
        lambda: team_jobs_cmd(bad),
        lambda: cancel_user_jobs_cmd(bad, "alice"),
    ):
        with pytest.raises(ValueError):
            builder()


@pytest.mark.parametrize("bad", ["alice;id", "$(id)", "root"])
def test_builders_reject_bad_user_names(bad):
    for builder in (lambda: add_member_cmd("teama", bad, {}), lambda: remove_member_cmd("teama", bad),
                    lambda: hold_member_cmd("teama", bad), lambda: cancel_user_jobs_cmd("teama", bad)):
        with pytest.raises(ValueError):
            builder()


@pytest.mark.parametrize("value", [0, 10001, "1"])
def test_fairshare_bounds(value):
    with pytest.raises(ValueError):
        set_fairshare_cmd("teama", value)


def test_report_commands_reject_bad_dates():
    with pytest.raises(ValueError):
        jobs_cmd("2026-11-01; id", "2026-12-01")
    with pytest.raises(ValueError):
        jobs_cmd("now", "2026-12-01")


def test_join_commands_quotes_and_marks_sections():
    command = join_commands([("ASSOC", assoc_cmd()), ("MONTH_2026-11", jobs_cmd("2026-11-01", "2026-12-01"))])
    assert command.startswith("echo @@ASSOC ; /opt/slurm/bin/sacctmgr -n -P show assoc ")
    assert " ; echo @@MONTH_2026-11 ; /opt/slurm/bin/sacct " in command
    assert "billing" not in command
    with pytest.raises(ValueError):
        join_commands([("BAD;id", assoc_cmd())])


def test_chain_commands_uses_and():
    command = chain_commands([create_account_cmd("qatest", 10), *grant_qos_cmds("qatest", {})])
    assert " && " in command
    assert shlex.split(command)[:5] == [SACCTMGR, "-i", "add", "account", "qatest"]
    with pytest.raises(ValueError):
        chain_commands([])


def test_quote_argv_keeps_hostile_token_as_single_argument():
    hostile = "x'; touch /tmp/pwned; '"
    assert shlex.split(slurm.quote_argv(["echo", hostile])) == ["echo", hostile]


def test_run_command_uses_resolved_head_node_as_root(mocker):
    head = mocker.patch("api.teams.slurm.cluster.head_node", return_value="i-0123456789abcdef0")
    ssm = mocker.patch("api.teams.slurm.ssm_command", return_value="@@ASSOC\nteama||60|normal|")
    out = slurm.run_sections("hpc-poc", "us-west-2", [("ASSOC", assoc_cmd())])
    head.assert_called_once_with("hpc-poc", "us-west-2")
    region, instance_id, user, command = ssm.call_args.args
    assert (region, instance_id, user) == ("us-west-2", "i-0123456789abcdef0", "root")
    assert command.startswith("echo @@ASSOC ; /opt/slurm/bin/sacctmgr")
    assert out == {"ASSOC": ["teama||60|normal|"]}


def test_run_command_wraps_ssm_errors(mocker):
    mocker.patch("api.teams.slurm.cluster.head_node", return_value="i-0123456789abcdef0")
    mocker.patch("api.teams.slurm.ssm_command", side_effect=Exception(" sacctmgr: error: nope"))
    with pytest.raises(SlurmCommandError) as err:
        slurm.run_writes("hpc-poc", "us-west-2", [set_type_qos_cmd("teamb", "g6e-xlarge", 6)])
    assert "nope" in err.value.message


def test_rejected_name_never_reaches_ssm(mocker):
    ssm = mocker.patch("api.teams.slurm.ssm_command")
    mocker.patch("api.teams.slurm.cluster.head_node", return_value="i-0123456789abcdef0")
    with pytest.raises(ValueError):
        slurm.run_writes("hpc-poc", "us-west-2", [set_type_qos_cmd("teamb;reboot", "g6e-xlarge", 6)])
    ssm.assert_not_called()
