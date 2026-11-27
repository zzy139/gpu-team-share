"""Runtime environment catalog: validation, argv safety, CodeBuild calls and the route permission matrix."""
import base64
import gzip
import json
import shlex
import time

import botocore.session
import botocore.validate

import pytest
from marshmallow import ValidationError

from api.teams import envs
from api.teams.member_exec import MemberCommandError, build_command
from api.teams.member_commands import MEMBER_SCRIPT, submit_argv
from api.teams.validation import EnvCreate, EnvVisibility

from .conftest import CONFIG, seed_record
from .test_member_routes import FakeLoginNode, env, member  # noqa: F401 (fixtures)

Q = "cluster_name=hpc-poc&region=us-west-2"
ECR = "123456789012.dkr.ecr.us-west-2.amazonaws.com"
EVIL = "x$(reboot);`id`|rm -rf / && echo 'q' \"dq\" \\ $HOME"


def _gz(value):
    return base64.b64encode(gzip.compress(json.dumps(value).encode())).decode()


def _list_output(*entries):
    return "@@LIST " + _gz({"envs": list(entries), "truncated": False}) + "\n@@RESULT listed\n"


def _entry(name="cuda", version="12.4", status="succeeded", team="teama", owner="carol", visibility="team", **extra):
    item = {"scope": "team", "team": team, "name": name, "version": version, "status": status, "kind": "container",
            "path": f"/efs/envs/{team}/{name}/{version}", "dir_owner": owner, "has_log": True, "size_bytes": 0,
            "visibility": visibility}
    item.update(extra)
    return item


# --- image references -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ref,enroot,private", [
    ("ubuntu:22.04", "docker://ubuntu:22.04", False),
    ("docker.io/library/ubuntu:22.04", "docker://library/ubuntu:22.04", False),
    ("nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04", "docker://nvcr.io#nvidia/cuda:12.4.1-base-ubuntu22.04", False),
    ("public.ecr.aws/docker/library/python:3.11", "docker://public.ecr.aws#docker/library/python:3.11", False),
    ("ghcr.io/org/img@sha256:" + "a" * 64, "docker://ghcr.io#org/img@sha256:" + "a" * 64, False),
    (f"{ECR}/hpc-poc-teama-envs:v1", f"docker://{ECR}#hpc-poc-teama-envs:v1", True),
    (f"{ECR}/hpc-poc-teama-envs", f"docker://{ECR}#hpc-poc-teama-envs:latest", True),
])
def test_allowed_image_references(ref, enroot, private):
    parsed = envs.parse_image_ref(ref, CONFIG, "teama")
    assert parsed["enroot"] == enroot and parsed["private"] is private
    if private:
        assert parsed["image_ref"] == enroot.replace("docker://", "").replace("#", "/")


@pytest.mark.parametrize("ref", [
    "quay.io/org/img:1",                                          # registry outside the allowlist
    "evil.example.com/nvidia/cuda:12",
    "localhost/img",
    "localhost:5000/img",
    "999999999999.dkr.ecr.us-west-2.amazonaws.com/hpc-poc-teama-envs:v1",   # another account
    f"{ECR.replace('us-west-2', 'us-east-1')}/hpc-poc-teama-envs:v1",       # another region
    f"{ECR}/some-other-repo:v1",                                  # not the team repository
    "nvcr.io/nvidia/cuda:12;id",
    "nvcr.io/nvidia/cuda:$(id)",
    "nvcr.io/Nvidia/cuda:1",
    "nvcr.io/nvidia/../cuda:1",
    "nvcr.io#nvidia/cuda:1",
    "img@sha256:1234",
    "x" * 257,
    "",
])
def test_rejected_image_references(ref):
    with pytest.raises(ValueError):
        envs.parse_image_ref(ref, CONFIG, "teama")


def test_other_teams_ecr_repository_is_403():
    with pytest.raises(MemberCommandError) as err:
        envs.parse_image_ref(f"{ECR}/hpc-poc-teamb-envs:v1", CONFIG, "teama")
    assert err.value.status == 403


# --- request validation -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"name": "cuda", "version": "1", "source": "image"},                                   # image missing
    {"name": "cuda", "version": "1", "source": "image", "image": "a b"},
    {"name": "cuda", "version": "1", "source": "image", "image": "x", "dockerfile": "FROM x"},
    {"name": "cuda", "version": "1", "source": "conda"},
    {"name": "cuda", "version": "1", "source": "conda", "environment_yml": "a", "requirements_txt": "b"},
    {"name": "cuda", "version": "1", "source": "conda", "environment_yml": "a", "python": "3.11"},
    {"name": "cuda", "version": "1", "source": "conda", "requirements_txt": "a", "python": "3.11; id"},
    {"name": "cuda", "version": "1", "source": "dockerfile", "dockerfile": "FROM x\0"},
    {"name": "cuda", "version": "1", "source": "dockerfile", "dockerfile": "x" * 65537},
    {"name": "Cuda", "version": "1", "source": "image", "image": "x"},
    {"name": "cuda;id", "version": "1", "source": "image", "image": "x"},
    {"name": "cuda", "version": "../1", "source": "image", "image": "x"},
    {"name": "cuda", "version": "1", "source": "image", "image": "x", "description": "two\nlines"},
    {"name": "cuda", "version": "1", "source": "image", "image": "x", "owner": "bob"},
    {"name": "cuda", "version": "1", "source": "image", "image": "x", "visibility": "public"},
    {"name": "cuda", "version": "1", "source": "image", "image": "x", "visibility": None},
])
def test_invalid_create_bodies(body):
    with pytest.raises(ValidationError):
        EnvCreate.load(body)


