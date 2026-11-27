# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Stuck-job alerts (notify only, never cancel): read on the head node, evaluated here, sent by the scheduler.

* ``gpu_idle``: a running job whose GPUs stayed below ``ALERT_GPU_IDLE_PERCENT`` (5 %) for
  ``ALERT_GPU_IDLE_MINUTES`` (60) on every node. Once per idle episode; a sample at or above the threshold ends
  the episode, so a job that works again and then idles again is notified again.
* ``long_run``: a job that has run ``ALERT_LONG_RUN_HOURS`` (24) hours: once per block (24 h, 48 h ...).
* ``no_capacity``: a job pending for ``ALERT_NO_CAPACITY_HOURS`` (2) hours while EC2 has no capacity for its
  instance type (a node reason contains InsufficientInstanceCapacity). Once per job, with the other instance types
  that still have team quota and capacity.

GPU samples come from the compute nodes: ``/efs/.pcui-gpu/<hostname>`` lines ``<epoch> <max %> <avg %>``, one per
minute (``hpc-gpu-sample`` timer of node-enroot.sh). GPU nodes are exclusive, so a node belongs to one job.
State per job: ``USER#<u> | ALERT#<job>`` (ttl 30 days).
"""
import math
import os
import re
from datetime import datetime, timezone

from api.teams import reasons
from api.teams.slurm import (
    SQUEUE,
    parse_queue,
    parse_sinfo,
    parse_sinfo_ice,
    parse_type_quota_conf,
    queue_cmd,
    queue_usage,
    sinfo_cmd,
    type_quota_read_argv,
)

GPU_IDLE, LONG_RUN, NO_CAPACITY = "gpu_idle", "long_run", "no_capacity"
KINDS = (GPU_IDLE, LONG_RUN, NO_CAPACITY)
STATE_PREFIX = "ALERT#"
STATE_TTL_DAYS = 30
GPU_DIR = "/efs/.pcui-gpu"
MIN_SAMPLE_SHARE = 0.8  # an idle verdict needs samples for at least 80 % of the window minutes on every node
JOB_FORMAT = "%i|%u|%a|%P|%T|%r|%V|%S|%N|%j"
NODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
JOB_ID_RE = re.compile(r"\d{1,10}(_\d{1,6})?")
SLURM_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

# (environment variable, default, minimum, maximum)
SETTINGS = {
    "idle_percent": ("ALERT_GPU_IDLE_PERCENT", 5, 1, 100),
    "idle_minutes": ("ALERT_GPU_IDLE_MINUTES", 60, 15, 240),
    "long_run_hours": ("ALERT_LONG_RUN_HOURS", 24, 1, 720),
    "no_capacity_hours": ("ALERT_NO_CAPACITY_HOURS", 2, 1, 168),
}

# Root on the head node. Prints "map <job> <node>" for every node of every running job, then for each node
# "node <name>" followed by the last $2 lines of its sample file. Symlinks and odd names are skipped.
GPU_SCRIPT = r"""set -u
export PATH=/opt/slurm/bin:/usr/bin:/bin
dir="$1"; lines="$2"
[ -d "$dir" ] && [ ! -L "$dir" ] || exit 0
maps=$(squeue -a -h -t R -o '%i %N' 2>/dev/null | while read -r id nl; do
  [ -n "$nl" ] || continue
  scontrol show hostnames "$nl" 2>/dev/null | while read -r h; do echo "map $id $h"; done
done)
[ -n "$maps" ] || exit 0
printf '%s\n' "$maps"
printf '%s\n' "$maps" | awk 'NF == 3 {print $3}' | sort -u | while read -r h; do
  case "$h" in ''|*[!A-Za-z0-9._-]*) continue ;; esac
  f="$dir/$h"
  [ -f "$f" ] && [ ! -L "$f" ] || continue
  echo "node $h"
  tail -n "$lines" -- "$f" 2>/dev/null || true
