"""Member self-service routes: scoping, login-node execution and denial matrix."""
import base64
import gzip
import json
import shlex

import pytest

from api.teams import cluster, costs
from api.teams.costs import TeamCostExplorerClient

from api.teams.member_commands import MEMBER_SCRIPT

from . import slurm_outputs as out
from .conftest import seed_record, walk_keys
from .fakes import FakeSNS
from .test_routes import HEAD_NODE, FakeHeadNode

Q = "cluster_name=hpc-poc&region=us-west-2"
LOGIN_NODE = "i-0aaaaaaaaaaaaaaa1"
SCRIPT = "#!/bin/bash\n#SBATCH --gres=gpu:1\necho 'secret-body' $(hostname)\n"


def _gz(text):
    return base64.b64encode(gzip.compress(text.encode())).decode()


class FakeLoginNode:
    """send_command / get_command_invocation of the member session; answers by member-script operation."""

    def __init__(self):
        self.sent = []
        self.responses = {
            "jobs": (0, _gz("12|train|teama|g6e-xlarge|PENDING|QOSGrpNodeLimit|2026-11-02T10:00:00|N/A|1:00:00|0:00|"
                            "gres/gpu:1|4|16G|teama-g6e-xlarge\n"), ""),
            "history": (0, _gz(""), ""),
            "job": (0, "@@SCONTROL\nJobId=12 JobName=train UserId=carol(1) Account=teama JobState=RUNNING "
                       "WorkDir=/efs/teamA StdOut=/efs/teamA/slurm-12.out StdErr=/efs/teamA/slurm-12.out\n", ""),
            "read": (0, "@@SIZE 6\n@@OFFSET 0\n@@PATH /efs/teamA/slurm-12.out\n@@DATA\n"
                        + base64.b64encode(b"hello\n").decode() + "\n", ""),
            "list": (0, "@@PATH /efs/teamA\n@@DATA\n" + base64.b64encode(b"f\t6\t1.0\tslurm-12.out\0").decode(), ""),
            "submit": (0, "@@JOB 13\n@@SCRIPT /efs/teamA/.pcui-jobs/carol/x.sbatch\n", ""),
            "cancel": (0, "@@CANCELLED 12\n", ""),
            "env-list": (0, "@@LIST " + base64.b64encode(gzip.compress(json.dumps({"envs": [
                {"scope": "team", "team": "teamb", "name": "af2", "version": "2.3", "status": "succeeded",
                 "kind": "conda", "path": "/efs/envs/teamb/af2/2.3", "visibility": "all"},
                {"scope": "team", "team": "teama", "name": "cuda", "version": "12.4", "status": "succeeded",
                 "kind": "container", "path": "/efs/envs/teama/cuda/12.4", "visibility": "team"},
                {"scope": "team", "team": "teama", "name": "cuda", "version": "12.5", "status": "failed",
                 "kind": "container", "path": "/efs/envs/teama/cuda/12.5", "visibility": "team"}],
                "truncated": False}).encode())).decode() + "\n@@RESULT listed\n", ""),
            "resubmit": (0, "@@FIELDS 12|carol|g6e-xlarge|2|01:30:00|teama-g6e-xlarge|"
                            "billing=2,cpu=8,gres/gpu=2,mem=0,node=2\n@@NAME train\n"
                            "@@COMMENT env:team/teama/cuda/12.4\n@@WORKDIR /efs/teamA/w\n@@CONTENT "
                         + _gz("#!/bin/bash\necho resubmitted\n") + "\n", ""),
        }
        self._last = None

    def describe_instances(self, **kwargs):
        return {"Reservations": [{"Instances": [{"InstanceId": LOGIN_NODE}]}]}

    def send_command(self, **kwargs):
        argv = shlex.split(kwargs["Parameters"]["commands"][0])
        self.sent.append({"instance": kwargs["InstanceIds"], "argv": argv, "raw": kwargs["Parameters"]["commands"][0]})
        self._last = argv[argv.index("pcui-member") + 1]
        return {"Command": {"CommandId": "cmd-1"}}

    def get_command_invocation(self, CommandId, InstanceId):
        code, stdout, stderr = self.responses[self._last]
        return {"Status": "Success" if code == 0 else "Failed", "ResponseCode": code,
                "StandardOutputContent": stdout, "StandardErrorContent": stderr}

    @property
    def ops(self):
        return [s["argv"][s["argv"].index("pcui-member") + 1] for s in self.sent]

    def client(self, name, region_name=None):
        return self


@pytest.fixture
def env(mocker, configured, parameter_store):
    seed_record(parameter_store, "teama", ad_group="teamA", team_path="/efs/teamA")
    seed_record(parameter_store, "teamb", ad_group="teamB", team_path="/efs/teamB")
    cluster._head_node_cache.clear()
    cluster._config_cache.clear()
    costs._ce_cache.clear()
    login = FakeLoginNode()
    mocker.patch("api.teams.member_exec.member_session", return_value=login)
    mocker.patch("api.teams.member_exec.time.sleep")
    head = FakeHeadNode()
    mocker.patch("api.teams.slurm.ssm_command", side_effect=head)
    head_node = mocker.patch("api.teams.cluster.head_node", return_value=HEAD_NODE)
    mocker.patch("api.teams.cluster.get_cluster_config_text", return_value=out.CLUSTER_CONFIG)
    ce = mocker.Mock()
    ce.get_cost_and_usage.return_value = {"ResultsByTime": []}
    mocker.patch("api.teams.routes.cost_client", return_value=TeamCostExplorerClient(ce))
    audit_logger = mocker.patch("api.teams.audit.logger", new=mocker.Mock())
    return {"login": login, "head": head, "head_node": head_node, "audit": audit_logger}