def test_valid_create_body():
    body = EnvCreate.load({"name": "sci", "version": "2024.1", "source": "conda", "requirements_txt": "numpy\n",
                           "python": "3.12", "description": EVIL})
    assert body["description"] == EVIL
    assert body["visibility"] == "team"  # 仅本团队 unless 所有人 is chosen
    assert EnvCreate.load({"name": "a", "version": "1", "source": "image", "image": "x",
                           "visibility": "all"})["visibility"] == "all"


@pytest.mark.parametrize("body,ok", [({"visibility": "team"}, True), ({"visibility": "all"}, True),
                                     ({}, False), ({"visibility": "shared"}, False),
                                     ({"visibility": "all", "team": "teamb"}, False)])
def test_visibility_body(body, ok):
    if ok:
        assert EnvVisibility.load(body) == body
    else:
        with pytest.raises(ValidationError):
            EnvVisibility.load(body)


# --- argv and command safety ------------------------------------------------------------------------------------


def _create_argv(body, team="teama", user="carol", cap=0):
    meta, spec_name, spec_text, builder, _ = envs.plan_create(body, CONFIG, team, user, "2026-11-04T00:00:00+00:00")
    return envs.create_argv("/efs/envs", "/efs/shared", team, body["name"], body["version"], meta, spec_name,
                            spec_text, cap, builder)


def test_untrusted_text_travels_only_inside_quoted_tokens():
    body = {"name": "sci", "version": "1", "source": "conda", "environment_yml": f"name: x\n# {EVIL}\n",
            "description": EVIL, "software": EVIL}
    argv = _create_argv(body)
    command = build_command("carol", envs.MEMBER_ENV_SCRIPT, argv)
    assert EVIL not in command and "reboot" not in command
    tokens = shlex.split(command)
    assert tokens[tokens.index("pcui-member") + 1:] == argv
    meta = json.loads(gzip.decompress(base64.b64decode(argv[6])))
    assert meta["description"] == EVIL and meta["owner"] == "carol" and meta["kind"] == "conda"
    assert gzip.decompress(base64.b64decode(argv[8])).decode() == f"name: x\n# {EVIL}\n"
    assert argv[7] == "environment.yml" and argv[-1] == "slurm"


def test_root_command_round_trips():
    for argv in (envs.root_argv("delete", "/efs/envs", "teama", "cuda", "12.4", "carol"),
                 envs.root_argv("prepare", "/efs/envs", "/efs/teamA", "teama", "cuda", "all"),
                 envs.root_argv("visibility", "/efs/envs", "teama", "cuda", "team", None)):
        tokens = shlex.split(envs.root_command(argv))
        assert tokens[:3] == ["python3", "-c", envs.ENV_ROOT_PY] and tokens[3:] == argv
    assert envs.root_argv("delete", "/efs/envs", "teama", "cuda", "12.4", None)[-1] == "-"
    assert envs.root_argv("visibility", "/efs/envs", "teama", "cuda", "all", "carol")[-1] == "carol"


@pytest.mark.parametrize("op,args", [
    ("delete", ("/efs/envs", "teama", "cuda", "12.4", "carol;id")),
    ("delete", ("/efs/envs", "teama", "cuda", "../x", None)),
    ("delete", ("/efs/envs/../teamB", "teama", "cuda", "1", None)),
    ("delete", ("/efs/envs", "Team A", "cuda", "1", None)),
    ("delete", ("/efs/envs", "teama", "cuda", "1", "team", None)),   # old arity (scope)
    ("prepare", ("/efs/envs", "/efs/teamA", "teama", "cuda", "public")),
    ("prepare", ("/efs/envs", "/efs/teamA/../x", "teama", "cuda", "all")),
    ("prepare", ("/efs/envs", "/efs/teamA", "teama", "Cuda", "all")),
    ("visibility", ("/efs/envs", "teama", "cuda", "everyone", None)),
    ("visibility", ("/efs/envs", "teama", "cuda", "all", "$(id)")),
    ("publish", ("/efs/teamA", "/efs/shared", "teama", "cuda", "1", "lead")),
    ("finalize", ("/efs/shared", "teama", "cuda", "1")),
])
def test_root_argv_rejects_invalid_values(op, args):
    with pytest.raises(ValueError):
        envs.root_argv(op, *args)


def test_mark_argv_validates_build_id_and_failure():
    assert envs.mark_argv("/efs/envs", "teama", "cuda", "1", build_id="b:1") == [
        "env-mark", "/efs/envs", "teama", "cuda", "1", "b:1", "-"]
    with pytest.raises(ValueError):
        envs.mark_argv("/efs/envs", "teama", "cuda", "1", build_id="x;id")
    with pytest.raises(ValueError):
        envs.mark_argv("/efs/envs", "teama", "cuda", "1", failure="$(id)")
    with pytest.raises(ValueError):
        envs.mark_argv("/efs/envs", "../teamb", "cuda", "1")


def test_command_size_stays_under_the_ssm_limit():
    # the largest spec token plus the program; the job submit command with its largest token is the reference
    argv = ["env-create", "/efs/envs", "/efs/shared", "teama", "a" * 32, "b" * 32, "M" * 2000, "Dockerfile",
            "A" * envs.ENV_TOKEN_MAX, "0", "codebuild"]
    assert len(build_command("carol", envs.MEMBER_ENV_SCRIPT, argv)) < envs.COMMAND_MAX
    submit = submit_argv("/efs/teamA", "/efs/teamA", None, "A" * 45000, "a" * 32, [])
    assert len(build_command("carol", MEMBER_SCRIPT, submit)) < envs.COMMAND_MAX


