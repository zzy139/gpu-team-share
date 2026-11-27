"""Stuck-job alerts: head-node report parsing, the GPU report program and the pure evaluation rules."""
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from api.teams import job_alerts as ja
from api.teams.slurm import parse_sections

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
CFG = {"idle_percent": 5, "idle_minutes": 60, "long_run_hours": 24, "no_capacity_hours": 2}
ICE = "(Code:InsufficientInstanceCapacity)Temporarily disabling node due to insufficient capacity"


def _job(job_id, state, user="carol", partition="g6-xlarge", start=None, submit=None, name="train"):
    return {"job_id": job_id, "user": user, "account": "teama", "partition": partition, "state": state,
            "reason": None, "submit": submit or NOW - timedelta(hours=30), "start": start, "nodelist": "",
            "name": name}


def _samples(minutes, util, end=NOW, step=60):
    """One sample per minute for the last ``minutes`` minutes (oldest first)."""
    last = int(end.timestamp())
    return [(last - i * step, util, util) for i in reversed(range(minutes))]


def _report(jobs, job_nodes=None, samples=None, ice=None, quota=None, running=None, nodes=None):
    return {"jobs": jobs, "job_nodes": job_nodes or {}, "samples": samples or {},
            "nodes": nodes or {"g6-xlarge": {"idle~": 4}, "g6e-2xlarge": {"down~": 8}, "g6e-xlarge": {"idle~": 8}},
            "ice": ice if ice is not None else {"g6-xlarge": False, "g6e-2xlarge": True, "g6e-xlarge": False},
            "types": {"g6-xlarge": "g6.xlarge", "g6e-2xlarge": "g6e.2xlarge", "g6e-xlarge": "g6e.xlarge"},
            "quota": quota if quota is not None else {"teama": {"g6-xlarge": 2, "g6e-2xlarge": 1, "g6e-xlarge": 1}},
            "running": running if running is not None else {"teama": {"g6-xlarge": 1}}}


def _run(report, state, now=NOW, cfg=CFG):
    alerts, updates = ja.evaluate(report, now, cfg, state)
    for job_id, item in updates.items():
        state[job_id] = {k: v for k, v in item.items() if k != "user"}
    return alerts, updates


# --- GPU idle ---------------------------------------------------------------------------------------------------


def test_gpu_idle_is_notified_once_per_episode_and_resets_on_activity():
    job = _job("40", "RUNNING", start=NOW - timedelta(hours=2))
    state = {}
    report = _report([job], {"40": ["g6-xlarge-dy-g6-xlarge-1"]}, {"g6-xlarge-dy-g6-xlarge-1": _samples(65, 0)})
    alerts, updates = _run(report, state)
    assert [(a["kind"], a["job_id"], a["user"], a["instance_type"]) for a in alerts] == [
        ("gpu_idle", "40", "carol", "g6.xlarge")]
    assert alerts[0]["minutes"] == 60 and alerts[0]["percent"] == 5 and alerts[0]["hours"] == 2.0
    assert updates["40"]["idle_alerted"] is True and updates["40"]["user"] == "carol"
    # still idle 5 minutes later: same episode, nothing new, nothing written
    later = NOW + timedelta(minutes=5)
    report["samples"]["g6-xlarge-dy-g6-xlarge-1"] = _samples(65, 0, end=later)
    assert _run(report, state, now=later) == ([], {})
    # the GPU works again (one sample >= 5 %): the episode ends, no alert
    busy = later + timedelta(minutes=5)
    report["samples"]["g6-xlarge-dy-g6-xlarge-1"] = _samples(64, 0, end=busy - timedelta(minutes=1)) + \
        [(int(busy.timestamp()), 80, 40)]
    alerts, updates = _run(report, state, now=busy)
    assert alerts == [] and updates["40"]["idle_alerted"] is False
    # idle again for a whole window: a new episode, notified again
    again = busy + timedelta(minutes=61)
    report["samples"]["g6-xlarge-dy-g6-xlarge-1"] = _samples(60, 2, end=again)
    alerts, _ = _run(report, state, now=again)
    assert [a["kind"] for a in alerts] == ["gpu_idle"]