@pytest.fixture
def member(mocker, client):
    def _login(groups=("member-teama",), email="carol@example.com", verified=True, sub="sub-1"):
        access = {"cognito:groups": list(groups), "username": "uuid-1", "sub": "sub-1"}
        id_claims = {"sub": sub, "token_use": "id", "email": email, "email_verified": verified}

        def _decode(token, audience=None, access_token=None):
            return dict(id_claims) if token == "id-token" else dict(access)

        mocker.patch("api.PclusterApiHandler.jwt_decode", side_effect=_decode)
        mocker.patch("api.teams.member_identity.jwt_decode", side_effect=_decode)
        client.set_cookie("accessToken", "access-token")
        client.set_cookie("idToken", "id-token")
        return client

    return _login


MEMBER_READS = [
    "/team-quota/member/me?region=us-west-2",
    f"/team-quota/member/options?{Q}",
    f"/team-quota/member/jobs?{Q}",
    f"/team-quota/member/history?{Q}&start=2026-11-01&end=2026-11-20",
    f"/team-quota/member/jobs/12?{Q}",
    f"/team-quota/member/files?{Q}&path=/efs/teamA/slurm-12.out",
    f"/team-quota/member/dir?{Q}&path=/efs/teamA",
    f"/team-quota/member/team?{Q}&start=2026-11-01&end=2026-12-01",
    f"/team-quota/member/jobs/12/resubmit?{Q}",
    f"/team-quota/member/budget?{Q}",
    "/team-quota/member/templates?region=us-west-2",
    "/team-quota/member/notifications?region=us-west-2",
    "/team-quota/member/preferences?region=us-west-2",
]
TEMPLATE = {"name": "train 1x", "job": {"instance_type": "g6e.xlarge", "time_minutes": 30, "script_content": SCRIPT}}
MEMBER_WRITES = [
    ("post", f"/team-quota/member/jobs?{Q}", {"instance_type": "g6e.xlarge", "time_minutes": 10,
                                               "script_content": SCRIPT}),
    ("post", f"/team-quota/member/jobs/12/cancel?{Q}", {}),
    ("post", "/team-quota/member/templates?region=us-west-2", TEMPLATE),
    ("patch", f"/team-quota/member/templates/{'a' * 32}?region=us-west-2", {"name": "x"}),
    ("delete", f"/team-quota/member/templates/{'a' * 32}?region=us-west-2", None),
    ("post", "/team-quota/member/notifications/read?region=us-west-2", {}),
    ("put", "/team-quota/member/preferences?region=us-west-2", {"job_notify": True}),
]
LOGIN_NODE_ROUTES = {"jobs": MEMBER_READS[2], "history": MEMBER_READS[3], "job": MEMBER_READS[4],
                     "read": MEMBER_READS[5], "list": MEMBER_READS[6]}


# --- authentication and authorization ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", MEMBER_READS)
def test_anonymous_member_read_is_401(client, env, url):
    assert client.get(url).status_code == 401
    assert env["login"].sent == []


def _send(client, method, url, body):
    return getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)


@pytest.mark.parametrize("method,url,body", MEMBER_WRITES)
def test_anonymous_member_write_is_401(client, env, method, url, body):
    assert _send(client, method, url, body).status_code == 401


@pytest.mark.parametrize("method,url,body", MEMBER_WRITES)
def test_member_write_without_csrf_is_rejected(member, env, dynamo, method, url, body):
    # same convention as test_routes.test_write_requires_csrf: the CSRF check answers 400 or 403
    assert _send(member(), method, url, body).status_code in (400, 403)
    assert env["login"].sent == [] and not dynamo.calls


@pytest.mark.parametrize("groups", [["admin"], ["team-teama"], ["viewer"]])
@pytest.mark.parametrize("method,url,body", MEMBER_WRITES)
def test_non_members_cannot_write_member_routes(member, env, mock_csrf_needed, dynamo, groups, method, url, body):
    assert _send(member(groups=groups), method, url, body).status_code == 403
    assert env["login"].sent == [] and not dynamo.calls


@pytest.mark.parametrize("groups", [["admin"], ["team-teama"], ["viewer"]])
@pytest.mark.parametrize("url", MEMBER_READS)
def test_non_members_cannot_use_member_routes(member, env, groups, url):
    assert member(groups=groups).get(url).status_code == 403
    assert env["login"].sent == [] and env["head"].commands == []