def test_oversized_dockerfile_is_413():
    body = {"name": "app", "version": "1", "source": "dockerfile", "dockerfile": "RUN true\n" * 2000}
    with pytest.raises(MemberCommandError) as err:
        _create_argv(body)
    assert err.value.status == 413


def test_member_script_runs_the_program_through_bash(tmp_path):
    import subprocess
    import sys
    shims = tmp_path / "bin"
    shims.mkdir()
    (shims / "python3").symlink_to(sys.executable)
    (tmp_path / "envs" / "teama").mkdir(parents=True)
    assert "umask 022" in envs.MEMBER_ENV_SCRIPT  # versions readable wherever the name directory lets others in
    script = envs.MEMBER_ENV_SCRIPT.replace("export PATH=/opt/slurm/bin:/usr/local/bin:/usr/bin:/bin",
                                            f"export PATH={shims}:/usr/bin:/bin")
    result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", script, "pcui-member", "env-list",
                             str(tmp_path / "envs"), "teama"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    values = envs.markers(result.stdout)
    assert envs.parse_list(values) == {"envs": [], "truncated": False}


# --- CodeBuild --------------------------------------------------------------------------------------------------


def _build(build_id, status, phase, team="teama", name="cuda", version="12.4"):
    return {"id": build_id, "buildStatus": status, "currentPhase": phase, "environment": {"environmentVariables": [
        {"name": "TEAM", "value": team}, {"name": "ENV_NAME", "value": name},
        {"name": "ENV_VERSION", "value": version}]}}


class FakeCodeBuild:
    def __init__(self):
        self.started, self.stopped, self.builds, self.fail = [], [], {}, None

    def start_build(self, **kwargs):
        # Same request validation as boto3, so unsupported parameters fail here as they do live.
        shape = botocore.session.get_session().get_service_model("codebuild").operation_model("StartBuild").input_shape
        report = botocore.validate.ParamValidator().validate(kwargs, shape)
        assert not report.has_errors(), report.generate_report()
        if self.fail:
            raise self.fail
        self.started.append(kwargs)
        return {"build": {"id": "hpc-poc-env-build:b-1"}}

    def batch_get_builds(self, ids):
        return {"builds": [self.builds[i] for i in ids if i in self.builds]}

    def stop_build(self, id):
        self.stopped.append(id)


@pytest.fixture
def codebuild(mocker):
    fake = FakeCodeBuild()
    mocker.patch("api.teams.envs.codebuild_client", return_value=fake)
    return fake


def test_start_build_names_the_team_directory(codebuild):
    build_id = envs.start_build(CONFIG, "teama", "app", "1", "dockerfile", "1001", "2001",
                                dockerfile="FROM x\n")
    assert build_id == "hpc-poc-env-build:b-1"
    call = codebuild.started[0]
    assert call["projectName"] == "hpc-poc-env-build"
    # StartBuild has no file-system override; boto3 rejects unknown parameters (ParamValidationError).
    assert set(call) == {"projectName", "environmentVariablesOverride"}
    variables = {v["name"]: v["value"] for v in call["environmentVariablesOverride"]}
    # the buildspec opens envs/TEAM/ENV_NAME/ENV_VERSION (no TEAM_DIR any more)
    assert variables == {"TEAM": "teama", "ENV_NAME": "app", "ENV_VERSION": "1", "SOURCE": "dockerfile",
                         "OWNER_UID": "1001", "TEAM_GID": "2001",
                         "DOCKERFILE_B64": base64.b64encode(b"FROM x\n").decode()}
    assert envs.efs_subpath("teama") == "/envs/teama"
    with pytest.raises(ValueError):
        envs.efs_subpath("../teama")


def test_build_status_merge(codebuild):
    entries = [_entry(status="building", build_id="b:1"), _entry(version="2", status="queued", build_id="b:2"),
               _entry(version="3", status="succeeded", build_id="b:3"),
               _entry(version="4", status="building", build_id="b:4")]
    codebuild.builds = {"b:1": _build("b:1", "IN_PROGRESS", "BUILD"),
                        "b:2": _build("b:2", "FAILED", "COMPLETED", version="2"),
                        "b:3": _build("b:3", "SUCCEEDED", "COMPLETED", version="3"),
                        # env.json of version 4 points at another team's build: ignored
                        "b:4": _build("b:4", "FAILED", "COMPLETED", team="teamb", version="4")}
    envs.merge_build_status(CONFIG, entries)
    assert entries[0]["status"] == "building" and entries[0]["progress"] == "BUILD"
    assert entries[1]["status"] == "failed" and entries[1]["failure"] == "CodeBuild 构建失败（阶段 COMPLETED）"
    assert "progress" not in entries[2]
    assert entries[3]["status"] == "building" and "progress" not in entries[3]


def test_stop_build_only_stops_the_versions_own_build(codebuild):
    codebuild.builds = {"b:1": _build("b:1", "IN_PROGRESS", "BUILD", team="teamb")}
    envs.stop_build(CONFIG, "b:1", "teama", "cuda", "12.4")
    assert codebuild.stopped == []
    envs.stop_build(CONFIG, "b:1", "teamb", "cuda", "12.4")
    assert codebuild.stopped == ["b:1"]


