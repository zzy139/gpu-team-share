# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Scheduled tasks of the HPC portal: Lambda ``<project>-pcui-scheduler`` (the PCUI image with the command
``awslambda.scheduler.handler``), started by EventBridge with
``{"task": "jobs" | "budgets" | "login_health" | "job_alerts"}``.

* ``jobs`` (every 5 minutes): job ends since the watermark (``sacct`` on the head node) become bell notifications
  and, if the user asked for it, an e-mail through the portal topic (message attribute ``user``). Each job is
  notified once (marker item per job). Members offboarded in ``finish`` mode lose their Slurm association once
  their last job ended.
* ``budgets`` (daily): month-to-date estimated cost per team (machines x hours x on-demand price) against the
  team budget; the first crossing of 80 % and 100 % per month notifies the team members and the admin topic.
* ``job_alerts`` (every 5 minutes): GPU idle, long-running and no-capacity alerts of running and pending jobs
  (``api.teams.job_alerts``) become bell notifications of the submitter and, with the ``email_alerts`` preference
  (default on) and an e-mail subscription, an e-mail through the portal topic. Never cancels a job.
* ``login_health`` (every 5 minutes): metric ``HPC/LoginNodesHealthy{ClusterName}`` = running login nodes whose
  EC2 status checks pass.

