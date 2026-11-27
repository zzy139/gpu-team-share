import base64
import gzip
import os
import random
import re
import shlex
import stat
import string
import subprocess
import textwrap

import pytest

from api.teams import member_commands as mc
from api.teams.member_exec import MemberCommandError, build_command, check_result
from api.teams.member_exec import slurm_error_message
from api.teams.reasons import REASONS, explain_pending, reason_text

ENTRY = {"instance_type": "g6e.12xlarge", "partition": "g6e-12xlarge", "gpus": 4, "vcpus": 48, "memory_gib": 384.0}
BODY = {"instance_type": "g6e.12xlarge", "nodes": 1, "gpus_per_node": 1, "cpus": 4, "mem_gb": 16,
        "time_minutes": 60, "borrow": False}
UUID = "0123456789abcdef0123456789abcdef"
NASTY = "#!/bin/bash\necho '; rm -rf / #'\necho $(id) `whoami` \"$HOME\"\n\u4e2d\u6587\n"


# --- builders ---------------------------------------------------------------------------------------------------


def test_argv_round_trips_through_the_quoted_command():
    argv = mc.read_argv("/fsx/teamA/slurm-1.out", "tail", 0, 16384, "/fsx/teamA")
    command = build_command("carol", mc.MEMBER_SCRIPT, argv)
    assert shlex.split(command)[10:] == [mc.MEMBER_SCRIPT, "pcui-member", "read", "/fsx/teamA/slurm-1.out", "tail",
                                         "0", "16384", "/fsx/teamA"]


@pytest.mark.parametrize("builder,args", [
    (mc.job_argv, ["1;id"]),
    (mc.job_argv, ["$(id)"]),
    (mc.cancel_argv, ["12 13"]),
    (mc.history_argv, ["2026-11-01;id", "2026-11-02"]),
    (mc.read_argv, ["relative/path", "tail", 0, 10, "/fsx/teamA"]),
    (mc.read_argv, ["/fsx/a\nb", "tail", 0, 10, "/fsx/teamA"]),
    (mc.read_argv, ["/fsx/teamA/x", "head", 0, 10, "/fsx/teamA"]),
    (mc.read_argv, ["/fsx/teamA/x", "tail", 0, 16385, "/fsx/teamA"]),
    (mc.list_argv, ["/fsx/teamA", "/fsx/../etc"]),
])
def test_invalid_builder_input_is_rejected(builder, args):
    with pytest.raises(ValueError):
        builder(*args)


def test_pasted_script_travels_only_as_an_encoded_token():
    token, digest, size = mc.encode_script(NASTY)
    argv = mc.submit_argv("/fsx/teamc", "/fsx/teamc", None, token, UUID, mc.sbatch_args(BODY, "teamc", ENTRY))
    command = build_command("carol", mc.MEMBER_SCRIPT, argv)
    assert "rm -rf" not in command and "$(id)" not in command.replace(mc.MEMBER_SCRIPT, "")
    assert size == len(NASTY.encode()) and len(digest) == 64
    decoded = subprocess.run(["/bin/sh", "-c", "printf '%s' \"$1\" | base64 -d | gzip -dc", "sh", token],
                             capture_output=True, check=True).stdout
    assert decoded == NASTY.encode("utf-8")


def test_script_size_limits():
    with pytest.raises(ValueError):
        mc.encode_script("x" * 65537)
    with pytest.raises(ValueError):
        mc.encode_script("a\x00b")
    rng = random.Random(1)
    incompressible = "".join(rng.choice(string.ascii_letters + string.digits + "+/") for _ in range(65536))
    with pytest.raises(MemberCommandError) as err:
        mc.encode_script(incompressible)
    assert err.value.status == 413
    token, _, _ = mc.encode_script("#!/bin/bash\n" + "echo hello\n" * 5000)
    assert len(token) <= mc.SCRIPT_TOKEN_MAX