@pytest.mark.parametrize("total,level", [(0, None), (799_000_000, None), (800_000_000, "warning"),
                                         (1_000_000_000, "exceeded")])
def test_usage_levels(total, level):
    view = envs.usage([{"size_bytes": total}], 1)
    assert view["level"] == level
    assert (envs.usage_warning(view) is None) == (level is None)
    assert envs.usage([{"size_bytes": total}], 0)["level"] is None


# --- routes -----------------------------------------------------------------------------------------------------


class FakeHeadRoot:
    """ssm_command stand-in for the root environment program on the head node."""

    def __init__(self):
        self.calls = []
        self.responses = {"list": _list_output(), "delete": "@@RESULT deleted\n",
                          "prepare": "@@VISIBILITY team\n@@RESULT prepared\n",
                          "visibility": "@@OLD team\n@@VISIBILITY all\n@@RESULT changed\n"}

    def __call__(self, region, instance_id, user, command):
        assert user == "root"
        tokens = shlex.split(command)
        assert tokens[:3] == ["python3", "-c", envs.ENV_ROOT_PY], command[:200]
        self.calls.append(tokens[3:])
        return self.responses[tokens[3]]

    @property
    def ops(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def envx(env, mocker, codebuild):  # noqa: F811
    head = FakeHeadRoot()
    mocker.patch("api.teams.slurm.ssm_command", side_effect=head)
    login = env["login"]
    login.responses.update({
        "env-list": (0, _list_output(_entry(), _entry(version="12.5", status="failed"),
                                     _entry(name="other", owner="dave"),
                                     _entry(team="teamb", name="torch", owner="bob", visibility="all")), ""),
        "env-create": (0, "@@TOTAL 0\n@@DIR /efs/envs/teama/cuda/12.4\n@@JOB 4242\n@@RESULT created\n", ""),
        "env-mark": (0, "@@RESULT marked\n", ""),
        "env-retry": (0, "@@DIR /efs/envs/teama/cuda/12.5\n@@BUILDER slurm\n@@JOB 4243\n@@RESULT retried\n", ""),
    })
    return {**env, "head": head, "codebuild": codebuild}


LEAD = ["team-teama"]
VISIBILITY_URL = f"/team-quota/member/envs/team/teama/cuda/visibility?{Q}"
ENV_ROUTES = [
    ("get", f"/team-quota/member/envs?{Q}", None),
    ("post", f"/team-quota/member/envs?{Q}", {"team": "teama", "name": "cuda", "version": "1", "source": "image",
                                             "image": "ubuntu"}),
    ("post", f"/team-quota/member/envs/team/teama/cuda/12.5/retry?{Q}", {}),
    ("delete", f"/team-quota/member/envs/team/teama/cuda/12.4?{Q}", None),
    ("put", VISIBILITY_URL, {"visibility": "all"}),
]


def _call(client, method, url, body):
    return getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)


def _extra(envx):
    return envx["audit"].info.call_args.kwargs["extra"]


@pytest.mark.parametrize("method,url,body", ENV_ROUTES)
def test_anonymous_env_routes_are_401(client, envx, method, url, body):
    assert _call(client, method, url, body).status_code == 401
    assert envx["login"].sent == [] and envx["head"].calls == []


@pytest.mark.parametrize("method,url,body", ENV_ROUTES)
def test_env_writes_need_csrf(member, envx, method, url, body):  # noqa: F811
    if method == "get":
        return
    assert _call(member(), method, url, body).status_code in (400, 403)
    assert envx["login"].sent == [] and envx["head"].calls == []


@pytest.mark.parametrize("groups", [["viewer"], ["member-teamb"], ["team-teamb"]])
@pytest.mark.parametrize("method,url,body", ENV_ROUTES)
def test_other_teams_get_403(member, envx, mock_csrf_needed, groups, method, url, body):  # noqa: F811
    response = _call(member(groups=groups), method, url + "&team=teama" if method == "get" else url, body)
    assert response.status_code == 403
    assert envx["login"].sent == [] and envx["head"].calls == [] and envx["codebuild"].started == []


def test_member_lists_own_and_public_envs_on_the_login_node(member, envx):  # noqa: F811
    body = member().get(f"/team-quota/member/envs?{Q}").json
    assert envx["login"].ops == ["env-list"] and envx["head"].calls == []
    argv = envx["login"].sent[0]["argv"]
    assert argv[:5] == ["sudo", "-n", "-H", "-u", "carol"]
    assert argv[argv.index("pcui-member") + 1:] == ["env-list", "/efs/envs", "teama"]
    assert "shared" not in body and body["teams"] == ["teama", "teamb"]
    by_id = {e["id"]: e for e in body["envs"]}
    own, failed = by_id["team/teama/cuda/12.4"], by_id["team/teama/cuda/12.5"]
    other, public = by_id["team/teama/other/12.4"], by_id["team/teamb/torch/12.4"]
    assert own["can"] == {"delete": True, "retry": False} and own["can_change_visibility"] is True
    assert own["visibility"] == "team" and own["log_path"] == "/efs/envs/teama/cuda/12.4/build.log"
    assert own["disabled_reasons"] == {"retry": "只有构建失败的版本可以重新构建"}
    assert failed["can"]["retry"] is True and "retry" not in failed["disabled_reasons"]
    # a version of another member: no delete, no visibility change (carol created no version of "other")
    assert other["can"] == {"delete": False, "retry": False} and other["can_change_visibility"] is False
    assert other["disabled_reasons"] == {"delete": "只有这个版本的创建者、团队负责人或管理员可以删除",
                                         "retry": "只有创建者可以重新构建",
                                         "visibility": "只有这个环境的创建者、团队负责人或管理员可以更改可见范围"}
    # another team's 所有人 environment: usable, nothing else
    assert (public["team"], public["visibility"], public["log_path"]) == ("teamb", "all", None)
    assert public["can"] == {"delete": False, "retry": False} and public["can_change_visibility"] is False
    assert set(public["disabled_reasons"].values()) == {envs.OTHER_TEAM_REASON}
    for entry in body["envs"]:
        assert not {"published", "published_at", "published_by", "finalized", "detected"} & set(entry)
        assert not {"publish", "unpublish"} & set(entry["can"])
    assert body["usage"]["total_bytes"] == 0  # the team's own environments only
    assert body["roles"] == {"member": True, "lead": False, "admin": False}