@pytest.mark.parametrize("method,url", [
    ("get", f"/team-quota/lifecycle/teams?{Q}"),
    ("post", f"/team-quota/lifecycle/teams?{Q}"),
    ("post", f"/team-quota/teams?{Q}"),
    ("patch", f"/team-quota/teams/teama?{Q}"),
    ("delete", f"/team-quota/teams/teama?{Q}"),
    ("get", f"/team-quota/teams?{Q}"),
    ("get", f"/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01"),
    ("get", f"/team-quota/trend?{Q}"),
    ("get", "/team-quota/clusters?region=us-west-2"),
    ("get", f"/team-quota/catalog?{Q}"),
    ("get", f"/team-quota/lead/jobs?{Q}&team=teama"),
    ("get", f"/team-quota/lead/quota?{Q}&team=teama"),
    ("get", "/team-quota/audit"),
    ("post", f"/team-quota/teams/teama/members/alice/offboard?{Q}"),
    ("post", f"/team-quota/teams/teama/members/alice/reenable?{Q}"),
    ("get", "/manager/queue_status?cluster_name=hpc-poc&region=us-west-2"),
    ("post", "/manager/sacct?cluster_name=hpc-poc&region=us-west-2"),
    ("get", "/manager/scontrol_job?cluster_name=hpc-poc&region=us-west-2&job_id=12"),
    ("get", "/manager/cancel_job?cluster_name=hpc-poc&region=us-west-2&job_id=12"),
    ("post", "/logs"),
])
def test_member_cannot_use_admin_lead_or_upstream_routes(member, env, mock_csrf_needed, method, url):
    client = member()
    response = getattr(client, method)(url, json={"logs": []} if method != "get" else None)
    assert response.status_code == 403
    assert env["login"].sent == [] and env["head"].commands == []


def test_member_asking_for_other_team_is_403(member, env):
    assert member().get(f"/team-quota/member/jobs?{Q}&team=teamb").status_code == 403
    assert env["login"].sent == []


def test_unverified_email_is_403(member, env):
    assert member(verified=False).get(f"/team-quota/member/jobs?{Q}").status_code == 403


def test_identity_ignores_request_values(member, env):
    response = member().get(f"/team-quota/member/jobs?{Q}&user=bob&email=bob@example.com")
    assert response.status_code == 200
    assert env["login"].sent[0]["argv"][:5] == ["sudo", "-n", "-H", "-u", "carol"]


def test_unmanaged_cluster_is_400(member, env):
    assert member().get("/team-quota/member/jobs?cluster_name=other&region=us-west-2").status_code == 400


# --- behavior ---------------------------------------------------------------------------------------------------


def test_me(member, env):
    body = member().get("/team-quota/member/me?region=us-west-2").json
    assert body == {"email": "carol@example.com", "user": "carol", "teams": [{"team": "teama", "team_path": "/efs/teamA"}],
                    "clusters": [{"name": "hpc-poc", "region": "us-west-2"}]}


@pytest.mark.parametrize("op", sorted(LOGIN_NODE_ROUTES))
def test_member_reads_run_on_the_login_node_as_the_member(member, env, op):
    # (a pending job adds a read-only head-node call for the quota context: see test_jobs_have_contextual_reason_text)
    env["login"].responses["jobs"] = (0, _gz("12|train|teama|g6e-xlarge|RUNNING|None|2026-11-02T10:00:00|"
                                             "2026-11-02T10:01:00|1:00:00|0:10|gres/gpu:1|4|16G|teama-g6e-xlarge\n"), "")
    response = member().get(LOGIN_NODE_ROUTES[op])
    assert response.status_code == 200, response.json
    sent = env["login"].sent
    assert len(sent) == 1 and sent[0]["instance"] == [LOGIN_NODE]
    assert sent[0]["argv"][:5] == ["sudo", "-n", "-H", "-u", "carol"]
    assert env["login"].ops == [op]
    assert env["head"].commands == []
    env["head_node"].assert_not_called()


def test_jobs_have_contextual_reason_text(member, env):
    jobs = member().get(f"/team-quota/member/jobs?{Q}").json["jobs"]
    assert jobs[0]["instance_type"] == "g6e.xlarge"
    assert jobs[0]["reason_text"] == "g6e.xlarge 团队配额已用满（2/2 台）。可改用 g6e.2xlarge（剩余 1 台），或开启借用"
    assert jobs[0]["alerts"] == []
    # the team context is read on the head node (aggregated, read-only); the job list itself on the login node
    assert env["login"].ops == ["jobs"]
    assert all(" -i " not in c for c in env["head"].commands)


def test_file_read_passes_the_team_root_and_is_audited(member, env):
    response = member().get(f"/team-quota/member/files?{Q}&path=/efs/teamA/slurm-12.out&limit=100")
    assert base64.b64decode(response.json["content_b64"]) == b"hello\n"
    argv = env["login"].sent[0]["argv"]
    # readable roots: the team directory and the team's environments (build logs); $HOME is added on the node
    assert argv[-7:] == ["read", "/efs/teamA/slurm-12.out", "tail", "0", "100", "/efs/teamA", "/efs/envs/teama"]
    extra = env["audit"].info.call_args.kwargs["extra"]
    assert (extra["action"], extra["team"], extra["result"]) == ("member_read_file", "teama", "success")


def test_file_outside_roots_is_403(member, env):
    env["login"].responses["read"] = (4, "", "")
    response = member().get(f"/team-quota/member/files?{Q}&path=/efs/teamA/../teamB/x.out")
    assert response.status_code == 403
    assert env["audit"].info.call_args.kwargs["extra"]["result"] == "rejected"