def test_sbatch_args():
    assert mc.sbatch_args(BODY, "teamc", ENTRY) == [
        "--account=teamc", "--qos=teamc-g6e-12xlarge", "--partition=g6e-12xlarge", "--nodes=1", "--job-name=pcui-job",
        "--time=60", "--mem=16G", "--cpus-per-task=4", "--gres=gpu:1"]
    whole = mc.sbatch_args({"instance_type": "g6e.12xlarge", "time_minutes": 5}, "teamc", ENTRY)
    assert "--mem=0" in whole and "--gres=gpu:4" in whole and not any(a.startswith("--cpus") for a in whole)
    borrowed = mc.sbatch_args({**BODY, "borrow": True, "gpus_per_node": 0, "job_name": "train.v1"}, "teamc", ENTRY)
    assert "--qos=borrow" in borrowed and "--job-name=train.v1" in borrowed
    assert not any(a.startswith("--gres") for a in borrowed)
    env = {"scope": "shared", "team": "teamb", "name": "af2", "version": "2.3.2"}
    assert mc.sbatch_args({**BODY, "env": env}, "teamc", ENTRY)[-1] == "--comment=env:shared/teamb/af2/2.3.2"
    for bad in ({**BODY, "gpus_per_node": 5}, {**BODY, "cpus": 49}, {**BODY, "mem_gb": 385}):
        with pytest.raises(ValueError):
            mc.sbatch_args(bad, "teamc", ENTRY)
    with pytest.raises(ValueError):
        mc.sbatch_args(BODY, "teamc", None)


def test_sbatch_time_limit_rules():
    # own quota: no --time unless the member gave one (the partition limit applies: UNLIMITED)
    own = mc.sbatch_args({"instance_type": "g6e.12xlarge"}, "teamc", ENTRY)
    assert not any(a.startswith("--time") for a in own)
    assert own == ["--account=teamc", "--qos=teamc-g6e-12xlarge", "--partition=g6e-12xlarge", "--nodes=1",
                   "--job-name=pcui-job", "--mem=0", "--gres=gpu:4"]
    assert "--time=90" in mc.sbatch_args({"instance_type": "g6e.12xlarge", "time_minutes": 90}, "teamc", ENTRY)
    # borrowed: always capped at 240 minutes, never rejected for its time
    for minutes, expected in ((None, "--time=240"), (241, "--time=240"), (10080, "--time=240"), (30, "--time=30")):
        body = {"instance_type": "g6e.12xlarge", "borrow": True}
        if minutes:
            body["time_minutes"] = minutes
        args = mc.sbatch_args(body, "teamc", ENTRY)
        assert "--qos=borrow" in args and [a for a in args if a.startswith("--time")] == [expected]


def test_env_paths_and_comments():
    team_env = {"scope": "team", "team": "teamc", "name": "cuda", "version": "12.4"}
    assert mc.env_path(team_env, "teamc", "/efs/envs") == "/efs/envs/teamc/cuda/12.4"
    # old references (published copies) resolve to the same layout; another team's environment is not rejected
    # here: the login node decides by reading it as the member (only 所有人 environments are readable)
    shared = {**team_env, "scope": "shared", "team": "teamd"}
    assert mc.env_path(shared, "teamc", "/efs/envs") == "/efs/envs/teamd/cuda/12.4"
    assert mc.env_path({**team_env, "team": "teamd"}, "teamc", "/efs/envs") == "/efs/envs/teamd/cuda/12.4"
    for bad in ({**team_env, "name": ".."}, {**team_env, "version": "1/2"}, {**team_env, "scope": "x"},
                {**team_env, "team": "../x"}):
        with pytest.raises(ValueError):
            mc.env_path(bad, "teamc", "/efs/envs")
    with pytest.raises(ValueError):
        mc.env_path(team_env, "teamc", "/efs/../envs")
    assert mc.parse_env_comment(mc.env_comment(team_env)) == team_env
    assert mc.parse_env_comment("env:team/teamc/cuda/12.4;id") is None
    assert mc.parse_env_comment(None) is None


def test_parse_resubmit():
    text = ("@@FIELDS 9|carol|g6e-12xlarge|2|1-00:30:00|borrow|cpu=96,gres/gpu=8,node=2\n@@NAME big run\n"
            "@@COMMENT \n@@WORKDIR /efs/teamc\n@@PATH /efs/teamc/run.sh\n")
    result = mc.parse_resubmit(text, {"g6e-12xlarge": ENTRY})
    assert result["job"] == {"instance_type": "g6e.12xlarge", "nodes": 2, "gpus_per_node": 4,
                             "time_minutes": 240, "borrow": True, "env": None, "workdir": "/efs/teamc",
                             "script_path": "/efs/teamc/run.sh"}  # borrow capped at 4 h, name with a space dropped
    other = mc.parse_resubmit(text.replace("g6e-12xlarge", "mixed"), {"g6e-12xlarge": ENTRY})
    assert other["job"]["instance_type"] is None
    # own quota without a time limit (UNLIMITED): no time_minutes (was a default of 60)
    own = mc.parse_resubmit(text.replace("1-00:30:00|borrow", "UNLIMITED|teamc-g6e-12xlarge"),
                            {"g6e-12xlarge": ENTRY})
    assert "time_minutes" not in own["job"] and own["job"]["borrow"] is False
    timed = mc.parse_resubmit(text.replace("1-00:30:00|borrow", "02:00:00|teamc-g6e-12xlarge"),
                              {"g6e-12xlarge": ENTRY})
    assert timed["job"]["time_minutes"] == 120
    # a borrowed job without a limit is resubmitted with the borrow maximum
    unlimited_borrow = mc.parse_resubmit(text.replace("1-00:30:00", "UNLIMITED"), {"g6e-12xlarge": ENTRY})
    assert unlimited_borrow["job"]["time_minutes"] == 240
    assert mc._time_minutes("30:00") == 30 and mc._time_minutes("02:00:00") == 120
    assert mc._time_minutes("UNLIMITED") is None
    with pytest.raises(MemberCommandError):
        mc.parse_resubmit("", {})