def test_lead_lists_through_the_head_node(member, envx):  # noqa: F811
    envx["head"].responses["list"] = _list_output(_entry(), _entry(name="other", owner="dave"),
                                                  _entry(team="teamb", name="torch", visibility="all"))
    body = member(groups=LEAD, email="lena@example.com").get(f"/team-quota/member/envs?{Q}").json
    assert envx["login"].sent == []
    assert envx["head"].calls == [["list", "/efs/envs", "teama"]]
    own = [e for e in body["envs"] if e["team"] == "teama"]
    assert all(e["can"]["delete"] and e["can_change_visibility"] and not e["can"]["retry"] for e in own)
    public = next(e for e in body["envs"] if e["team"] == "teamb")
    assert not public["can"]["delete"] and not public["can_change_visibility"]
    assert body["roles"] == {"member": False, "lead": True, "admin": False}


def test_list_merges_codebuild_status(member, envx):  # noqa: F811
    envx["login"].responses["env-list"] = (0, _list_output(_entry(status="building", build_id="b:1")), "")
    envx["codebuild"].builds = {"b:1": _build("b:1", "IN_PROGRESS", "PRE_BUILD")}
    body = member().get(f"/team-quota/member/envs?{Q}").json
    assert body["envs"][0]["progress"] == "PRE_BUILD"
    assert envx["head"].calls == []  # nothing to finalize any more


def test_cap_warning_in_the_list(member, envx, parameter_store):  # noqa: F811
    seed_record(parameter_store, "teama", ad_group="teamA", team_path="/efs/teamA", env_storage_cap_gb=10)
    envx["login"].responses["env-list"] = (0, _list_output(
        _entry(size_bytes=8_500_000_000), _entry(team="teamb", name="big", visibility="all", size_bytes=10 ** 12)),
        "")
    body = member().get(f"/team-quota/member/envs?{Q}").json
    assert body["usage"] == {"total_bytes": 8_500_000_000, "cap_gb": 10, "percent": 85.0, "level": "warning"}
    assert "85.0 %" in body["warning"]


def _create(client, body):
    return client.post(f"/team-quota/member/envs?{Q}", json=body)


def _sent_argv(envx, op):
    sent = [s["argv"] for s in envx["login"].sent if s["argv"][s["argv"].index("pcui-member") + 1] == op]
    argv = sent[-1]
    return argv[argv.index("pcui-member") + 1:]


def test_create_public_image_prepares_the_name_then_submits_a_build_job(member, envx, mock_csrf_needed):  # noqa
    response = _create(member(), {"name": "cuda", "version": "12.4", "source": "image",
                                  "image": "nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04", "description": EVIL})
    assert response.status_code == 201, response.json
    assert response.json["env"]["job_id"] == "4242" and response.json["env"]["builder"] == "slurm"
    assert response.json["env"]["visibility"] == "team"
    assert envx["head"].calls == [["prepare", "/efs/envs", "/efs/teamA", "teama", "cuda", "team"]]
    argv = _sent_argv(envx, "env-create")
    assert argv[:6] == ["env-create", "/efs/envs", "/efs/shared", "teama", "cuda", "12.4"]
    meta = json.loads(gzip.decompress(base64.b64decode(argv[6])))
    assert meta["ref"] == "docker://nvcr.io#nvidia/cuda:12.4.1-base-ubuntu22.04" and meta["description"] == EVIL
    assert "published" not in meta
    assert argv[7:] == ["-", "-", "0", "slurm"]
    assert EVIL not in envx["login"].sent[-1]["raw"]
    assert envx["codebuild"].started == []
    extra = _extra(envx)
    assert (extra["action"], extra["result"]) == ("env_create", "success")
    assert extra["params"]["visibility"] == "team" and extra["params"]["visibility_applied"] == "team"


def test_create_for_everyone_prepares_the_name_3775(member, envx, mock_csrf_needed):  # noqa: F811
    envx["head"].responses["prepare"] = "@@VISIBILITY all\n@@RESULT prepared\n"
    response = _create(member(), {"name": "cuda", "version": "12.4", "source": "image", "image": "ubuntu",
                                  "visibility": "all"})
    assert response.status_code == 201, response.json
    assert envx["head"].calls == [["prepare", "/efs/envs", "/efs/teamA", "teama", "cuda", "all"]]
    assert response.json["env"]["visibility"] == "all"