@pytest.mark.parametrize("case", ["few_samples", "short_job", "one_node_busy", "no_samples", "threshold"])
def test_gpu_idle_needs_a_full_idle_window_on_every_node(case):
    nodes = ["n1", "n2"]
    start = NOW - timedelta(hours=3)
    samples = {"n1": _samples(60, 0), "n2": _samples(60, 0)}
    if case == "few_samples":
        samples["n2"] = _samples(40, 0)            # < 80 % of the 60 window minutes
    elif case == "short_job":
        start = NOW - timedelta(minutes=50)        # samples before the job started do not count
    elif case == "one_node_busy":
        samples["n2"][10] = (samples["n2"][10][0], 30, 10)
    elif case == "no_samples":
        samples = {}
    elif case == "threshold":
        samples["n1"][-1] = (samples["n1"][-1][0], 5, 5)  # 5 % is not below 5 %
    report = _report([_job("41", "RUNNING", start=start)], {"41": nodes}, samples)
    alerts, _ = ja.evaluate(report, NOW, CFG, {})
    assert alerts == []


def test_gpu_idle_threshold_and_window_come_from_the_configuration():
    cfg = {**CFG, "idle_percent": 20, "idle_minutes": 15}
    report = _report([_job("42", "RUNNING", start=NOW - timedelta(minutes=20))], {"42": ["n1"]},
                     {"n1": _samples(15, 10)})
    alerts, _ = ja.evaluate(report, NOW, cfg, {})
    assert [(a["kind"], a["minutes"], a["percent"]) for a in alerts] == [("gpu_idle", 15, 20)]
    assert ja.evaluate(report, NOW, CFG, {})[0] == []  # 10 % is not idle at the default 5 %


# --- long-running reminder --------------------------------------------------------------------------------------


def test_long_run_reminder_once_per_24_hour_block():
    job = _job("50", "RUNNING", start=NOW - timedelta(hours=23, minutes=55))
    state = {}
    assert _run(_report([job]), state) == ([], {})
    job["start"] = NOW - timedelta(hours=24, minutes=1)
    alerts, updates = _run(_report([job]), state)
    assert [(a["kind"], a["hours"]) for a in alerts] == [("long_run", 24.0)]
    assert updates["50"]["long_run_block"] == 1
    job["start"] = NOW - timedelta(hours=47)
    assert _run(_report([job]), state)[0] == []          # same block
    job["start"] = NOW - timedelta(hours=48, minutes=2)
    assert [a["hours"] for a in _run(_report([job]), state)[0]] == [48.0]
    assert state["50"]["long_run_block"] == 2


# --- no capacity ------------------------------------------------------------------------------------------------


def test_no_capacity_alert_after_two_hours_once_with_alternatives():
    job = _job("26", "PENDING", user="dave", partition="g6e-2xlarge", submit=NOW - timedelta(hours=1, minutes=59))
    state = {}
    alerts, updates = _run(_report([job]), state)
    assert alerts == [] and updates["26"]["pending_since"] == job["submit"].isoformat()
    later = NOW + timedelta(minutes=5)
    alerts, updates = _run(_report([job]), state, now=later)
    assert [(a["kind"], a["user"], a["instance_type"]) for a in alerts] == [("no_capacity", "dave", "g6e.2xlarge")]
    assert alerts[0]["hours"] == 2.1
    # g6.xlarge: quota left and startable; g6e.xlarge: quota left and startable (no ICE)
    assert alerts[0]["alternatives"] == [
        {"instance_type": "g6.xlarge", "partition": "g6-xlarge", "remaining": 1, "status": "startable"},
        {"instance_type": "g6e.xlarge", "partition": "g6e-xlarge", "remaining": 1, "status": "startable"}]
    assert updates["26"]["no_capacity"] is True
    assert _run(_report([job]), state, now=later + timedelta(hours=5)) == ([], {})  # once per job


def test_no_capacity_counts_from_the_first_time_pending_even_after_a_requeue():
    # a requeued job may show a new submit time; the wait counts from the first time it was seen pending
    state = {"26": {"pending_since": (NOW - timedelta(hours=3)).isoformat()}}
    job = _job("26", "PENDING", partition="g6e-2xlarge", submit=NOW - timedelta(minutes=10))
    alerts, _ = ja.evaluate(_report([job]), NOW, CFG, state)
    assert [(a["kind"], a["hours"]) for a in alerts] == [("no_capacity", 3.0)]