def test_submit_argv_requires_exactly_one_script():
    options = mc.sbatch_args(BODY, "teamc", ENTRY)
    with pytest.raises(ValueError):
        mc.submit_argv("/fsx/teamc", "/fsx/teamc", "/fsx/teamc/a.sh", "abc", UUID, options)
    with pytest.raises(ValueError):
        mc.submit_argv("/fsx/teamc", "/fsx/teamc", None, None, UUID, options)
    with pytest.raises(ValueError):
        mc.submit_argv("/fsx/teamc", "/fsx/teamc", "/fsx/teamc/a.sh", None, "../x", options)
    with pytest.raises(ValueError):
        mc.submit_argv("/fsx/teamc", "/fsx/teamc", "/fsx/teamc/a.sh", None, UUID, ["--wrap=id;reboot"])
    argv = mc.submit_argv("/fsx/teamc", "/fsx/teamc/run", "/fsx/teamc/a.sh", None, UUID, options)
    assert argv[:9] == ["submit", "/fsx/teamc", "/fsx/teamc/run", "path", "/fsx/teamc/a.sh", UUID, "-", "-", "-"]
    with_env = mc.submit_argv("/efs/teamc", "/efs/teamc", "/efs/teamc/a.sh", None, UUID, options,
                              "/efs/envs/teamc/cuda/1", "/efs/shared", "/efs/envs")
    assert with_env[6:9] == ["/efs/envs/teamc/cuda/1", "/efs/shared", "/efs/envs"]
    with pytest.raises(ValueError):
        mc.submit_argv("/efs/teamc", "/efs/teamc", "/efs/teamc/a.sh", None, UUID, options, "/efs/envs/../x",
                       "/efs/shared", "/efs/envs")
    with pytest.raises(ValueError):
        mc.submit_argv("/efs/teamc", "/efs/teamc", "/efs/teamc/a.sh", None, UUID, options, "/efs/envs/teamc/a/1",
                       "/efs/shared", "/efs/../envs")
    with pytest.raises(ValueError):
        mc.submit_argv("/efs/teamc", "/efs/teamc", "/efs/teamc/a.sh", None, UUID, ["--comment=env:../../x"])


# --- parsers ----------------------------------------------------------------------------------------------------


def _gz(text):
    return base64.b64encode(gzip.compress(text.encode())).decode()


def test_parse_squeue_with_reason_text():
    out = _gz("12|train|a|b|teamc|g6|PENDING|QOSGrpNodeLimit|2026-11-02T10:00:00|N/A|1:00:00|0:00|gres/gpu:1|4|16G|teamc-std\n"
              "13|run|teamc|g6|RUNNING|None|2026-11-02T10:00:00|2026-11-02T10:01:00|1:00:00|5:00|gres/gpu:1|4|16G|borrow\n")
    jobs = mc.parse_squeue(out)
    assert jobs[0]["job_id"] == "12" and jobs[0]["name"] == "train|a|b"
    assert jobs[0]["reason"] == "QOSGrpNodeLimit"
    assert jobs[0]["reason_text"] == reason_text("QOSGrpNodeLimit")
    assert jobs[1]["state"] == "RUNNING" and jobs[1]["reason_text"] == "" and jobs[1]["qos"] == "borrow"


def test_parse_history_newest_first():
    out = _gz("10|a|teamc|g6|teamc-std|COMPLETED|None|2026-11-01T00:00:00|2026-11-01T00:01:00|2026-11-01T00:02:00|60|0:0|billing=1|/fsx/teamc\n"
              "11|b|teamc|g6|borrow|FAILED|None|2026-11-02T00:00:00|2026-11-02T00:01:00|2026-11-02T00:02:00|61|1:0|billing=1|/fsx/teamc\n")
    jobs = mc.parse_history(out)
    assert [j["job_id"] for j in jobs] == ["11", "10"]
    assert jobs[0]["elapsed_seconds"] == 61 and jobs[0]["workdir"] == "/fsx/teamc"