def test_create_reports_the_visibility_an_existing_name_keeps(member, envx, mock_csrf_needed):  # noqa: F811
    envx["head"].responses["prepare"] = "@@VISIBILITY all\n@@RESULT prepared\n"  # the name was 所有人 already
    response = _create(member(), {"name": "cuda", "version": "12.6", "source": "image", "image": "ubuntu"})
    assert response.status_code == 201 and response.json["env"]["visibility"] == "all"


def test_failed_prepare_stops_the_create(member, envx, mock_csrf_needed):  # noqa: F811
    envx["head"].responses["prepare"] = "@@RESULT denied\n"
    response = _create(member(), {"name": "cuda", "version": "1", "source": "image", "image": "ubuntu"})
    assert response.status_code == 403
    assert envx["login"].sent == []
    assert _extra(envx)["result"] == "rejected"


def test_create_from_the_team_repository_uses_codebuild(member, envx, mock_csrf_needed):  # noqa: F811
    envx["login"].responses["env-create"] = (0, "@@TOTAL 0\n@@DIR /efs/envs/teama/app/1\n@@IDS 1001 2001\n"
                                                "@@RESULT created\n", "")
    response = _create(member(), {"name": "app", "version": "1", "source": "image",
                                  "image": f"{ECR}/hpc-poc-teama-envs:v1"})
    assert response.status_code == 201, response.json
    assert response.json["env"]["build_id"] == "hpc-poc-env-build:b-1"
    call = envx["codebuild"].started[0]
    variables = {v["name"]: v["value"] for v in call["environmentVariablesOverride"]}
    assert variables["SOURCE"] == "ecr" and variables["IMAGE_REF"] == f"{ECR}/hpc-poc-teama-envs:v1"
    assert (variables["OWNER_UID"], variables["TEAM_GID"]) == ("1001", "2001")
    assert variables["TEAM"] == "teama" and "TEAM_DIR" not in variables and "fileSystemLocationsOverride" not in call
    assert _sent_argv(envx, "env-mark") == ["env-mark", "/efs/envs", "teama", "app", "1", "hpc-poc-env-build:b-1",
                                            "-"]


def test_codebuild_start_failure_marks_the_version_failed(member, envx, mock_csrf_needed):  # noqa: F811
    from botocore.exceptions import ClientError
    envx["login"].responses["env-create"] = (0, "@@IDS 1001 2001\n@@RESULT created\n", "")
    envx["codebuild"].fail = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "StartBuild")
    response = _create(member(), {"name": "app", "version": "1", "source": "dockerfile", "dockerfile": "FROM x\n"})
    assert response.status_code == 502
    # env.json keeps the ASCII text (argv allowlist); the member sees it in Chinese
    assert _sent_argv(envx, "env-mark")[-1] == "Could not start the CodeBuild build (AccessDeniedException)"
    assert response.json["message"] == "无法启动 CodeBuild 构建（AccessDeniedException）"


def test_known_failure_texts_are_shown_in_chinese():
    assert envs.failure_text("Could not start the CodeBuild build (Throttling)") == "无法启动 CodeBuild 构建（Throttling）"
    assert envs.failure_text("Could not submit the build job: sbatch: error: Invalid qos") == (
        "无法提交构建作业：sbatch: error: Invalid qos")
    assert envs.failure_text("conda solve failed") == "conda solve failed" and envs.failure_text(None) is None
    access = type("A", (), {"team": "teama", "admin": False, "lead": False, "member_user": "carol"})()
    view = envs.entry_view(_entry(status="failed", failure="Could not start the CodeBuild build (error)"), access)
    assert view["failure"] == "无法启动 CodeBuild 构建（error）"


@pytest.mark.parametrize("image,status", [
    (f"{ECR}/hpc-poc-teamb-envs:v1", 403),     # another team's repository
    ("quay.io/org/img:1", 400),                # registry outside the allowlist
    ("evil.example.com/x:1", 400),
])
def test_create_rejects_foreign_images_before_anything_runs(member, envx, mock_csrf_needed, image, status):  # noqa
    response = _create(member(), {"name": "app", "version": "1", "source": "image", "image": image})
    assert response.status_code == status
    assert envx["login"].sent == [] and envx["head"].calls == [] and envx["codebuild"].started == []
    assert _extra(envx)["result"] == "rejected"


def test_create_oversized_dockerfile_is_413(member, envx, mock_csrf_needed):  # noqa: F811
    response = _create(member(), {"name": "app", "version": "1", "source": "dockerfile",
                                  "dockerfile": "RUN true\n" * 2000})
    assert response.status_code == 413
    assert envx["login"].sent == [] and envx["head"].calls == []


def test_create_rejects_an_invalid_visibility(member, envx, mock_csrf_needed):  # noqa: F811
    response = _create(member(), {"name": "app", "version": "1", "source": "image", "image": "ubuntu",
                                  "visibility": "public"})
    assert response.status_code == 400
    assert envx["login"].sent == [] and envx["head"].calls == []


@pytest.mark.parametrize("result,status,message", [
    ("exists", 409, "这个环境版本已存在，请换一个版本号"), ("cap", 409, None), ("denied", 403, None),
    ("unavailable", 503, None), ("not_found", 404, "环境不存在")])
def test_create_results_map_to_http(member, envx, mock_csrf_needed, result, status, message):  # noqa: F811
    envx["login"].responses["env-create"] = (0, f"@@TOTAL 5\n@@RESULT {result}\n", "")
    response = _create(member(), {"name": "cuda", "version": "1", "source": "image", "image": "ubuntu"})
    assert response.status_code == status
    if message:
        assert response.json["message"] == message