Environment: ``PCUI_TEAMS_CONFIG_PARAMETER``, ``PCUI_SCHEDULER_CLUSTER``, ``SSM_LOG_GROUP_NAME`` and the alert
thresholds ``ALERT_GPU_IDLE_PERCENT``, ``ALERT_GPU_IDLE_MINUTES``, ``ALERT_LONG_RUN_HOURS``,
``ALERT_NO_CAPACITY_HOURS``.
E-mail subjects are ASCII (SNS rejects other characters in e-mail subjects); bodies are Chinese.
The head node is found by its ParallelCluster tags; commands are built from validated argv tokens only.
"""
import base64
import binascii
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import boto3

from api.logging.logger import DefaultLogger
from api.pcm_globals import _logger_ctxvar
from api.teams import costs, datastore, job_alerts, lifecycle_store, notify, pricing
from api.teams.cluster import instance_type_for_job, parse_compute_resources
from api.teams.member_commands import parse_env_comment
from api.teams.slurm import (
    SACCT,
    assoc_cmd,
    join_commands,
    jobs_cmd,
    parse_assoc,
    parse_sacct_jobs,
    parse_sections,
    parse_type_quota_conf,
    quote_argv,
    remove_member_cmd,
    type_quota_read_argv,
    user_jobs_cmd,
)
from api.validation.validators import is_alphanumeric_with_hyphen

CLUSTER_ENV = "PCUI_SCHEDULER_CLUSTER"
END_STATES = "CD,F,TO,CA,OOM,NF,BF,PR"
JOB_FORMAT = "--format=JobID,User,Account,State,End,ExitCode,Partition,JobName,Comment"
FIRST_RUN_LOOKBACK = timedelta(minutes=15)
OVERLAP = timedelta(minutes=2)
MAX_LOOKBACK = timedelta(days=2)
JOB_MARKER_TTL_DAYS = 30
BUDGET_ALERT_TTL_DAYS = 400
METRIC_NAMESPACE, METRIC_NAME = "HPC", "LoginNodesHealthy"
CLUSTER_CONFIG_PATH = "/opt/parallelcluster/shared/cluster-config.yaml"
INSTANCE_ID_RE = re.compile(r"i-[0-9a-f]{8,17}")
JOB_ID_RE = re.compile(r"\d{1,10}(_\d{1,6})?")
SLURM_TIME = "%Y-%m-%dT%H:%M:%S"
STATE_WORDS = {"COMPLETED": "completed", "FAILED": "failed", "TIMEOUT": "timed out", "CANCELLED": "was cancelled",
               "OUT_OF_MEMORY": "ran out of memory", "NODE_FAIL": "failed (node failure)",
               "BOOT_FAIL": "failed (boot failure)", "PREEMPTED": "was preempted"}
STATE_WORDS_ZH = {"COMPLETED": "已完成", "FAILED": "失败", "TIMEOUT": "超时被结束", "CANCELLED": "已取消",
                  "OUT_OF_MEMORY": "内存不足被结束", "NODE_FAIL": "失败（节点故障）", "BOOT_FAIL": "失败（机器启动失败）",
                  "PREEMPTED": "被抢占"}


def _init_logging():
    _logger_ctxvar.set(DefaultLogger(False))
    logging.getLogger().setLevel(logging.INFO)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def cluster_name():
    name = os.environ.get(CLUSTER_ENV, "")
    if not name or len(name) > 60 or not is_alphanumeric_with_hyphen(name):
        raise ValueError(f"{CLUSTER_ENV} is not a valid cluster name")
    return name


# --- head node --------------------------------------------------------------------------------------------------


def ec2_client(region):
    return boto3.client("ec2", region_name=region)


def _instances(region, cluster, node_type):
    ids, kwargs = [], {"Filters": [
        {"Name": "tag:parallelcluster:cluster-name", "Values": [cluster]},
        {"Name": "tag:parallelcluster:node-type", "Values": [node_type]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ]}
    ec2 = ec2_client(region)
    for _ in range(10):
        resp = ec2.describe_instances(**kwargs)
        for reservation in resp.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                if INSTANCE_ID_RE.fullmatch(instance.get("InstanceId", "")):
                    ids.append(instance["InstanceId"])
        if not resp.get("NextToken"):
            break
        kwargs["NextToken"] = resp["NextToken"]
    return sorted(ids)


def ssm_run(region, instance_id, command):
    from api.PclusterApiHandler import ssm_command

    return ssm_command(region, instance_id, "root", command)


class HeadNode:
    def __init__(self, region, cluster):
        ids = _instances(region, cluster, "HeadNode")
        if not ids:
            raise RuntimeError(f"No running head node for cluster {cluster}")
        self.region, self.instance_id = region, ids[0]

    def sections(self, sections):
        return parse_sections(ssm_run(self.region, self.instance_id, join_commands(sections)))

    def write(self, argv):
        return ssm_run(self.region, self.instance_id, quote_argv(argv))


# --- jobs -------------------------------------------------------------------------------------------------------


def job_ends_cmd(since):
    return [SACCT, "-a", "-X", "-n", "-P", "-S", since.strftime(SLURM_TIME), "-E", "now", "-s", END_STATES,
            JOB_FORMAT]


def parse_job_ends(lines):
    jobs = []
    for line in lines:
        cols = line.rstrip("\n").split("|", 7)
        if len(cols) != 8 or not JOB_ID_RE.fullmatch(cols[0].strip()):
            continue
        job_id, user, account, state, end, exit_code, partition, rest = cols
        name, _, comment = rest.rpartition("|") if "|" in rest else (rest, "", "")
        if not user or not end or end in ("Unknown", "None"):
            continue
        jobs.append({"job_id": job_id.strip(), "user": user.strip(), "account": account.strip(),
                     "state": state.split(" ")[0].strip(), "end": end.strip(), "exit_code": exit_code.strip(),
                     "partition": partition.strip(), "name": name, "env": parse_env_comment(comment)})
    return jobs


def ascii_subject(text):
    """SNS e-mail subjects must be ASCII: other characters (e.g. a Chinese job name) become '?'."""
    return re.sub(r"[^\x20-\x7e]", "?", text)[:100]


def job_message(job, instance_type):
    verb = STATE_WORDS.get(job["state"], job["state"].lower())
    word = STATE_WORDS_ZH.get(job["state"], job["state"])
    subject = ascii_subject(f"Job {job['job_id']} ({job['name'] or 'job'}) {verb}")
    lines = [f"您的作业 {job['job_id']}（{job['name'] or 'job'}）{word}，结束时间 {job['end']} UTC。",
             f"团队：{job['account']}", f"队列：{job['partition']}"]
    if instance_type:
        lines.append(f"机型：{instance_type}")
    if job.get("env"):
        env = job["env"]
        lines.append(f"环境：{env['team']}/{env['name']} {env['version']}")
    lines.append(f"退出码：{job['exit_code']}")
    lines.append("请打开 HPC 门户查看输出。")
    return subject, "\n".join(lines)


class Prefs:
    """Per-run cache of member preferences (GetItem only: the scheduler role has no BatchGetItem)."""

    def __init__(self, store):
        self.store, self.cache = store, {}

    def get(self, user):
        if user not in self.cache:
            self.cache[user] = notify.get_prefs(self.store, user)
        return self.cache[user]


def notify_job_ends(config, store, jobs, types, now):
    prefs, result = Prefs(store), {"notified": 0, "emailed": 0, "duplicates": 0}
    for job in jobs:
        p = prefs.get(job["user"])
        if not (p["job_notify"] or p["email_jobs"]):
            continue
        marker = {"pk": datastore.user_pk(job["user"]), "sk": f"JOBSEEN#{job['job_id']}",
                  "ttl": datastore.ttl_after(JOB_MARKER_TTL_DAYS, now.timestamp())}
        if not store.put(marker, if_absent=True):
            result["duplicates"] += 1
            continue
        instance_type = types.get(job["partition"])
        if p["job_notify"]:
            store.put({
                "pk": datastore.user_pk(job["user"]), "sk": f"NOTIF#{now.isoformat()}#{job['job_id']}",
                "kind": "job", "job_id": job["job_id"], "name": job["name"], "state": job["state"],
                "exit_code": job["exit_code"], "partition": job["partition"], "instance_type": instance_type,
                "env": job["env"], "end": job["end"], "team": job["account"],
                "ttl": datastore.ttl_after(datastore.NOTIF_TTL_DAYS, now.timestamp()),
            })
            result["notified"] += 1
        if p["email_jobs"]:
            subject, message = job_message(job, instance_type)
            notify.publish(config, config["portal_topic_arn"], subject, message,
                           {"user": job["user"], "kind": "job"})
            result["emailed"] += 1
    return result


def finish_offboarding(head, store, cluster, items, outputs):
    """Remove the Slurm association of members offboarded in ``finish`` mode once they have no job left."""
    finished = []
    for index, item in enumerate(items):
        if outputs.get(f"OFFB{index}"):
            continue  # jobs still queued or running
        head.write(remove_member_cmd(item["team"], item["user"]))
        done = {**item, "state": "offboarded", "finished_at": _now().isoformat()}
        store.put({**done, "pk": datastore.user_pk(item["user"]), "sk": datastore.offboard_sk(item["team"])})
        store.put({**done, "pk": datastore.SYS_PK, "sk": datastore.offboard_index_sk(item["team"], item["user"])})
        finished.append(f"{item['team']}/{item['user']}")
    return finished


def run_jobs(config, cluster, now=None):
    now = now or _now()
    store = datastore.store(config)
    mark = store.get(datastore.SYS_PK, datastore.WATERMARK_SK)
    since = now - FIRST_RUN_LOOKBACK
    if mark and mark.get("until"):
        since = max(datetime.fromisoformat(mark["until"]) - OVERLAP, now - MAX_LOOKBACK)
    finishing = [item for item in store.query(datastore.SYS_PK, prefix="OFFBOARD#")
                 if item.get("state") == "finishing" and item.get("cluster", cluster) == cluster]
    head = HeadNode(config["region"], cluster)
    sections = [("ENDS", job_ends_cmd(since.replace(tzinfo=None))), ("TYPES", type_quota_read_argv())]
    sections += [(f"OFFB{i}", user_jobs_cmd(item["team"], item["user"])) for i, item in enumerate(finishing)]
    out = head.sections(sections)
    types = parse_type_quota_conf(out.get("TYPES", []))["types"]
    result = notify_job_ends(config, store, parse_job_ends(out.get("ENDS", [])), types, now)
    result["offboarded"] = finish_offboarding(head, store, cluster, finishing, out)
    store.put({"pk": datastore.SYS_PK, "sk": datastore.WATERMARK_SK, "until": now.isoformat()})
    return {"task": "jobs", **result}


# --- budgets ----------------------------------------------------------------------------------------------------


def budget_message(team, month, percent, spent, budget, threshold):
    level = "exceeded" if threshold >= notify.EXCEEDED_PERCENT else f"reached {threshold} % of"
    subject = ascii_subject(f"Team {team} has {level} its {month} budget")
    state = "已超出" if threshold >= notify.EXCEEDED_PERCENT else f"已达到 {threshold}%"
    message = (f"团队 {team} {month} 的预估计算费用为 {spent:.2f} USD，占月度预算 {budget:.2f} USD 的 "
               f"{percent:.1f} %（{state}）。\n预估费用 = 机器数 × 小时数 × 该机型的按需价格；"
               "实际账单费用约一天后在 Cost Explorer 中显示。")
    return subject, message


def _compute_resources(lines):
    """{(queue, compute resource): [instance types]} from the base64 cluster configuration, {} when unreadable."""
    try:
        return parse_compute_resources(base64.b64decode("".join(lines), validate=True).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError, AttributeError) as err:
        logging.getLogger().warning("cluster configuration unreadable: %s", type(err).__name__)
        return {}


def run_budgets(config, cluster, now=None):
    now = now or _now()
    store = datastore.store(config)
    month = f"{now:%Y-%m}"
    budgets = {}
    for record in lifecycle_store.list_records(config):
        if record["state"] != "active":
            continue
        item = store.get(datastore.team_pk(record["team"]), datastore.BUDGET_SK)
        if item and item.get("budget_usd"):
            budgets[record["team"]] = float(item["budget_usd"])
    if not budgets:
        return {"task": "budgets", "teams": 0, "alerts": []}
    start = now.date().replace(day=1)
    end = now.date() + timedelta(days=1)
    head = HeadNode(config["region"], cluster)
    out = head.sections([("JOBS", jobs_cmd(start.isoformat(), end.isoformat())), ("TYPES", type_quota_read_argv()),
                         ("ASSOC", assoc_cmd()), ("CONFIG", ["base64", "-w0", CLUSTER_CONFIG_PATH])])
    types = parse_type_quota_conf(out.get("TYPES", []))["types"]
    # Same pricing as the Team usage page: every queue counts, not only the quota-controlled GPU queues
    # (e.g. the build queue), so the banner and the usage page show the same month-to-date estimate.
    mapping = _compute_resources(out.get("CONFIG", []))
    all_types = set(types.values()) | {t for ts in mapping.values() for t in ts or []}
    prices = pricing.instance_prices(all_types, config["region"]) if all_types else {}

    def type_of(job):
        return types.get(job.get("partition")) or instance_type_for_job(job.get("partition"), job.get("nodelist"),
                                                                        mapping)
    estimates = costs.estimate(parse_sacct_jobs(out.get("JOBS", [])), type_of, prices, start, end,
                               now=now.replace(tzinfo=None))
    teams = parse_assoc(out.get("ASSOC", []))
    prefs, alerts = Prefs(store), []
    for team, budget in sorted(budgets.items()):
        spent = estimates["teams"].get(team, {}).get("estimate_cost", 0.0)
        percent = round(spent / budget * 100, 1) if budget > 0 else 0.0
        store.put({"pk": datastore.team_pk(team), "sk": datastore.budget_state_sk(month), "spent_usd": spent,
                   "budget_usd": budget, "percent": percent, "updated_at": now.isoformat(),
                   "ttl": datastore.ttl_after(BUDGET_ALERT_TTL_DAYS, now.timestamp())})
        for threshold in notify.BUDGET_THRESHOLDS:
            if percent < threshold:
                continue
            marker = {"pk": datastore.team_pk(team), "sk": datastore.budget_alert_sk(month, threshold),
                      "at": now.isoformat(), "ttl": datastore.ttl_after(BUDGET_ALERT_TTL_DAYS, now.timestamp())}
            if not store.put(marker, if_absent=True):
                continue  # this threshold was already notified this month
            subject, message = budget_message(team, month, percent, spent, budget, threshold)
            for member in (teams.get(team) or {}).get("members", []):
                user = member["user"]
                store.put({"pk": datastore.user_pk(user), "sk": f"NOTIF#{now.isoformat()}#budget-{team}-{threshold}",
                           "kind": "budget", "team": team, "state": f"{threshold}",
                           "name": subject, "ttl": datastore.ttl_after(datastore.NOTIF_TTL_DAYS, now.timestamp())})
                if prefs.get(user)["email_budget"]:
                    notify.publish(config, config["portal_topic_arn"], subject, message,
                                   {"user": user, "kind": "budget", "team": team})
            notify.publish(config, config["admin_topic_arn"], subject, message, {"kind": "budget", "team": team})
            alerts.append({"team": team, "threshold": threshold, "percent": percent})
    return {"task": "budgets", "teams": len(budgets), "alerts": alerts}


# --- login node health ------------------------------------------------------------------------------------------


def cloudwatch_client(region):
    return boto3.client("cloudwatch", region_name=region)


def run_login_health(config, cluster, now=None):
    region = config["region"]
    ids = _instances(region, cluster, "LoginNode")
    healthy = 0
    if ids:
        resp = ec2_client(region).describe_instance_status(InstanceIds=ids)
        for status in resp.get("InstanceStatuses", []):
            if (status.get("InstanceStatus") or {}).get("Status") == "ok" and \
                    (status.get("SystemStatus") or {}).get("Status") == "ok":
                healthy += 1
    cloudwatch_client(region).put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=[{
        "MetricName": METRIC_NAME, "Dimensions": [{"Name": "ClusterName", "Value": cluster}],
        "Value": healthy, "Unit": "Count",
    }])
    return {"task": "login_health", "running": len(ids), "healthy": healthy}


# --- job alerts -------------------------------------------------------------------------------------------------


def _alert_state(store, users):
    """{job_id: state item} of the users' ALERT# items (one Query per user; the role has no BatchGetItem)."""
    state = {}
    for user in sorted(users):
        for item in store.query(datastore.user_pk(user), prefix=job_alerts.STATE_PREFIX):
            state[item["sk"][len(job_alerts.STATE_PREFIX):]] = item
    return state