def test_other_users_job_is_403(member, env):
    env["login"].responses["job"] = (6, "", "")
    assert member().get(f"/team-quota/member/jobs/99?{Q}").status_code == 403


def test_job_detail_owner_is_checked_again(member, env):
    env["login"].responses["job"] = (0, "@@SCONTROL\nJobId=12 UserId=bob(2) JobState=RUNNING\n", "")
    assert member().get(f"/team-quota/member/jobs/12?{Q}").status_code == 403


def test_unknown_cluster_user_is_403(member, env):
    env["login"].responses["jobs"] = (1, "", "sudo: unknown user carol\n")
    response = member().get(f"/team-quota/member/jobs?{Q}")
    assert response.status_code == 403 and response.json["message"] == "集群账号尚未生效，请联系管理员"


def test_no_login_node_is_503_without_head_node_fallback(member, env, mocker):
    mocker.patch.object(env["login"], "describe_instances", return_value={"Reservations": []})
    response = member().get(f"/team-quota/member/jobs?{Q}")
    assert response.status_code == 503 and response.json["message"] == "登录节点暂不可用"
    assert env["login"].sent == [] and env["head"].commands == []


def _submit_argv(env):
    argv = env["login"].sent[-1]["argv"]
    return argv[argv.index("pcui-member") + 1:]


def test_options_show_types_with_team_limits(member, env):
    body = member().get(f"/team-quota/member/options?{Q}").json
    types = {t["instance_type"]: t for t in body["types"]}
    assert types["g6e.xlarge"] == {"instance_type": "g6e.xlarge", "partition": "g6e-xlarge", "gpus": 1, "vcpus": 4,
                                   "memory_gib": 32.0, "gpu_model": "NVIDIA L40S", "gpu_memory_gib": 44.7,
                                   "gpu_memory_gb": 48, "price_per_hour": 1.861, "limit": 2, "running": 2,
                                   "remaining": 0, "has_quota": True, "borrow_allowed": True, "status": "startable"}
    assert types["g6e.2xlarge"]["remaining"] == 1 and types["g6e.12xlarge"]["limit"] == 0
    # capacity from sinfo: ICE reason on g6e-2xlarge, every g6e-12xlarge node in use
    assert types["g6e.2xlarge"]["status"] == "no_capacity" and types["g6e.12xlarge"]["status"] == "busy"
    assert types["g6e.12xlarge"]["has_quota"] is False and types["g6e.2xlarge"]["has_quota"] is True
    # no CPU / memory / GPU / time maxima any more: the form derives everything from the instance type
    assert body["limits"] == {"nodes_max": 64, "borrow_max_minutes": 240}
    assert any("/opt/slurm/bin/sinfo -a -h -o '%R|%T|%D|%E'" in c for c in env["head"].commands)
    assert {q["queue"]: q["reason"] for q in body["unmanaged_queues"]} == {"build": "no_gpu", "mixed": "multi_type"}
    assert body["team_root"] == "/efs/teamA" and body["shared_root"] == "/efs/shared"
    assert body["envs_root"] == "/efs/envs"
    assert body["budget"] is None
    # the runtime environment dropdown: succeeded environments only, read on the login node as the member; the
    # team's own first, then the 所有人 environments of other teams
    assert body["envs"] == [{"scope": "team", "team": "teama", "name": "cuda", "version": "12.4",
                             "kind": "container", "description": None, "detected": None, "visibility": "team"},
                            {"scope": "team", "team": "teamb", "name": "af2", "version": "2.3",
                             "kind": "conda", "description": None, "detected": None, "visibility": "all"}]
    assert env["login"].sent[0]["argv"][-3:] == ["env-list", "/efs/envs", "teama"]
    assert body["envs_error"] is None
    assert env["login"].ops == ["env-list"]


def test_submit_pasted_script(member, env, mock_csrf_needed):
    response = member().post(f"/team-quota/member/jobs?{Q}", json={
        "instance_type": "g6e.12xlarge", "nodes": 2, "gpus_per_node": 2, "cpus": 8, "mem_gb": 64,
        "time_minutes": 60, "borrow": True, "script_content": SCRIPT})
    assert response.status_code == 201
    assert response.json == {"job_id": "13", "script_path": "/efs/teamA/.pcui-jobs/carol/x.sbatch"}
    sent = env["login"].sent[0]
    assert "secret-body" not in sent["raw"]
    argv = _submit_argv(env)
    assert argv[:4] == ["submit", "/efs/teamA", "/efs/teamA", "content"]
    assert gzip.decompress(base64.b64decode(argv[4])).decode() == SCRIPT
    assert argv[6:9] == ["-", "-", "-"]  # no runtime environment
    assert argv[9:] == ["--account=teama", "--qos=borrow", "--partition=g6e-12xlarge", "--nodes=2",
                        "--job-name=pcui-job", "--time=60", "--mem=64G", "--cpus-per-task=8", "--gres=gpu:2"]
    extra = env["audit"].info.call_args.kwargs["extra"]
    assert extra["action"] == "member_submit" and extra["result"] == "success"
    assert extra["params"]["partition"] == "g6e-12xlarge" and extra["params"]["instance_type"] == "g6e.12xlarge"
    assert len(extra["params"]["script_sha256"]) == 64 and extra["params"]["script_bytes"] == len(SCRIPT.encode())
    assert "secret-body" not in json.dumps(extra, default=str)
    assert "secret-body" not in response.get_data(as_text=True)