def test_truncated_output_is_a_clear_error():
    with pytest.raises(MemberCommandError):
        mc.parse_squeue(_gz("x" * 100)[:-10])


def test_parse_job_detail_scontrol():
    text = ("@@SCONTROL\nJobId=12 JobName=train UserId=carol(17680) GroupId=x(1) Account=teamc QOS=teamc-std "
            "JobState=PENDING Reason=QOSGrpNodeLimit SubmitTime=2026-11-02T10:00:00 StartTime=Unknown "
            "TimeLimit=01:00:00 RunTime=00:00:00 Partition=g6 WorkDir=/fsx/teamc StdOut=/fsx/teamc/slurm-12.out "
            "StdErr=/fsx/teamc/slurm-12.out ReqTRES=cpu=4,gres/gpu=1\n")
    job = mc.parse_job_detail(text + "@@STDOUT_EXISTS 0\n")
    assert (job["job_id"], job["user"], job["state"]) == ("12", "carol", "PENDING")
    assert job["stdout_path"] == "/fsx/teamc/slurm-12.out" and job["start"] is None
    assert job["reason_text"].startswith("团队在这个机型上的配额已用满")
    assert job["time_limit"] == "01:00:00" and job["stdout_exists"] is False
    unlimited = mc.parse_job_detail(text.replace("TimeLimit=01:00:00", "TimeLimit=UNLIMITED")
                                    .replace("Reason=QOSGrpNodeLimit", "Reason=None") + "@@STDOUT_EXISTS 1\n")
    assert unlimited["time_limit"] is None and unlimited["stdout_exists"] is True
    assert unlimited["reason_text"] == "正在等待调度，机器启动约需 5 分钟"  # no reason yet: never empty
    assert mc.parse_job_detail(text)["stdout_exists"] is None


def test_parse_job_detail_sacct_expands_patterns():
    text = "@@SACCT\n12|carol|train|teamc|g6|teamc-std|COMPLETED|None|t|t|t|0:0|billing=1|/fsx/teamc|%x-%j.out|/fsx/teamc/%u-%j.err\n"
    job = mc.parse_job_detail(text)
    assert job["stdout_path"] == "/fsx/teamc/train-12.out"
    assert job["stderr_path"] == "/fsx/teamc/carol-12.err"


def test_parse_read_and_list():
    data = base64.b64encode(b"hello\n").decode()
    read = mc.parse_read(f"@@SIZE 100\n@@OFFSET 94\n@@PATH /fsx/teamc/a.out\n@@DATA\n{data}\n", 16384)
    assert read == {"path": "/fsx/teamc/a.out", "size": 100, "offset": 94, "length": 6, "content_b64": data,
                    "truncated": True, "limit": 16384}
    raw = b"d\t4096\t1700000000.0\tsub\0f\t12\t1700000001.5\tz.out\0f\t1\t1\tpart"
    listing = mc.parse_list(f"@@PATH /fsx/teamc\n@@DATA\n{base64.b64encode(raw).decode()}\n")
    assert [e["name"] for e in listing["entries"]] == ["sub", "z.out"]
    assert listing["truncated"] is True


def test_reason_mapping_is_chinese():
    assert reason_text("QOSGrpNodeLimit").startswith("团队在这个机型上的配额已用满")
    assert reason_text("Resources") == "正在等待空闲机器（机器正在启动或全部在用）"
    assert reason_text("ReqNodeNotAvail,_UnavailableNodes:g6-dy-1") == "作业需要的机器暂时不可用"
    assert reason_text("Nodes required for job are DOWN, DRAINED or reserved for jobs in higher priority partitions") \
        == "作业需要的机器暂时不可用"
    assert reason_text("None") == ""
    assert reason_text("SomethingNew") == "其他原因：SomethingNew"
    assert reason_text("BeginTime").startswith("上次机器启动没有成功，作业已自动重新排队")
    for code, text in REASONS.items():
        assert code == "None" or re.search(r"[\u4e00-\u9fff]", text), code


CONTEXT = {"types": {"g6e-xlarge": "g6e.xlarge", "g6e-2xlarge": "g6e.2xlarge", "g6e-4xlarge": "g6e.4xlarge"},
           "limits": {"g6e-xlarge": 1, "g6e-2xlarge": 2}, "running": {"g6e-xlarge": 0, "g6e-2xlarge": 2},
           "nodes": {"g6e-4xlarge": {"down~": 4}, "g6e-xlarge": {"idle~": 8}}}