def run_job_alerts(config, cluster, now=None):
    now = now or _now()
    cfg = job_alerts.config_from_env()
    store = datastore.store(config)
    head = HeadNode(config["region"], cluster)
    report = job_alerts.parse_report(head.sections(job_alerts.report_sections(cfg)))
    state = _alert_state(store, {job["user"] for job in report["jobs"]})
    alerts, updates = job_alerts.evaluate(report, now, cfg, state)
    ttl = datastore.ttl_after(job_alerts.STATE_TTL_DAYS, now.timestamp())
    by_job = {}
    for alert in alerts:
        by_job.setdefault(alert["job_id"], []).append(alert)
    prefs, emailed, failed, sent = Prefs(store), 0, [], []
    # Per job: deliver its alerts first, then save its "already alerted" state. A bell item that cannot be written
    # leaves the state unsaved, so the next run sends that job's alerts again; an e-mail that cannot be sent is
    # logged and the bell item stays (no repeated bell items every run while e-mail is broken).
    for job_id in sorted(set(updates) | set(by_job)):
        try:
            for alert in by_job.get(job_id, []):
                store.put({
                    "pk": datastore.user_pk(alert["user"]),
                    "sk": f"NOTIF#{now.isoformat()}#{alert['job_id']}-{alert['kind']}",
                    "kind": alert["kind"], "job_id": alert["job_id"], "name": alert["name"],
                    "partition": alert["partition"], "instance_type": alert["instance_type"], "team": alert["team"],
                    "hours": alert.get("hours"), "minutes": alert.get("minutes"),
                    "alternatives": alert.get("alternatives"), "text": job_alerts.alert_text(alert),
                    "ttl": datastore.ttl_after(datastore.NOTIF_TTL_DAYS, now.timestamp()),
                })
                sent.append(alert)
                p = prefs.get(alert["user"])
                if p["email_alerts"] and p.get("subscription_arn"):
                    try:
                        notify.publish(config, config["portal_topic_arn"], job_alerts.alert_subject(alert),
                                       job_alerts.alert_message(alert), {"user": alert["user"], "kind": alert["kind"]})
                        emailed += 1
                    except Exception:  # pylint: disable=broad-except
                        logging.getLogger().exception("job alert e-mail failed for job %s (%s)", job_id, alert["kind"])
                        failed.append({"job_id": job_id, "kind": alert["kind"], "step": "email"})
            if job_id in updates:
                item = updates[job_id]
                store.put({"pk": datastore.user_pk(item["user"]), "sk": f"{job_alerts.STATE_PREFIX}{job_id}",
                           **{k: v for k, v in item.items() if k != "user"}, "updated_at": now.isoformat(),
                           "ttl": ttl})
        except Exception:  # pylint: disable=broad-except
            logging.getLogger().exception("job alerts for job %s not delivered; retried on the next run", job_id)
            failed.append({"job_id": job_id, "step": "portal"})
    return {"task": "job_alerts", "jobs": len(report["jobs"]), "emailed": emailed, "failed": failed,
            "alerts": [{"kind": a["kind"], "job_id": a["job_id"], "user": a["user"]} for a in sent]}


TASKS = {"jobs": run_jobs, "budgets": run_budgets, "login_health": run_login_health, "job_alerts": run_job_alerts}


def handler(event, context=None):
    _init_logging()
    task = (event or {}).get("task")
    if task not in TASKS:
        raise ValueError(f"Unknown task {task!r}")
    cluster = cluster_name()
    if task == "login_health":
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        config = lifecycle_store.load_config() or {"region": region}
        return run_login_health(config, cluster)
    config = lifecycle_store.load_config()
    if config is None:
        return {"task": task, "skipped": "team lifecycle not configured"}
    if cluster not in config["clusters"]:
        raise ValueError(f"Cluster {cluster} is not managed by the team lifecycle")
    result = TASKS[task](config, cluster)
    logging.getLogger().info("scheduler %s", result)
    return result