def test_submit_defaults_to_whole_machines_and_type_qos(member, env, mock_csrf_needed):
    response = member().post(f"/team-quota/member/jobs?{Q}", json={
        "instance_type": "g6e.xlarge", "time_minutes": 30, "script_path": "/efs/teamA/run.sh",
        "workdir": "/efs/teamA/w"})
    assert response.status_code == 201
    argv = _submit_argv(env)
    assert argv[:5] == ["submit", "/efs/teamA", "/efs/teamA/w", "path", "/efs/teamA/run.sh"]
    assert argv[9:] == ["--account=teama", "--qos=teama-g6e-xlarge", "--partition=g6e-xlarge", "--nodes=1",
                        "--job-name=pcui-job", "--time=30", "--mem=0", "--gres=gpu:1"]


@pytest.mark.parametrize("env_ref,expected", [
    ({"scope": "team", "team": "teama", "name": "cuda", "version": "12.4"}, "/efs/envs/teama/cuda/12.4"),
    # another team's environment and old references (published copies) resolve to the same layout
    ({"scope": "team", "team": "teamb", "name": "af2", "version": "2.3.2"}, "/efs/envs/teamb/af2/2.3.2"),
    ({"scope": "shared", "team": "teamb", "name": "af2", "version": "2.3.2"}, "/efs/envs/teamb/af2/2.3.2"),
])
def test_submit_with_runtime_environment(member, env, mock_csrf_needed, env_ref, expected):
    response = member().post(f"/team-quota/member/jobs?{Q}", json={
        "instance_type": "g6e.xlarge", "time_minutes": 30, "script_content": SCRIPT, "env": env_ref})
    assert response.status_code == 201, response.json
    argv = _submit_argv(env)
    assert argv[6:9] == [expected, "/efs/shared", "/efs/envs"]
    comment = f"--comment=env:{env_ref['scope']}/{env_ref['team']}/{env_ref['name']}/{env_ref['version']}"
    assert argv[-1] == comment


def test_member_script_wraps_the_job_in_the_environment():
    assert "srun --container-image=%q --container-mounts=%q --container-mount-home --container-workdir=%q" \
        in MEMBER_SCRIPT
    assert "source %q %q" in MEMBER_SCRIPT and "tools/hpc-activate" in MEMBER_SCRIPT
    assert '[ "$st" = succeeded ] || exit 9' in MEMBER_SCRIPT
    assert '[[ "$p" == "$er/"* ]] || exit 9' in MEMBER_SCRIPT


def test_submit_rejections(member, env, mock_csrf_needed):
    client = member()
    base = {"instance_type": "g6e.xlarge", "time_minutes": 30, "script_path": "/efs/teamA/run.sh"}
    bad = [
        ({**base, "instance_type": "p5.48xlarge"}, 400),                       # no queue for the type
        ({**base, "instance_type": "g6.xlarge"}, 400),                         # only in the multi-type queue
        ({**base, "instance_type": "m7i.xlarge"}, 400),                        # CPU build queue
        ({**base, "gpus_per_node": 2}, 400),                                   # g6e.xlarge has 1 GPU
        ({**base, "cpus": 5}, 400),
        ({**base, "mem_gb": 33}, 400),
        ({**base, "partition": "g6e-xlarge"}, 400),
        ({"instance_type": "g6e.xlarge", "time_minutes": 30}, 400),
        ({**base, "user": "bob"}, 400),
        ({**base, "team": "teamb"}, 403),
        ({**base, "env": {"scope": "team", "team": "teama", "name": "..", "version": "1"}}, 400),
        ({**base, "env": {"scope": "team", "team": "../x", "name": "cuda", "version": "1"}}, 400),
    ]
    for body, status in bad:
        assert client.post(f"/team-quota/member/jobs?{Q}", json=body).status_code == status, body
    assert env["login"].sent == []


@pytest.mark.parametrize("body,expected_time", [
    ({}, []),                                                   # own quota: no time limit
    ({"time_minutes": 120}, ["--time=120"]),                    # 高级选项: an optional shorter limit
    ({"borrow": True}, ["--time=240"]),                         # borrowed: 4 hours at most
    ({"borrow": True, "time_minutes": 300}, ["--time=240"]),    # capped, not rejected
])
def test_submit_time_limit_rules(member, env, mock_csrf_needed, body, expected_time):
    response = member().post(f"/team-quota/member/jobs?{Q}", json={
        "instance_type": "g6e.xlarge", "script_path": "/efs/teamA/run.sh", **body})
    assert response.status_code == 201, response.json
    assert [a for a in _submit_argv(env) if a.startswith("--time")] == expected_time


def test_lua_rejection_is_shown_without_the_sbatch_prefix(member, env, mock_csrf_needed):
    message = ("Team teama has no quota for g6e.2xlarge (queue g6e-2xlarge); choose one of g6e.xlarge or add "
               "--qos=borrow.")
    env["login"].responses["submit"] = (1, "", f"sbatch: error: {message}\n"
                                               "sbatch: error: Batch job submission failed: Unspecified error\n")
    response = member().post(f"/team-quota/member/jobs?{Q}", json={
        "instance_type": "g6e.xlarge", "time_minutes": 30, "script_path": "/efs/teamA/run.sh"})
    assert response.status_code == 400
    assert response.json["message"] == message
    assert env["audit"].info.call_args.kwargs["extra"]["result"] == "failure"