def test_explain_pending_suggests_other_types_or_borrow():
    assert explain_pending("QOSGrpNodeLimit", "g6e-2xlarge", "teama-g6e-2xlarge", CONTEXT) == \
        "g6e.2xlarge 团队配额已用满（2/2 台）。可改用 g6e.xlarge（剩余 1 台），或开启借用"
    full = {**CONTEXT, "running": {"g6e-xlarge": 1, "g6e-2xlarge": 2}}
    assert explain_pending("QOSGrpNodeLimit", "g6e-2xlarge", "q", full) == \
        "g6e.2xlarge 团队配额已用满（2/2 台）。请等待正在运行的作业结束，或开启借用"


def test_explain_pending_reports_missing_ec2_capacity():
    text = explain_pending("ReqNodeNotAvail,_UnavailableNodes:g6e-4xlarge-dy-g6e-4xlarge-1", "g6e-4xlarge", "q",
                           CONTEXT)
    # without the reason column (old sinfo format) the node states decide; g6e.xlarge has quota and startable nodes
    assert text == ("g6e.4xlarge 暂无容量：AWS 目前没有这种机器可用，系统会自动重试，有容量时作业会自动开始；"
                    "等待期间不产生费用。也可以取消后改用 g6e.xlarge（剩余 1 台，需启动约5分钟）")
    # nodes that can still be launched: plain text
    assert explain_pending("Resources", "g6e-xlarge", "q", CONTEXT) == reason_text("Resources")
    assert explain_pending("Priority", "g6e-4xlarge", "q", CONTEXT) == reason_text("Priority")
    assert explain_pending("QOSGrpNodeLimit", "build", "q", CONTEXT) == reason_text("QOSGrpNodeLimit")
    assert explain_pending("QOSGrpNodeLimit", "g6e-xlarge", "q", None) == reason_text("QOSGrpNodeLimit")


def test_slurm_error_message_strips_the_sbatch_prefix():
    stderr = ("sbatch: error: Choose an instance type with -p <queue>, for example -p g6e-xlarge (queues: a, b).\n"
              "sbatch: error: Batch job submission failed: Unspecified error\n")
    assert slurm_error_message(stderr) == \
        "Choose an instance type with -p <queue>, for example -p g6e-xlarge (queues: a, b)."
    assert slurm_error_message("sbatch: error: Batch job submission failed: Invalid qos specification\n") == \
        "Batch job submission failed: Invalid qos specification"
    with pytest.raises(MemberCommandError) as err:
        check_result(1, "", stderr)
    assert err.value.message.startswith("Choose an instance type")


# --- MEMBER_SCRIPT path checks, run locally with bash and minimal GNU-compatible shims ---------------------------

SHIMS = {
    "realpath": """
        import os, sys
        args = [a for a in sys.argv[1:] if a not in ("-e", "-m", "--")]
        path = os.path.realpath(args[0])
        sys.exit(1) if "-m" not in sys.argv and not os.path.exists(path) else print(path)
    """,
    "stat": """
        import os, sys
        print(os.path.getsize(sys.argv[-1]))
    """,
    "dd": """
        import sys
        opts = dict(a.split("=", 1) for a in sys.argv[1:])
        with open(opts["if"], "rb") as f:
            f.seek(int(opts["skip"]))
            sys.stdout.buffer.write(f.read(int(opts["count"])))
    """,
    "id": """
        print("carol")
    """,
    "scontrol": """
        import os
        print(os.environ.get("FAKE_SCONTROL", ""))
    """,
    "sacct": """
        import os, sys
        fmt = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--format=")), "")
        print(os.environ.get("FAKE_SACCT_" + fmt.split(",")[0].upper(), os.environ.get("FAKE_SACCT", "")))
    """,
    "gzip": """
        import gzip, sys
        path = [a for a in sys.argv[1:] if not a.startswith("-")]
        data = open(path[0], "rb").read() if path else sys.stdin.buffer.read()
        if any(a.startswith("-") and "d" in a for a in sys.argv[1:]):
            sys.stdout.buffer.write(gzip.decompress(data))
        else:
            sys.stdout.buffer.write(gzip.compress(data))
    """,
    "squeue": """
        import os
        print(os.environ.get("FAKE_OWNER", ""))
    """,
    "scancel": """
        import sys
        print("scancel", sys.argv[-1])
    """,
    "base64": """
        import base64, sys
        data = sys.stdin.buffer.read()
        if "-d" in sys.argv:
            sys.stdout.buffer.write(base64.b64decode(data))
        else:
            sys.stdout.write(base64.b64encode(data).decode())
    """,
    "sbatch": """
        print("77")
    """,
}