def test_no_capacity_needs_the_ice_reason_and_lists_no_type_without_quota_or_capacity():
    job = _job("27", "PENDING", partition="g6e-2xlarge", submit=NOW - timedelta(hours=5))
    assert ja.evaluate(_report([job], ice={"g6e-2xlarge": False}), NOW, CFG, {})[0] == []  # just queued
    full = _report([job], running={"teama": {"g6-xlarge": 2, "g6e-xlarge": 1}})
    (alert,), _ = ja.evaluate(full, NOW, CFG, {})
    assert alert["alternatives"] == []
    assert "目前没有其他有配额且有容量的机型" in ja.alert_text(alert)


# --- messages, configuration, badges ----------------------------------------------------------------------------


def test_messages_have_ascii_subjects_and_chinese_bodies():
    base = {"job_id": "26", "user": "dave", "team": "teama", "partition": "g6e-2xlarge",
            "instance_type": "g6e.2xlarge", "name": "训练"}
    idle = {**base, "kind": "gpu_idle", "minutes": 60, "percent": 5, "hours": 2.0}
    long_run = {**base, "kind": "long_run", "hours": 24.0}
    no_cap = {**base, "kind": "no_capacity", "hours": 17.2, "alternatives": [
        {"instance_type": "g6.xlarge", "partition": "g6-xlarge", "remaining": 1, "status": "available"}]}
    assert ja.alert_subject(idle) == "HPC job 26: GPU idle for 60 min"
    assert ja.alert_subject(long_run) == "HPC job 26: running for 24 hours"
    assert ja.alert_subject(no_cap) == "HPC job 26: waiting for g6e.2xlarge capacity"
    assert "最近 60 分钟 GPU 利用率一直低于 5%" in ja.alert_text(idle) and "系统不会自动取消" in ja.alert_text(idle)
    assert "已运行 24 小时" in ja.alert_text(long_run)
    text = ja.alert_text(no_cap)
    assert "已排队 17.2 小时" in text and "系统会自动重试" in text and "等待期间不产生费用" in text
    assert "g6.xlarge（剩余 1 台，有空闲）" in text
    message = ja.alert_message(no_cap)
    assert "作业号：26" in message and "一键取消：门户 → 我的作业" in message


def test_thresholds_from_the_environment():
    assert ja.config_from_env({}) == CFG
    env = {"ALERT_GPU_IDLE_PERCENT": "10", "ALERT_GPU_IDLE_MINUTES": "30", "ALERT_LONG_RUN_HOURS": "12",
           "ALERT_NO_CAPACITY_HOURS": "x"}
    assert ja.config_from_env(env) == {"idle_percent": 10, "idle_minutes": 30, "long_run_hours": 12,
                                       "no_capacity_hours": 2}
    assert ja.config_from_env({"ALERT_GPU_IDLE_MINUTES": "1000", "ALERT_GPU_IDLE_PERCENT": "0"}) == CFG


def test_active_alerts_follow_the_job_state():
    item = {"idle_alerted": True, "long_run_block": 1, "no_capacity": True}
    assert ja.active_alerts(item, "RUNNING") == ["idle", "long_run"]
    assert ja.active_alerts(item, "PENDING") == ["no_capacity"]
    assert ja.active_alerts(item, "COMPLETED") == []
    assert ja.active_alerts(None, "RUNNING") == []
    assert ja.active_alerts({"idle_alerted": False}, "RUNNING") == []


# --- head-node report -------------------------------------------------------------------------------------------