@pytest.mark.parametrize("env_team", ["teama", "teamb"])
def test_environment_not_readable_or_not_ready_is_403(member, env, mock_csrf_needed, env_team):
    # exit 9 of the member script: another team's 仅本团队 environment (the kernel denies the member) or a version
    # that is not built
    env["login"].responses["submit"] = (9, "", "")
    response = member().post(f"/team-quota/member/jobs?{Q}", json={
        "instance_type": "g6e.xlarge", "time_minutes": 30, "script_path": "/efs/teamA/run.sh",
        "env": {"scope": "team", "team": env_team, "name": "cuda", "version": "12.4"}})
    assert response.status_code == 403
    assert response.json["message"] == "该环境仅对其所属团队可见，或尚未构建成功"
    assert _submit_argv(env)[6] == f"/efs/envs/{env_team}/cuda/12.4"
    assert env["audit"].info.call_args.kwargs["extra"]["result"] == "failure"


def test_resubmit_prefills_the_submit_form(member, env):
    response = member().get(f"/team-quota/member/jobs/12/resubmit?{Q}")
    assert response.status_code == 200, response.json
    assert response.json == {"job_id": "12", "partition": "g6e-xlarge", "job": {
        "instance_type": "g6e.xlarge", "nodes": 2, "gpus_per_node": 1, "time_minutes": 90, "borrow": False,
        "env": {"scope": "team", "team": "teama", "name": "cuda", "version": "12.4"}, "job_name": "train",
        "workdir": "/efs/teamA/w", "script_content": "#!/bin/bash\necho resubmitted\n"}}
    assert env["login"].sent[0]["argv"][-3:] == ["resubmit", "12", "/efs/teamA"]
    env["login"].responses["resubmit"] = (6, "", "")
    assert member().get(f"/team-quota/member/jobs/13/resubmit?{Q}").status_code == 403


def test_cancel_own_job_only(member, env, mock_csrf_needed):
    client = member()
    assert client.post(f"/team-quota/member/jobs/12/cancel?{Q}", json={}).json == {"job_id": "12", "result": "cancelled"}
    env["login"].responses["cancel"] = (6, "", "")
    assert client.post(f"/team-quota/member/jobs/13/cancel?{Q}", json={}).status_code == 403
    assert client.post(f"/team-quota/member/jobs/1;id/cancel?{Q}", json={}).status_code in (400, 404)
    actions = [c.kwargs["extra"]["result"] for c in env["audit"].info.call_args_list
               if c.kwargs["extra"]["action"] == "member_cancel"]
    assert actions == ["success", "failure"]


def test_my_team_is_scoped_and_uses_the_head_node_read_path(member, env):
    response = member(email="alice@example.com").get(f"/team-quota/member/team?{Q}&start=2026-11-01&end=2026-12-01")
    assert response.status_code == 200
    body = response.json
    assert body["team"] == "teama"
    assert body["quota"]["name"] == "teama" and body["usage"]["name"] == "teama"
    assert body["mine"]["user"] == "alice"
    assert body["mine"]["instance_hours"] == pytest.approx(1.0)
    assert body["mine"]["estimate_cost"] == pytest.approx(1.861, abs=1e-4)
    assert body["quota"]["type_limits"]["g6e.xlarge"] == 2
    assert body["quota"]["running_by_type"]["g6e.xlarge"]["users"] == {} and body["quota"]["members"] == []
    assert "totals" not in body and "editable" not in body
    assert "teamb" not in json.dumps(body) and "carol" not in json.dumps(body)
    assert not [k for k in walk_keys(body) if "billing" in k.lower()]
    assert env["login"].sent == []
    assert all(" -i " not in c for c in env["head"].commands)  # read-only commands


# --- templates, notifications, preferences, budget --------------------------------------------------------------


def test_templates_crud_is_per_user(member, env, mock_csrf_needed, dynamo):
    client = member()
    created = client.post("/team-quota/member/templates?region=us-west-2", json=TEMPLATE)
    assert created.status_code == 201, created.json
    tid = created.json["id"]
    assert created.json["job"]["instance_type"] == "g6e.xlarge"
    assert dynamo.item("USER#carol", f"TPL#{tid}")["name"] == "train 1x"
    listed = client.get("/team-quota/member/templates?region=us-west-2").json
    assert [t["id"] for t in listed["templates"]] == [tid] and listed["max"] == 50
    renamed = client.patch(f"/team-quota/member/templates/{tid}?region=us-west-2", json={"name": "renamed"})
    assert renamed.json["name"] == "renamed"
    other = member(email="dave@example.com")
    assert other.get("/team-quota/member/templates?region=us-west-2").json["templates"] == []
    assert other.delete(f"/team-quota/member/templates/{tid}?region=us-west-2").status_code == 404
    client = member()
    assert client.delete(f"/team-quota/member/templates/{tid}?region=us-west-2").status_code == 200
    assert dynamo.item("USER#carol", f"TPL#{tid}") is None
    actions = [c.kwargs["extra"]["action"] for c in env["audit"].info.call_args_list]
    assert actions.count("member_template_create") == 1 and "member_template_delete" in actions
    assert "secret-body" not in repr(env["audit"].info.call_args_list)