@pytest.fixture
def tree(tmp_path):
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in SHIMS.items():
        path = shims / name
        path.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
        path.chmod(0o755)
    root = tmp_path / "fsx"
    for team in ("teamc", "teamc2", "teamd"):
        (root / team).mkdir(parents=True)
    (root / "teamc" / "ok.out").write_text("line1\nline2\n")
    (root / "teamd" / "secret.out").write_text("other team")
    (root / "teamc2" / "x.out").write_text("prefix sibling")
    (root / "teamc" / "link").symlink_to(root / "teamd")
    (root / "teamc" / "link.out").symlink_to(root / "teamd" / "secret.out")
    home = tmp_path / "home"
    home.mkdir()
    script = mc.MEMBER_SCRIPT.replace("export PATH=/opt/slurm/bin:/usr/local/bin:/usr/bin:/bin",
                                      f"export PATH={shims}:/usr/bin:/bin")
    assert script != mc.MEMBER_SCRIPT

    def run(*argv, **extra_env):
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", **extra_env}
        return subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", script, "pcui-member", *argv],
                              capture_output=True, text=True, env=env)

    return {"root": root, "run": run}


def test_read_allows_files_under_the_team_root(tree):
    root = tree["root"]
    result = tree["run"]("read", str(root / "teamc" / "ok.out"), "tail", "0", "6", str(root / "teamc"))
    assert result.returncode == 0, result.stderr
    parsed = mc.parse_read(result.stdout, 6)
    assert base64.b64decode(parsed["content_b64"]) == b"line2\n"
    assert parsed["size"] == 12 and parsed["offset"] == 6


@pytest.mark.parametrize("relative", [
    "teamc/../teamd/secret.out",   # ../ escape
    "teamc/link/secret.out",       # symlinked directory into another team
    "teamc/link.out",              # symlinked file into another team
    "teamc2/x.out",                # prefix sibling of the root
    "teamc/../teamd/missing",      # ../ escape to a path that does not exist (403, not 404)
    "teamc/link/missing",          # symlink escape to a path that does not exist (403, not 404)
])
def test_read_rejects_paths_outside_the_roots(tree, relative):
    root = tree["root"]
    result = tree["run"]("read", f"{root}/{relative}", "tail", "0", "100", str(root / "teamc"))
    assert result.returncode == 4
    assert "other team" not in result.stdout and "prefix sibling" not in result.stdout


def test_read_missing_file_is_5_and_directory_is_4(tree):
    root = tree["root"]
    assert tree["run"]("read", str(root / "teamc" / "nope"), "tail", "0", "1", str(root / "teamc")).returncode == 5
    assert tree["run"]("read", str(root / "teamc"), "tail", "0", "1", str(root / "teamc")).returncode == 4


@pytest.mark.parametrize("relative", ["teamd", "teamc/link", "teamc2", "teamc/..", "teamc/../teamd/missing"])
def test_list_rejects_directories_outside_the_roots(tree, relative):
    root = tree["root"]
    assert tree["run"]("list", f"{root}/{relative}", str(root / "teamc")).returncode == 4


def test_unknown_operation_fails(tree):
    assert tree["run"]("rm", "/").returncode == 2


def test_job_detail_of_another_user_is_6(tree):
    line = "JobId=12 JobName=x UserId=bob(1001) Account=teamc JobState=RUNNING"
    assert tree["run"]("job", "12", FAKE_SCONTROL=line).returncode == 6
    own = tree["run"]("job", "12", FAKE_SCONTROL=line.replace("bob", "carol"))
    assert own.returncode == 0 and own.stdout.startswith("@@SCONTROL")


def test_cancel_only_own_job(tree):
    assert tree["run"]("cancel", "12", FAKE_OWNER="bob").returncode == 6
    assert tree["run"]("cancel", "12", FAKE_OWNER="").returncode == 6
    own = tree["run"]("cancel", "12", FAKE_OWNER="carol")
    assert own.returncode == 0 and "scancel 12" in own.stdout


def _status(result):
    with pytest.raises(MemberCommandError) as err:
        check_result(result.returncode, result.stdout, result.stderr)
    return err.value


def test_foreign_job_hidden_by_private_data_is_403(tree):
    # PrivateData=jobs: scontrol and sacct run as the member return nothing for another user's job.
    result = tree["run"]("job", "12", FAKE_SCONTROL="", FAKE_SACCT="")
    assert result.returncode == 6 and "@@" not in result.stdout
    err = _status(result)
    assert (err.status, err.message) == (403, "作业不存在或不属于您")