def test_create_passes_the_cap_in_bytes(member, envx, mock_csrf_needed, parameter_store):  # noqa: F811
    seed_record(parameter_store, "teama", ad_group="teamA", team_path="/efs/teamA", env_storage_cap_gb=10)
    envx["login"].responses["env-create"] = (0, "@@TOTAL 9000000000\n@@JOB 1\n@@RESULT created\n", "")
    response = _create(member(), {"name": "cuda", "version": "1", "source": "conda", "environment_yml": "x: 1\n"})
    assert response.status_code == 201
    assert _sent_argv(envx, "env-create")[9] == "10000000000"
    assert response.json["usage"]["level"] == "warning" and response.json["warning"]


def test_leads_and_admins_cannot_create(member, envx, mock_csrf_needed):  # noqa: F811
    for groups in (LEAD, ["admin"]):
        response = _create(member(groups=groups), {"name": "cuda", "version": "1", "source": "image",
                                                   "image": "ubuntu", "team": "teama"})
        assert response.status_code == 403
    assert envx["login"].sent == [] and envx["head"].calls == []


def test_member_deletes_own_only_and_leaves_a_tombstone(member, envx, mock_csrf_needed, dynamo):  # noqa: F811
    response = member().delete(f"/team-quota/member/envs/team/teama/cuda/12.4?{Q}")
    assert response.status_code == 200
    assert response.json == {"id": "team/teama/cuda/12.4", "result": "deleted"}
    assert envx["head"].calls == [["delete", "/efs/envs", "teama", "cuda", "12.4", "carol"]]
    tombstone = dynamo.item("TEAM#teama", "ENVDEL#cuda#12.4")
    assert tombstone["by"] == "carol" and tombstone["at"].startswith("20")
    assert abs(tombstone["ttl"] - (time.time() + 400 * 86400)) < 120
    assert _extra(envx)["action"] == "env_delete" and _extra(envx)["params"]["as"] == "owner"
    envx["head"].responses["delete"] = "@@RESULT not_owner\n"
    assert member().delete(f"/team-quota/member/envs/team/teama/other/12.4?{Q}").status_code == 403
    assert dynamo.item("TEAM#teama", "ENVDEL#other#12.4") is None
    assert _extra(envx)["result"] == "rejected"


def test_a_tombstone_failure_does_not_fail_the_delete(member, envx, mock_csrf_needed, dynamo):  # noqa: F811
    from botocore.exceptions import ClientError
    dynamo.fail = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "PutItem")
    assert member().delete(f"/team-quota/member/envs/team/teama/cuda/12.4?{Q}").status_code == 200


def test_lead_deletes_any_version_of_the_team(member, envx, mock_csrf_needed, dynamo):  # noqa: F811
    envx["head"].responses["delete"] = "@@BUILD hpc-poc-env-build:b-9\n@@RESULT deleted\n"
    envx["codebuild"].builds = {"hpc-poc-env-build:b-9": _build("hpc-poc-env-build:b-9", "IN_PROGRESS", "BUILD",
                                                                name="other")}
    response = member(groups=LEAD).delete(f"/team-quota/member/envs/team/teama/other/12.4?{Q}")
    assert response.status_code == 200
    assert envx["head"].calls == [["delete", "/efs/envs", "teama", "other", "12.4", "-"]]
    assert envx["codebuild"].stopped == ["hpc-poc-env-build:b-9"]
    assert dynamo.item("TEAM#teama", "ENVDEL#other#12.4")["by"]


@pytest.mark.parametrize("groups", [["member-teama"], LEAD, ["admin"]])
@pytest.mark.parametrize("method,url", [
    ("delete", f"/team-quota/member/envs/shared/teama/cuda/12.4?{Q}"),       # unpublish removed
    ("post", f"/team-quota/member/envs/shared/teama/cuda/12.4/retry?{Q}"),   # shared copies no longer exist
])
def test_shared_scope_routes_are_404(member, envx, mock_csrf_needed, groups, method, url):  # noqa: F811
    response = _call(member(groups=groups), method, url, {} if method == "post" else None)
    assert response.status_code == 404
    assert envx["head"].calls == [] and envx["login"].sent == []


@pytest.mark.parametrize("groups", [["member-teama"], LEAD, ["admin"]])
def test_publish_route_is_gone(client, member, envx, mock_csrf_needed, groups):  # noqa: F811
    assert not [r.rule for r in client.application.url_map.iter_rules() if "publish" in r.rule]
    caller = member(groups=groups)
    response = caller.post(f"/team-quota/member/envs/team/teama/cuda/12.4/publish?{Q}", json={})
    # the same answer as for any URL without a route: 404, or 405 because the frontend's catch-all GET route
    # (app.py '/<path:path>') matches every path
    control = caller.post(f"/team-quota/member/envs/team/teama/cuda/12.4/no-such-action?{Q}", json={})
    assert response.status_code == control.status_code and response.status_code in (404, 405)
    assert envx["head"].calls == [] and envx["login"].sent == []


def test_invalid_path_values_are_rejected(member, envx, mock_csrf_needed):  # noqa: F811
    for url in ("/team-quota/member/envs/team/teama/cuda;id/1", "/team-quota/member/envs/team/teama/cuda/..%2f..",
                "/team-quota/member/envs/other/teama/cuda/1"):
        assert member().delete(f"{url}?{Q}").status_code in (400, 404, 405)
    for url in ("/team-quota/member/envs/team/teama/cuda;id/visibility", "/team-quota/member/envs/team/Team/cuda/"
                "visibility", "/team-quota/member/envs/shared/teama/cuda/visibility"):
        assert member().put(f"{url}?{Q}", json={"visibility": "all"}).status_code in (400, 403, 404, 405)
    assert envx["head"].calls == []