@pytest.mark.parametrize("body", [
    {"name": "x", "job": {"instance_type": "g6e.xlarge", "time_minutes": 30}},          # no script
    {"name": "", "job": TEMPLATE["job"]},
    {"name": "a" * 65, "job": TEMPLATE["job"]},
    {"name": "x", "job": {**TEMPLATE["job"], "billing_limit": 1}},
    {"name": "x", "job": TEMPLATE["job"], "owner": "bob"},
])
def test_invalid_templates_are_400(member, env, mock_csrf_needed, body):
    assert member().post("/team-quota/member/templates?region=us-west-2", json=body).status_code == 400


def test_template_limit_and_bad_ids(member, env, mock_csrf_needed, dynamo):
    for i in range(50):
        dynamo.items[("USER#carol", f"TPL#{i:032x}")] = {"pk": {"S": "USER#carol"}, "sk": {"S": f"TPL#{i:032x}"},
                                                          "name": {"S": f"t{i}"}}
    client = member()
    assert client.post("/team-quota/member/templates?region=us-west-2", json=TEMPLATE).status_code == 409
    assert client.patch("/team-quota/member/templates/abc?region=us-west-2", json={"name": "x"}).status_code == 404
    assert client.delete("/team-quota/member/templates/NOTHEX?region=us-west-2").status_code == 404


def test_notifications_bell_and_read(member, env, mock_csrf_needed, dynamo):
    from api.teams import datastore
    store = datastore.Store({"data_table": "hpc-poc-pcui-data", "region": "us-west-2"})
    store.put({"pk": "USER#carol", "sk": "NOTIF#2026-11-02T10:00:00+00:00#12", "kind": "job", "job_id": "12",
               "state": "COMPLETED", "name": "train"})
    store.put({"pk": "USER#bob", "sk": "NOTIF#2026-11-02T10:00:00+00:00#13", "kind": "job", "job_id": "13"})
    client = member()
    body = client.get("/team-quota/member/notifications?region=us-west-2").json
    assert body["unread"] == 1 and [n["job_id"] for n in body["items"]] == ["12"]
    assert client.post("/team-quota/member/notifications/read?region=us-west-2", json={}).status_code == 200
    assert client.get("/team-quota/member/notifications?region=us-west-2").json["unread"] == 0


def test_preferences_subscribe_with_the_token_email(member, env, mock_csrf_needed, mocker, dynamo):
    sns = FakeSNS()
    mocker.patch("api.teams.notify.sns_client", return_value=sns)
    client = member()
    assert client.get("/team-quota/member/preferences?region=us-west-2").json == {
        "job_notify": False, "email_jobs": False, "email_budget": False, "email_alerts": True,
        "email": {"address": "carol@example.com", "status": "none"}}
    body = client.put("/team-quota/member/preferences?region=us-west-2",
                      json={"job_notify": True, "email_jobs": True}).json
    assert body["email"] == {"address": "carol@example.com", "status": "pending"}
    (sub,) = sns.subscriptions.values()
    assert sub == {"endpoint": "carol@example.com", "protocol": "email", "filter": '{"user":["carol"]}',
                   "pending": True}
    assert sns.calls[0][1]["TopicArn"].endswith(":hpc-poc-portal-notify")
    # a second e-mail preference reuses the subscription; switching both off removes it
    client.put("/team-quota/member/preferences?region=us-west-2", json={"email_budget": True})
    assert len(sns.subscriptions) == 1
    # alert e-mails (on by default) keep the subscription; switching every e-mail preference off removes it
    kept = client.put("/team-quota/member/preferences?region=us-west-2", json={"email_jobs": False,
                                                                               "email_budget": False}).json
    assert kept["email"]["status"] == "pending" and len(sns.subscriptions) == 1
    off = client.put("/team-quota/member/preferences?region=us-west-2", json={"email_alerts": False}).json
    assert off["email"]["status"] == "none" and sns.subscriptions == {} and off["email_alerts"] is False
    assert dynamo.item("USER#carol", "PREFS")["job_notify"] is True
    assert client.put("/team-quota/member/preferences?region=us-west-2",
                      json={"email": "bob@example.com"}).status_code == 400
    actions = [c.kwargs["extra"]["action"] for c in env["audit"].info.call_args_list]
    assert actions.count("member_preferences") == 4


def test_budget_banner(member, env, dynamo):
    from api.teams import datastore
    store = datastore.Store({"data_table": "hpc-poc-pcui-data", "region": "us-west-2"})
    month = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m")
    assert member().get(f"/team-quota/member/budget?{Q}").json == {"team": "teama", "budget": None}
    store.put({"pk": "TEAM#teama", "sk": "BUDGET", "budget_usd": 100})
    store.put({"pk": "TEAM#teama", "sk": f"BUDGETSTATE#{month}", "spent_usd": 85.5, "percent": 85.5,
               "updated_at": "2026-11-02T01:00:00+00:00"})
    banner = member().get(f"/team-quota/member/budget?{Q}").json["budget"]
    assert banner["level"] == "warning" and banner["budget_usd"] == 100 and banner["spent_usd"] == 85.5