def test_foreign_job_in_sacct_is_403(tree):
    result = tree["run"]("job", "12", FAKE_SCONTROL="", FAKE_SACCT="12|bob|x|teamc|g6|normal|COMPLETED")
    assert result.returncode == 6 and _status(result).status == 403


def test_cancel_foreign_job_hidden_by_private_data_is_403(tree):
    result = tree["run"]("cancel", "12", FAKE_OWNER="")
    assert result.returncode == 6 and "scancel" not in result.stdout
    assert _status(result).status == 403


def test_pasted_script_is_private_to_the_member(tree):
    root = tree["root"]
    token = base64.b64encode(gzip.compress(b"#!/bin/bash\ntrue\n")).decode()
    job_uuid = "0" * 32
    result = tree["run"](*mc.submit_argv(str(root / "teamc"), str(root / "teamc"), None, token, job_uuid,
                                         ["--partition=g6e-xlarge"]))
    assert result.returncode == 0, result.stderr
    assert "@@JOB 77" in result.stdout
    script = root / "teamc" / ".pcui-jobs" / "carol" / f"{job_uuid}.sbatch"
    assert script.read_bytes() == b"#!/bin/bash\ntrue\n"
    assert stat.S_IMODE(script.stat().st_mode) == 0o600


def _make_env(path, status="succeeded", kind="container"):
    path.mkdir(parents=True)
    (path / "env.json").write_text('{"kind": "%s", "status": "%s"}' % (kind, status))
    if kind == "container":
        (path / "image.sqsh").write_bytes(b"sqsh")
    else:
        (path / "prefix").mkdir()


USER_SCRIPT = "#!/bin/bash\n#SBATCH --output=out-%j.log\n\n# note\n#SBATCH --gres=gpu:1\necho hi\n#SBATCH --late\n"


@pytest.mark.parametrize("kind", ["container", "conda"])
def test_submit_with_environment_writes_a_wrapper(tree, kind):
    root = tree["root"]
    (root / "shared").mkdir(parents=True)
    _make_env(root / "envs" / "teamc" / "cuda" / "1", kind=kind)
    token = base64.b64encode(gzip.compress(USER_SCRIPT.encode())).decode()
    job_uuid = "1" * 32
    argv = mc.submit_argv(str(root / "teamc"), str(root / "teamc"), None, token, job_uuid, ["--nodes=1"],
                          str(root / "envs" / "teamc" / "cuda" / "1"), str(root / "shared"), str(root / "envs"))
    result = tree["run"](*argv)
    assert result.returncode == 0, result.stderr
    jobs = root.resolve() / "teamc" / ".pcui-jobs" / "carol"
    wrapper = (jobs / f"{job_uuid}.wrap.sbatch").read_text()
    lines = wrapper.splitlines()
    assert lines[:3] == ["#!/bin/bash", "#SBATCH --output=out-%j.log", "#SBATCH --gres=gpu:1"]
    assert "#SBATCH --late" not in wrapper  # directives after the first command are not directives
    script = str(jobs / f"{job_uuid}.sbatch")
    if kind == "container":
        srun = shlex.split(next(line for line in lines if line.startswith("srun ")))  # as bash parses it
        r = root.resolve()
        assert srun == ["srun", f"--container-image={r}/envs/teamc/cuda/1/image.sqsh",
                        f"--container-mounts={r}/teamc:{r}/teamc,{r}/shared:{r}/shared:ro", "--container-mount-home",
                        f"--container-workdir={r}/teamc", "/bin/bash", script]
    else:
        assert f"source {root.resolve()}/shared/tools/hpc-activate {root.resolve()}/envs/teamc/cuda/1/prefix" in wrapper
        assert f"exec /bin/bash {script}" in wrapper
    assert stat.S_IMODE((jobs / f"{job_uuid}.wrap.sbatch").stat().st_mode) == 0o600
    assert f"@@SCRIPT {script}" in result.stdout