done
"""


def config_from_env(environ=None):
    """Thresholds from the scheduler environment; a missing or invalid value falls back to its default."""
    environ = os.environ if environ is None else environ
    cfg = {}
    for key, (name, default, low, high) in SETTINGS.items():
        try:
            value = int(str(environ.get(name, default)).strip())
        except ValueError:
            value = default
        cfg[key] = value if low <= value <= high else default
    return cfg


# --- head-node report -------------------------------------------------------------------------------------------


def jobs_cmd():
    return [SQUEUE, "-a", "-h", "-t", "R,CF,PD", "-o", JOB_FORMAT]


def gpu_argv(lines):
    return ["bash", "-c", GPU_SCRIPT, "pcui-gpu", GPU_DIR, str(int(lines))]


def report_sections(cfg):
    return [("ALERTJOBS", jobs_cmd()), ("QUEUE", queue_cmd()), ("SINFO", sinfo_cmd()),
            ("TYPES", type_quota_read_argv()), ("GPU", gpu_argv(cfg["idle_minutes"] + 5))]


def _time(value):
    value = (value or "").strip()
    if not SLURM_TIME_RE.fullmatch(value):
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)  # head node clocks run in UTC


def parse_jobs(lines):
    jobs = []
    for line in lines:
        cols = line.rstrip("\n").split("|", 9)
        if len(cols) != 10 or not JOB_ID_RE.fullmatch(cols[0].strip()):
            continue
        job_id, user, account, partition, state, reason, submit, start, nodelist, name = (c.strip() for c in cols)
        if not user:
            continue
        jobs.append({"job_id": job_id, "user": user, "account": account, "partition": partition, "state": state,
                     "reason": None if reason in ("", "None") else reason, "submit": _time(submit),
                     "start": _time(start), "nodelist": nodelist, "name": name})
    return jobs


def parse_gpu(lines):
    """({job_id: [node]}, {node: [(epoch, max %, avg %)]}) from the GPU section."""
    job_nodes, samples, current = {}, {}, None
    for line in lines:
        parts = line.split()
        if len(parts) == 3 and parts[0] == "map":
            if JOB_ID_RE.fullmatch(parts[1]) and NODE_RE.fullmatch(parts[2]):
                job_nodes.setdefault(parts[1], []).append(parts[2])
        elif len(parts) == 2 and parts[0] == "node":
            current = parts[1] if NODE_RE.fullmatch(parts[1]) else None
            if current:
                samples.setdefault(current, [])
        elif current and len(parts) == 3 and all(p.isdigit() for p in parts):
            samples[current].append((int(parts[0]), int(parts[1]), int(parts[2])))
    return job_nodes, samples


def parse_report(sections):
    conf = parse_type_quota_conf(sections.get("TYPES", []))
    usage = queue_usage(parse_queue(sections.get("QUEUE", [])))
    job_nodes, samples = parse_gpu(sections.get("GPU", []))
    return {
        "jobs": parse_jobs(sections.get("ALERTJOBS", [])),
        "job_nodes": job_nodes,
        "samples": samples,
        "nodes": parse_sinfo(sections.get("SINFO", [])),
        "ice": parse_sinfo_ice(sections.get("SINFO", [])),
        "types": conf["types"],
        "quota": conf["quota"],
        "running": {team: {p: u.get("running", 0) for p, u in parts.items()} for team, parts in usage.items()},
    }


# --- evaluation (pure) ------------------------------------------------------------------------------------------


def _state_word(state):
    return (state or "").split(" ")[0].upper()


def _team_context(report, team):
    return {"types": report["types"], "limits": report["quota"].get(team, {}),
            "running": report["running"].get(team, {}), "nodes": report["nodes"], "ice": report["ice"]}


def _base_alert(kind, job, report):
    return {"kind": kind, "job_id": job["job_id"], "user": job["user"], "team": job["account"],
            "partition": job["partition"], "instance_type": report["types"].get(job["partition"]),
            "name": job["name"]}


def _idle_verdict(job, report, now, cfg):
    """True (idle for the whole window on every node), False (a sample at or above the threshold) or None (not
    enough data or not running long enough)."""
    nodes = report["job_nodes"].get(job["job_id"])
    if not nodes or job["start"] is None:
        return None
    minutes = cfg["idle_minutes"]
    now_epoch = now.timestamp()
    since = max(now_epoch - minutes * 60, job["start"].timestamp())
    windows = {node: [s for s in report["samples"].get(node, []) if since <= s[0] <= now_epoch + 60]
               for node in nodes}
    if any(s[1] >= cfg["idle_percent"] for window in windows.values() for s in window):
        return False
    if (now - job["start"]).total_seconds() < minutes * 60:
        return None
    need = max(1, math.ceil(minutes * MIN_SAMPLE_SHARE))
    return True if all(len(window) >= need for window in windows.values()) else None


def evaluate(report, now, cfg, state):
    """Alerts to send and state items to write.

    ``state`` = {job_id: {"pending_since", "idle_alerted", "long_run_block", "no_capacity"}} of earlier runs.
    Returns ``(alerts, updates)``: alerts [{kind, job_id, user, team, partition, instance_type, name, hours?,
    minutes?, percent?, alternatives?}] and updates {job_id: new state (with user)} for changed jobs only.
    """
    alerts, updates = [], {}
    for job in report["jobs"]:
        job_id, word = job["job_id"], _state_word(job["state"])
        old = state.get(job_id) or {}
        new = {"user": job["user"], "pending_since": old.get("pending_since"),
               "idle_alerted": bool(old.get("idle_alerted")), "long_run_block": int(old.get("long_run_block") or 0),
               "no_capacity": bool(old.get("no_capacity"))}
        if word in ("RUNNING", "R") and job["start"] is not None:
            elapsed_hours = (now - job["start"]).total_seconds() / 3600
            verdict = _idle_verdict(job, report, now, cfg)
            if verdict is False:
                new["idle_alerted"] = False
            elif verdict is True and not new["idle_alerted"]:
                new["idle_alerted"] = True
                alerts.append({**_base_alert(GPU_IDLE, job, report), "minutes": cfg["idle_minutes"],
                               "percent": cfg["idle_percent"], "hours": round(elapsed_hours, 1)})
            block = int(elapsed_hours // cfg["long_run_hours"])
            if block >= 1 and block > new["long_run_block"]:
                new["long_run_block"] = block
                alerts.append({**_base_alert(LONG_RUN, job, report), "hours": round(elapsed_hours, 1)})
        elif word in ("PENDING", "PD"):
            since = _parse_iso(new["pending_since"])
            if job["submit"] is not None and (since is None or job["submit"] < since):
                since = job["submit"]  # a requeued job may get a new submit time; keep the first one seen
            since = since or now
            new["pending_since"] = since.isoformat()
            waited = (now - since).total_seconds() / 3600
            if (waited >= cfg["no_capacity_hours"] and report["ice"].get(job["partition"])
                    and not new["no_capacity"]):
                new["no_capacity"] = True
                alerts.append({**_base_alert(NO_CAPACITY, job, report), "hours": round(waited, 1),
                               "alternatives": reasons.alternatives(_team_context(report, job["account"]),
                                                                    job["partition"])})
        if any(new[k] != _normalized(old).get(k) for k in ("pending_since", "idle_alerted", "long_run_block",
                                                         "no_capacity")):
            updates[job_id] = new
    return alerts, updates


def _normalized(old):
    return {"pending_since": old.get("pending_since"), "idle_alerted": bool(old.get("idle_alerted")),
            "long_run_block": int(old.get("long_run_block") or 0), "no_capacity": bool(old.get("no_capacity"))}


def _parse_iso(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def active_alerts(item, job_state):
    """Badges of a job in 我的作业 from its state item: ``idle`` (疑似空闲), ``long_run`` (已运行超24小时),
    ``no_capacity`` (暂无容量)."""
    if not item:
        return []
    word = _state_word(job_state)
    result = []
    if word in ("RUNNING", "R"):
        if item.get("idle_alerted"):
            result.append("idle")
        if int(item.get("long_run_block") or 0) > 0:
            result.append("long_run")
    if word in ("PENDING", "PD") and item.get("no_capacity"):
        result.append("no_capacity")
    return result


# --- messages ---------------------------------------------------------------------------------------------------


def _ascii(text):
    return re.sub(r"[^\x20-\x7e]", "?", text)[:100]


def _job_label(alert):
    details = [d for d in (alert.get("name"), alert.get("instance_type") or alert.get("partition")) if d]
    return f"作业 {alert['job_id']}" + (f"（{'，'.join(details)}）" if details else "")


def alert_subject(alert):
    """ASCII subject (SNS rejects other characters in e-mail subjects)."""
    itype = alert.get("instance_type") or alert.get("partition") or "?"
    if alert["kind"] == GPU_IDLE:
        return _ascii(f"HPC job {alert['job_id']}: GPU idle for {alert['minutes']} min")
    if alert["kind"] == LONG_RUN:
        return _ascii(f"HPC job {alert['job_id']}: running for {alert['hours']:g} hours")
    return _ascii(f"HPC job {alert['job_id']}: waiting for {itype} capacity")


def alert_text(alert):
    """One Chinese paragraph for the bell and the e-mail."""
    label = _job_label(alert)
    if alert["kind"] == GPU_IDLE:
        return (f"{label}已运行 {alert['hours']:g} 小时，最近 {alert['minutes']} 分钟 GPU 利用率一直低于 "
                f"{alert['percent']}%，可能已卡住或在空等。系统不会自动取消；如果不再需要，请在门户“我的作业”中一键取消。")
    if alert["kind"] == LONG_RUN:
        return (f"{label}已运行 {alert['hours']:g} 小时。这只是提醒，作业会继续运行；如果不再需要，"
                "请在门户“我的作业”中一键取消。")
    itype = alert.get("instance_type") or alert.get("partition")
    text = (f"{label}已排队 {alert['hours']:g} 小时：{itype} 暂无容量（AWS 目前没有这种机器可用），系统会自动重试，"
            "有容量时作业会自动开始；等待期间不产生费用。")
    if alert.get("alternatives"):
        text += f"也可以取消后改用有配额且有容量的机型：{reasons.format_alternatives(alert['alternatives'])}。"
    else:
        text += "目前没有其他有配额且有容量的机型。"
    return text


def alert_message(alert):
    lines = [alert_text(alert), "", f"作业号：{alert['job_id']}", f"团队：{alert['team']}"]
    if alert.get("instance_type"):
        lines.append(f"机型：{alert['instance_type']}")
    lines.append("一键取消：门户 → 我的作业")
    return "\n".join(lines)
