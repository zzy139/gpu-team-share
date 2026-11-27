import shlex

import pytest

from api.teams import member_exec
from api.teams.member_exec import LoginNodeUnavailable, MemberCommandError

from .conftest import CONFIG
from .fakes import client_error

LOGIN_NODE = "i-0aaaaaaaaaaaaaaa1"
SCRIPT = "echo hi"


class FakeEC2:
    def __init__(self, ids):
        self.ids, self.calls = list(ids), []

    def describe_instances(self, **kwargs):
        self.calls.append(kwargs)
        return {"Reservations": [{"Instances": [{"InstanceId": i}]} for i in self.ids]}


class FakeSSM:
    def __init__(self):
        self.sent, self.results, self.fail_send = [], [], []

    def send_command(self, **kwargs):
        self.sent.append(kwargs)
        if self.fail_send:
            raise self.fail_send.pop(0)
        return {"Command": {"CommandId": f"cmd-{len(self.sent)}"}}

    def get_command_invocation(self, CommandId, InstanceId):
        if self.results:
            return self.results.pop(0)
        return {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "ok", "StandardErrorContent": ""}


class FakeSession:
    def __init__(self, ec2, ssm):
        self.ec2, self.ssm, self.clients = ec2, ssm, []

    def client(self, name, region_name=None):
        self.clients.append((name, region_name))
        return {"ec2": self.ec2, "ssm": self.ssm}[name]


@pytest.fixture
def env(mocker):
    ec2, ssm = FakeEC2([LOGIN_NODE, "i-0bbbbbbbbbbbbbbb2"]), FakeSSM()
    session = FakeSession(ec2, ssm)
    member_session = mocker.patch("api.teams.member_exec.member_session", return_value=session)
    mocker.patch("api.teams.member_exec.time.sleep")
    default_client = mocker.patch("boto3.client", side_effect=AssertionError("default credentials must not be used"))
    head = mocker.patch("api.teams.cluster.head_node", side_effect=AssertionError("never the head node"))
    mocker.patch("api.teams.member_exec.logger", new=mocker.Mock())
    return {"ec2": ec2, "ssm": ssm, "session": session, "member_session": member_session,
            "default_client": default_client, "head": head}


def test_command_runs_on_login_node_as_member(env):
    code, out, err = member_exec.run_as_member(CONFIG, "hpc-poc", "us-west-2", "carol", SCRIPT, ["jobs"])
    assert (code, out) == (0, "ok")
    assert env["ec2"].calls[0]["Filters"] == [
        {"Name": "tag:parallelcluster:cluster-name", "Values": ["hpc-poc"]},
        {"Name": "tag:parallelcluster:node-type", "Values": ["LoginNode"]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ]
    sent = env["ssm"].sent[0]
    assert sent["InstanceIds"] == [LOGIN_NODE]
    assert sent["DocumentName"] == "AWS-RunShellScript"
    assert "CloudWatchOutputConfig" not in sent
    command = sent["Parameters"]["commands"][0]
    assert command.startswith("sudo -n -H -u carol -- /bin/bash --noprofile --norc -c ")
    assert shlex.split(command) == ["sudo", "-n", "-H", "-u", "carol", "--", "/bin/bash", "--noprofile", "--norc",
                                    "-c", SCRIPT, "pcui-member", "jobs"]
    env["member_session"].assert_called_once_with(CONFIG, "carol")
    assert ("ssm", "us-west-2") in env["session"].clients
    env["default_client"].assert_not_called()
    env["head"].assert_not_called()


def test_no_login_node_never_sends_a_command(env):
    env["ec2"].ids = []
    with pytest.raises(LoginNodeUnavailable) as err:
        member_exec.run_as_member(CONFIG, "hpc-poc", "us-west-2", "carol", SCRIPT, ["jobs"])
    assert err.value.status == 503 and err.value.message == "登录节点暂不可用"
    assert env["ssm"].sent == []
    env["head"].assert_not_called()


@pytest.mark.parametrize("user", ["root", "slurm", "Carol", "carol;id", "$(id)", "a" * 21, "", None])
def test_invalid_user_never_reaches_ssm(env, user):
    with pytest.raises(ValueError):
        member_exec.run_as_member(CONFIG, "hpc-poc", "us-west-2", user, SCRIPT, ["jobs"])
    assert env["ssm"].sent == []


def test_replaced_login_node_is_rediscovered_once(env):
    env["ssm"].fail_send = [client_error("InvalidInstanceId")]
    member_exec._login_node_cache.set(("hpc-poc", "us-west-2"), "i-0ddddddddddddddd9")
    member_exec.run_as_member(CONFIG, "hpc-poc", "us-west-2", "carol", SCRIPT, ["jobs"])
    assert [s["InstanceIds"] for s in env["ssm"].sent] == [["i-0ddddddddddddddd9"], [LOGIN_NODE]]


def test_second_invalid_instance_is_unavailable(env):
    env["ssm"].fail_send = [client_error("InvalidInstanceId"), client_error("InvalidInstanceId")]
    with pytest.raises(LoginNodeUnavailable):
        member_exec.run_as_member(CONFIG, "hpc-poc", "us-west-2", "carol", SCRIPT, ["jobs"])


def test_timeout_is_504(env, mocker):
    env["ssm"].results = [{"Status": "InProgress"}] * 1000
    clock = iter(range(0, 10000, 5))
    mocker.patch("api.teams.member_exec.time.monotonic", side_effect=lambda: next(clock))
    with pytest.raises(MemberCommandError) as err:
        member_exec.run_as_member(CONFIG, "hpc-poc", "us-west-2", "carol", SCRIPT, ["jobs"])
    assert err.value.status == 504


def test_pending_invocation_is_polled(env):
    env["ssm"].results = [{"Status": "Pending"}, {"Status": "InProgress"},
                          {"Status": "Failed", "ResponseCode": 6, "StandardOutputContent": "", "StandardErrorContent": ""}]
    code, _, _ = member_exec.run_as_member(CONFIG, "hpc-poc", "us-west-2", "carol", SCRIPT, ["cancel", "1"])
    assert code == 6


@pytest.mark.parametrize("code,stderr,status", [
    (4, "", 403), (5, "", 404), (6, "", 403), (7, "", 413), (8, "", 403),
    (1, "sudo: unknown user carol", 403),
    (1, "sbatch: error: Batch job submission failed: Invalid qos specification", 400),
])
def test_exit_codes_map_to_http_status(code, stderr, status):
    with pytest.raises(MemberCommandError) as err:
        member_exec.check_result(code, "", stderr)
    assert err.value.status == status
    if "unknown user" in stderr:
        assert err.value.message == "集群账号尚未生效，请联系管理员"
    if "sbatch" in stderr:
        assert "Invalid qos" in err.value.message


def test_member_session_assumes_the_member_role(mocker):
    sts = mocker.Mock()
    sts.assume_role.return_value = {"Credentials": {"AccessKeyId": "AKIA", "SecretAccessKey": "s", "SessionToken": "t"}}
    mocker.patch("api.teams.member_exec._sts_client", return_value=sts)
    session = member_exec.member_session(CONFIG, "carol")
    member_exec.member_session(CONFIG, "carol")
    sts.assume_role.assert_called_once_with(RoleArn=CONFIG["member_exec_role_arn"],
                                            RoleSessionName="pcui-member-carol", DurationSeconds=3600)
    assert session.get_credentials().access_key == "AKIA"


def test_module_does_not_use_the_head_node():
    import inspect
    source = inspect.getsource(member_exec)
    assert "head_node" not in source and "api.teams.cluster" not in source