@pytest.mark.parametrize("status,where,unreadable,code", [
    ("building", "envs/teamc/cuda/1", None, 9),       # not built yet
    ("failed", "envs/teamc/cuda/1", None, 9),
    ("succeeded", "envs/teamd/cuda/1", "envs/teamd/cuda", 9),  # another team's 仅本团队 environment (3770)
    ("succeeded", "teamc/envs/cuda/1", None, 9),      # the old layout, outside the environment root
    ("succeeded", "teamc/cuda/1", None, 9),           # not under envs/
    ("succeeded", "envs/teamd/cuda/1", None, 0),      # another team's 所有人 environment: readable, accepted
])
def test_submit_rejects_environments_that_are_not_ready_or_elsewhere(tree, status, where, unreadable, code):
    root = tree["root"]
    (root / "shared").mkdir(parents=True)
    (root / "envs").mkdir(parents=True, exist_ok=True)
    _make_env(root / where, status=status)
    token = base64.b64encode(gzip.compress(b"#!/bin/bash\ntrue\n")).decode()
    argv = mc.submit_argv(str(root / "teamc"), str(root / "teamc"), None, token, "2" * 32, [],
                          str(root / where), str(root / "shared"), str(root / "envs"))
    if unreadable:  # what the kernel does for a member of another team (the tests do not run as root)
        os.chmod(root / unreadable, 0)
    try:
        result = tree["run"](*argv)
    finally:
        if unreadable:
            os.chmod(root / unreadable, 0o755)
    assert result.returncode == code, result.stderr
    assert ("@@JOB" in result.stdout) == (code == 0)
    if code:
        assert _status(result).message == "该环境仅对其所属团队可见，或尚未构建成功"


def test_resubmit_returns_own_stored_script_only(tree):
    root = tree["root"].resolve()
    jobs = root / "teamc" / ".pcui-jobs" / "carol"
    jobs.mkdir(parents=True)
    (jobs / ("3" * 32 + ".sbatch")).write_text("#!/bin/bash\necho again\n")
    line = "12|carol|g6e-xlarge|1|01:00:00|teamc-g6e-xlarge|cpu=4,gres/gpu=1,node=1"
    submit_line = f"sbatch --parsable --chdir={root}/teamc {jobs}/{'3' * 32}.wrap.sbatch"
    result = tree["run"]("resubmit", "12", str(root / "teamc"), FAKE_SACCT_JOBID=line, FAKE_SACCT_JOBNAME="train",
                         FAKE_SACCT_COMMENT="env:team/teamc/cuda/1", FAKE_SACCT_WORKDIR=f"{root}/teamc",
                         FAKE_SACCT_SUBMITLINE=submit_line)
    assert result.returncode == 0, result.stderr
    parsed = mc.parse_resubmit(result.stdout, {"g6e-xlarge": {"instance_type": "g6e.xlarge"}})
    assert parsed["job"]["script_content"] == "#!/bin/bash\necho again\n"
    assert parsed["job"]["env"] == {"scope": "team", "team": "teamc", "name": "cuda", "version": "1"}
    foreign = tree["run"]("resubmit", "12", str(root / "teamc"), FAKE_SACCT_JOBID=line.replace("carol", "bob"))
    assert foreign.returncode == 6 and "@@FIELDS" not in foreign.stdout
    outside = tree["run"]("resubmit", "12", str(root / "teamc"), FAKE_SACCT_JOBID=line,
                          FAKE_SACCT_SUBMITLINE=f"sbatch {root}/teamd/secret.out")
    assert "other team" not in outside.stdout and "@@PATH" not in outside.stdout and "@@CONTENT" not in outside.stdout


def test_job_detail_reports_whether_the_output_file_exists(tree):
    root = tree["root"]
    out = root / "teamc" / "slurm-12.out"
    line = f"JobId=12 JobName=x UserId=carol(1001) Account=teamc JobState=RUNNING StdOut={out} WorkDir={root}/teamc"
    missing = tree["run"]("job", "12", FAKE_SCONTROL=line)
    assert missing.returncode == 0, missing.stderr
    assert missing.stdout.splitlines()[-1] == "@@STDOUT_EXISTS 0"
    out.write_text("hello\n")
    present = tree["run"]("job", "12", FAKE_SCONTROL=line)
    assert present.stdout.splitlines()[-1] == "@@STDOUT_EXISTS 1"
    assert mc.parse_job_detail(present.stdout)["stdout_exists"] is True


def test_job_detail_from_sacct_expands_the_output_pattern(tree):
    root = tree["root"]
    (root / "teamc" / "train|v1-12.out").write_text("x")
    # a '|' in the job name; relative %x-%j pattern under the work directory
    row = f"12|carol|train|v1|teamc|g6|q|COMPLETED|None|t|t|t|0:0|billing=1|{root}/teamc|%x-%j.out|%x-%j.err"
    result = tree["run"]("job", "12", FAKE_SCONTROL="", FAKE_SACCT=row)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "@@STDOUT_EXISTS 1"
    job = mc.parse_job_detail(result.stdout)
    assert job["stdout_path"] == f"{root}/teamc/train|v1-12.out" and job["stdout_exists"] is True
    gone = tree["run"]("job", "12", FAKE_SCONTROL="", FAKE_SACCT=row.replace("%x-%j.out", "%u-%%-%j.out"))
    assert gone.stdout.splitlines()[-1] == "@@STDOUT_EXISTS 0"