def test_parse_report():
    sections = parse_sections("\n".join([
        "@@ALERTJOBS",
        "40|carol|teama|g6-xlarge|RUNNING|None|2026-11-20T09:00:00|2026-11-20T09:05:00|g6-xlarge-dy-g6-xlarge-1|a|b",
        "26|dave|teama|g6e-2xlarge|PENDING|BeginTime|2026-11-19T19:00:00|N/A||train",
        "bad line",
        "@@QUEUE",
        "teama|carol|g6-xlarge|1|teama-g6-xlarge|RUNNING",
        "@@SINFO",
        f"g6e-2xlarge|down~|8|{ICE}",
        "g6-xlarge|allocated|1|none",
        "@@TYPES",
        "quota teama g6-xlarge 2", "type g6-xlarge g6.xlarge", "type g6e-2xlarge g6e.2xlarge",
        "@@GPU",
        "map 40 g6-xlarge-dy-g6-xlarge-1", "map 40 bad;name", "node g6-xlarge-dy-g6-xlarge-1",
        "1763629200 0 0", "1763629260 3 1", "garbage",
    ]))
    report = ja.parse_report(sections)
    jobs = {j["job_id"]: j for j in report["jobs"]}
    assert set(jobs) == {"40", "26"} and jobs["40"]["name"] == "a|b"
    assert jobs["40"]["start"] == datetime(2026, 11, 20, 9, 5, tzinfo=timezone.utc)
    assert jobs["26"]["start"] is None and jobs["26"]["reason"] == "BeginTime"
    assert report["job_nodes"] == {"40": ["g6-xlarge-dy-g6-xlarge-1"]}
    assert report["samples"] == {"g6-xlarge-dy-g6-xlarge-1": [(1763629200, 0, 0), (1763629260, 3, 1)]}
    assert report["ice"] == {"g6e-2xlarge": True, "g6-xlarge": False}
    assert report["quota"] == {"teama": {"g6-xlarge": 2}} and report["running"] == {"teama": {"g6-xlarge": 1}}
    assert report["types"]["g6e-2xlarge"] == "g6e.2xlarge"


def test_report_sections_are_read_only_constants():
    sections = dict(ja.report_sections(CFG))
    assert sections["ALERTJOBS"] == ["/opt/slurm/bin/squeue", "-a", "-h", "-t", "R,CF,PD", "-o",
                                     "%i|%u|%a|%P|%T|%r|%V|%S|%N|%j"]
    assert sections["SINFO"][-1] == "%R|%T|%D|%E"
    assert sections["GPU"] == ["bash", "-c", ja.GPU_SCRIPT, "pcui-gpu", "/efs/.pcui-gpu", "65"]


SHIMS = {
    "squeue": 'printf "40 g6-dy-[1-2]\\n41 g6-dy-3\\n"',
    "scontrol": 'case "$3" in "g6-dy-[1-2]") printf "g6-dy-1\\ng6-dy-2\\n" ;; *) echo "$3" ;; esac',
}


def test_gpu_program_maps_nodes_and_tails_sample_files(tmp_path):
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in SHIMS.items():
        (shims / name).write_text("#!/bin/bash\n" + body + "\n")
        (shims / name).chmod(0o755)
    gpu = tmp_path / "gpu"
    gpu.mkdir()
    (gpu / "g6-dy-1").write_text("".join(f"{1763629200 + 60 * i} 0 0\n" for i in range(10)))
    (gpu / "g6-dy-2").write_text("1763629200 50 25\n")
    (tmp_path / "secret").write_text("1 2 3\n")
    (gpu / "g6-dy-3").symlink_to(tmp_path / "secret")  # symlinks are never followed
    script = ja.GPU_SCRIPT.replace("export PATH=/opt/slurm/bin:/usr/bin:/bin", f"export PATH={shims}:/usr/bin:/bin")
    assert script != ja.GPU_SCRIPT
    result = subprocess.run(["/bin/bash", "-c", script, "pcui-gpu", str(gpu), "3"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    job_nodes, samples = ja.parse_gpu(result.stdout.splitlines())
    assert job_nodes == {"40": ["g6-dy-1", "g6-dy-2"], "41": ["g6-dy-3"]}
    assert samples == {"g6-dy-1": [(1763629200 + 60 * i, 0, 0) for i in (7, 8, 9)],
                       "g6-dy-2": [(1763629200, 50, 25)]}
    # no sample directory: nothing, no error
    missing = subprocess.run(["/bin/bash", "-c", script, "pcui-gpu", str(tmp_path / "none"), "3"],
                             capture_output=True, text=True)
    assert missing.returncode == 0 and missing.stdout == ""