# --- job detail: output state, projected end, time limit; alert badges ------------------------------------------


def _scontrol(state, extra="", exists=None, name="train", stdout="/efs/teamA/slurm-12.out"):
    text = (f"@@SCONTROL\nJobId=12 JobName={name} UserId=carol(1) Account=teama Partition=g6e-xlarge "
            f"JobState={state} TimeLimit=UNLIMITED WorkDir=/efs/teamA StdOut={stdout} {extra}\n")
    return (0, text + (f"@@STDOUT_EXISTS {exists}\n" if exists is not None else ""), "")


@pytest.mark.parametrize("state,extra,exists,expected,projected", [
    ("PENDING", "Reason=None StartTime=Unknown", 0, "not_started", True),
    ("CONFIGURING", "StartTime=2026-11-02T10:01:00 RunTime=00:00:10", 0, "not_started", True),
    ("RUNNING", "StartTime=2026-11-02T10:01:00 RunTime=00:05:00", 1, "ok", True),
    ("RUNNING", "StartTime=2026-11-02T10:01:00 RunTime=00:05:00", 0, "missing", True),
    ("COMPLETED", "StartTime=2026-11-02T10:01:00 EndTime=2026-11-02T11:00:00 RunTime=00:59:00", 1, "ok", False),
    ("FAILED", "StartTime=2026-11-02T10:01:00 EndTime=2026-11-02T10:02:00 RunTime=00:01:00", 0, "missing", False),
    ("CANCELLED", "StartTime=2026-11-02T10:01:00 EndTime=2026-11-02T10:01:00 RunTime=00:00:00", 0,
     "never_started", False),
])
def test_job_detail_output_state(member, env, state, extra, exists, expected, projected):
    env["login"].responses["job"] = _scontrol(state, extra, exists)
    body = member().get(f"/team-quota/member/jobs/12?{Q}").json
    assert body["output_state"] == expected and body["end_is_projected"] is projected
    assert body["time_limit"] is None  # UNLIMITED (own quota): no limit, the page shows "-"
    assert "stdout_exists" not in body and "env_deleted" not in body
    if state == "PENDING":
        assert body["reason_text"]  # never empty: Chinese explanation


def test_job_detail_of_a_build_whose_environment_was_deleted(member, env, dynamo):
    from api.teams import datastore
    log = "/efs/envs/teama/cuda/12.5/build.log"
    env["login"].responses["job"] = _scontrol("COMPLETED", "StartTime=2026-11-02T10:01:00 RunTime=00:10:00", 0,
                                              name="env-cuda", stdout=log)
    assert member().get(f"/team-quota/member/jobs/12?{Q}").json["output_state"] == "missing"  # no tombstone
    datastore.Store({"data_table": "hpc-poc-pcui-data", "region": "us-west-2"}).put({
        "pk": "TEAM#teama", "sk": "ENVDEL#cuda#12.5", "by": "dave", "at": "2026-11-03T08:00:00+00:00"})
    body = member().get(f"/team-quota/member/jobs/12?{Q}").json
    assert body["output_state"] == "env_deleted"
    assert body["env_deleted"] == {"by": "dave", "at": "2026-11-03T08:00:00+00:00", "team": "teama",
                                   "name": "cuda", "version": "12.5"}
    # a job whose name does not match the environment of its output directory is not an environment build
    env["login"].responses["job"] = _scontrol("COMPLETED", "StartTime=2026-11-02T10:01:00 RunTime=00:10:00", 0,
                                              name="env-other", stdout=log)
    assert member().get(f"/team-quota/member/jobs/12?{Q}").json["output_state"] == "missing"


def test_jobs_carry_alert_badges_from_the_scheduler_state(member, env, dynamo):
    from api.teams import datastore
    store = datastore.Store({"data_table": "hpc-poc-pcui-data", "region": "us-west-2"})
    store.put({"pk": "USER#carol", "sk": "ALERT#12", "no_capacity": True, "pending_since": "2026-11-02T10:00:00"})
    store.put({"pk": "USER#carol", "sk": "ALERT#13", "idle_alerted": True, "long_run_block": 1})
    store.put({"pk": "USER#bob", "sk": "ALERT#14", "idle_alerted": True})
    env["login"].responses["jobs"] = (0, _gz(
        "12|train|teama|g6e-xlarge|PENDING|None|2026-11-02T10:00:00|N/A|UNLIMITED|0:00|gres/gpu:1|4|16G|teama-g6e-xlarge\n"
        "13|run|teama|g6e-xlarge|RUNNING|None|2026-11-01T10:00:00|2026-11-01T10:01:00|UNLIMITED|1-02:00:00|gres/gpu:1|4|"
        "16G|teama-g6e-xlarge\n"
        "14|x|teama|g6e-xlarge|RUNNING|None|2026-11-01T10:00:00|2026-11-01T10:01:00|UNLIMITED|5:00|gres/gpu:1|4|16G|"
        "teama-g6e-xlarge\n"), "")
    jobs = {j["job_id"]: j for j in member().get(f"/team-quota/member/jobs?{Q}").json["jobs"]}
    assert jobs["12"]["alerts"] == ["no_capacity"]
    assert jobs["13"]["alerts"] == ["idle", "long_run"]
    assert jobs["14"]["alerts"] == []  # another user's state item is never read