def test_retry_slurm_build(member, envx, mock_csrf_needed):  # noqa: F811
    response = member().post(f"/team-quota/member/envs/team/teama/cuda/12.5/retry?{Q}", json={})
    assert response.status_code == 202 and response.json["job_id"] == "4243"
    assert _sent_argv(envx, "env-retry") == ["env-retry", "/efs/envs", "/efs/shared", "teama", "cuda", "12.5", "0"]


def test_retry_codebuild_validates_the_stored_image_again(member, envx, mock_csrf_needed):  # noqa: F811
    envx["login"].responses["env-retry"] = (0, "@@BUILDER codebuild\n@@SOURCE image\n"
                                               f"@@IMAGE {ECR}/hpc-poc-teamb-envs:v1\n@@IDS 1 2\n@@RESULT retried\n", "")
    response = member().post(f"/team-quota/member/envs/team/teama/app/1/retry?{Q}", json={})
    assert response.status_code == 409 and envx["codebuild"].started == []
    assert _sent_argv(envx, "env-mark")[-1] == "The stored image reference is not the team repository"
    envx["login"].responses["env-retry"] = (0, "@@BUILDER codebuild\n@@SOURCE image\n"
                                               f"@@IMAGE {ECR}/hpc-poc-teama-envs:v1\n@@IDS 1 2\n@@RESULT retried\n", "")
    response = member().post(f"/team-quota/member/envs/team/teama/app/1/retry?{Q}", json={})
    assert response.status_code == 202 and response.json["build_id"] == "hpc-poc-env-build:b-1"


def test_retry_not_owner_is_403(member, envx, mock_csrf_needed):  # noqa: F811
    envx["login"].responses["env-retry"] = (0, "@@RESULT not_owner\n", "")
    assert member().post(f"/team-quota/member/envs/team/teama/cuda/12.5/retry?{Q}", json={}).status_code == 403


# --- visibility (permission matrix) -----------------------------------------------------------------------------


def _visibility(client, value="all", url=VISIBILITY_URL):
    return client.put(url, json={"visibility": value})


def test_creator_changes_the_visibility(member, envx, mock_csrf_needed):  # noqa: F811
    response = _visibility(member())
    assert response.status_code == 200, response.json
    assert response.json == {"team": "teama", "name": "cuda", "visibility": "all", "old": "team"}
    # the creator check runs as root on the head node: carol must own a version of the name
    assert envx["head"].calls == [["visibility", "/efs/envs", "teama", "cuda", "all", "carol"]]
    assert envx["login"].sent == []
    extra = _extra(envx)
    assert (extra["action"], extra["team"], extra["result"]) == ("env_visibility", "teama", "success")
    assert extra["params"] == {"name": "cuda", "old": "team", "new": "all", "as": "creator"}


def test_other_member_of_the_team_cannot_change_the_visibility(member, envx, mock_csrf_needed):  # noqa: F811
    envx["head"].responses["visibility"] = "@@RESULT not_owner\n"
    response = _visibility(member(email="dave@example.com"))
    assert response.status_code == 403
    assert response.json["message"] == "只有这个环境的创建者、团队负责人或管理员可以更改可见范围"
    assert envx["head"].calls == [["visibility", "/efs/envs", "teama", "cuda", "all", "dave"]]
    extra = _extra(envx)
    assert (extra["action"], extra["result"]) == ("env_visibility", "rejected")
    assert extra["params"] == {"name": "cuda", "new": "all", "as": "creator"}


@pytest.mark.parametrize("groups,role", [(LEAD, "lead"), (["admin"], "admin"),
                                         (["member-teama", "team-teama"], "lead")])
def test_lead_and_admin_change_the_visibility(member, envx, mock_csrf_needed, groups, role):  # noqa: F811
    envx["head"].responses["visibility"] = "@@OLD all\n@@VISIBILITY team\n@@RESULT changed\n"
    response = _visibility(member(groups=groups, email="lena@example.com"), "team")
    assert response.status_code == 200, response.json
    assert response.json["visibility"] == "team" and response.json["old"] == "all"
    assert envx["head"].calls == [["visibility", "/efs/envs", "teama", "cuda", "team", "-"]]  # no owner check
    assert _extra(envx)["params"] == {"name": "cuda", "old": "all", "new": "team", "as": role}


@pytest.mark.parametrize("groups", [["member-teamb"], ["team-teamb"], ["viewer"]])
def test_other_teams_cannot_change_the_visibility(member, envx, mock_csrf_needed, groups):  # noqa: F811
    assert _visibility(member(groups=groups, email="bob@example.com")).status_code == 403
    assert envx["head"].calls == []


@pytest.mark.parametrize("body", [{}, {"visibility": "public"}, {"visibility": "all", "owner": "carol"}])
def test_invalid_visibility_bodies_are_400(member, envx, mock_csrf_needed, body):  # noqa: F811
    assert member().put(VISIBILITY_URL, json=body).status_code == 400
    assert envx["head"].calls == []


def test_visibility_of_a_missing_name_is_404(member, envx, mock_csrf_needed):  # noqa: F811
    envx["head"].responses["visibility"] = "@@RESULT not_found\n"
    response = _visibility(member(groups=LEAD))
    assert response.status_code == 404 and response.json["message"] == "环境不存在"
