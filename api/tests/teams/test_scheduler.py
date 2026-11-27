"""Scheduler Lambda: job-end notifications, budget alerts, finish-mode offboarding and login-node health."""
import json
import re
from datetime import datetime, timezone

import pytest

from api.teams import datastore, lifecycle_store
from awslambda import scheduler

from . import slurm_outputs as out
from .conftest import CONFIG, seed_record
from .fakes import HEAD_NODE, FakeSNS

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
ENDS = """\
31|carol|teama|COMPLETED|2026-11-20T11:58:00|0:0|g6e-xlarge|train|env:team/teama/cuda/12.4
32|carol|teama|FAILED|2026-11-20T11:59:00|1:0|g6e-2xlarge|bad|name|
33|bob|teamb|CANCELLED by 0|2026-11-20T11:59:30|0:15|g6e-12xlarge|x|
34|dave|teama|COMPLETED|Unknown|0:0|g6e-xlarge|y|"""


class FakeHead:
    def __init__(self):
        self.commands = []
        self.outputs = {"ENDS": ENDS, "TYPES": out.TYPE_QUOTA_CONF, "JOBS": out.SACCT_JOBS, "ASSOC": out.ASSOC}
        self.user_jobs = {}

    def __call__(self, region, instance_id, command):
        assert (region, instance_id) == ("us-west-2", HEAD_NODE)
        self.commands.append(command)
        lines = []
        for name in re.findall(r"echo @@([A-Za-z0-9_-]+)", command):
            lines.append(f"@@{name}")
            if name.startswith("OFFB"):
                lines.extend(self.user_jobs.get(int(name[4:]), []))
            else:
                lines.extend(self.outputs.get(name, "").splitlines())
        return "\n".join(lines)


class FakeEC2:
    def __init__(self):
        self.statuses = []

    def describe_instances(self, Filters, **kwargs):
        node_type = next(f["Values"][0] for f in Filters if f["Name"] == "tag:parallelcluster:node-type")
        assert next(f["Values"] for f in Filters if f["Name"] == "tag:parallelcluster:cluster-name") == ["hpc-poc"]
        ids = [HEAD_NODE] if node_type == "HeadNode" else ["i-0aaaaaaaaaaaaaaa1", "i-0aaaaaaaaaaaaaaa2"]
        return {"Reservations": [{"Instances": [{"InstanceId": i} for i in ids]}]}

    def describe_instance_status(self, InstanceIds):
        return {"InstanceStatuses": [
            {"InstanceId": InstanceIds[0], "InstanceStatus": {"Status": "ok"}, "SystemStatus": {"Status": "ok"}},
            {"InstanceId": InstanceIds[1], "InstanceStatus": {"Status": "impaired"}, "SystemStatus": {"Status": "ok"}},
        ]}


@pytest.fixture
def env(mocker, configured, parameter_store, dynamo):
    head = FakeHead()
    mocker.patch("awslambda.scheduler.ssm_run", side_effect=head)
    mocker.patch("awslambda.scheduler.ec2_client", return_value=FakeEC2())
    sns = FakeSNS()
    mocker.patch("api.teams.notify.sns_client", return_value=sns)
    store = datastore.store(CONFIG)
    return {"head": head, "sns": sns, "store": store, "dynamo": dynamo, "ssm": parameter_store}


def _prefs(store, user, **prefs):
    store.put({"pk": f"USER#{user}", "sk": "PREFS", **prefs})


# --- jobs -------------------------------------------------------------------------------------------------------


def test_job_ends_notify_opted_in_users_once(env):
    store, sns = env["store"], env["sns"]
    _prefs(store, "carol", job_notify=True, email_jobs=True)
    _prefs(store, "bob", job_notify=True, email_jobs=False)
    result = scheduler.run_jobs(CONFIG, "hpc-poc", now=NOW)
    assert (result["notified"], result["emailed"], result["duplicates"]) == (3, 2, 0)
    bell = env["dynamo"].with_prefix("USER#carol", "NOTIF#")
    assert [(n["job_id"], n["state"], n["instance_type"]) for n in bell] == [
        ("31", "COMPLETED", "g6e.xlarge"), ("32", "FAILED", "g6e.2xlarge")]
    assert bell[0]["env"] == {"scope": "team", "team": "teama", "name": "cuda", "version": "12.4"}
    assert bell[1]["name"] == "bad|name"
    assert env["dynamo"].with_prefix("USER#bob", "NOTIF#")[0]["state"] == "CANCELLED"
    assert [p["MessageAttributes"]["user"]["StringValue"] for p in sns.published] == ["carol", "carol"]
    assert all(p["TopicArn"] == CONFIG["portal_topic_arn"] for p in sns.published)
    assert "机型：g6e.xlarge" in sns.published[0]["Message"] and "已完成" in sns.published[0]["Message"]
    assert all(p["Subject"].isascii() for p in sns.published)
    assert env["dynamo"].item("SYS", "JOBWATERMARK")["until"] == NOW.isoformat()
    command = env["head"].commands[0]
    assert "-S 2026-11-20T11:45:00 -E now -s CD,F,TO,CA,OOM,NF,BF,PR" in command  # first run: 15 min back

    # the next run overlaps the previous window by 2 minutes; nothing is notified twice
    again = scheduler.run_jobs(CONFIG, "hpc-poc", now=NOW.replace(minute=5))
    assert (again["notified"], again["emailed"], again["duplicates"]) == (0, 0, 3)
    assert len(sns.published) == 2
    assert "-S 2026-11-20T11:58:00 " in env["head"].commands[1]


def test_job_ends_skip_users_without_preferences(env):
    result = scheduler.run_jobs(CONFIG, "hpc-poc", now=NOW)
    assert result["notified"] == 0 and env["sns"].published == []
    assert env["dynamo"].with_prefix("USER#carol", "JOBSEEN#") == []


def test_parse_job_ends():
    jobs = scheduler.parse_job_ends(ENDS.splitlines())
    assert [j["job_id"] for j in jobs] == ["31", "32", "33"]  # an unknown end time is skipped
    assert jobs[2]["state"] == "CANCELLED" and jobs[1]["env"] is None


def test_finish_mode_offboarding_removes_the_association_after_the_last_job(env):
    store = env["store"]
    item = {"team": "teamb", "user": "bob", "mode": "finish", "state": "finishing", "cluster": "hpc-poc"}
    store.put({"pk": "SYS", "sk": "OFFBOARD#teamb#bob", **item})
    store.put({"pk": "USER#bob", "sk": "OFFBOARD#teamb", **item})
    env["head"].user_jobs[0] = ["101"]
    assert scheduler.run_jobs(CONFIG, "hpc-poc", now=NOW)["offboarded"] == []
    assert not any("delete user" in c for c in env["head"].commands)
    env["head"].user_jobs[0] = []
    assert scheduler.run_jobs(CONFIG, "hpc-poc", now=NOW)["offboarded"] == ["teamb/bob"]
    assert env["head"].commands[-1] == "/opt/slurm/bin/sacctmgr -i delete user where name=bob account=teamb"
    assert env["dynamo"].item("SYS", "OFFBOARD#teamb#bob")["state"] == "offboarded"
    assert env["dynamo"].item("USER#bob", "OFFBOARD#teamb")["state"] == "offboarded"
    # once offboarded, the user is no longer checked
    scheduler.run_jobs(CONFIG, "hpc-poc", now=NOW)
    assert "OFFB0" not in env["head"].commands[-1]


# --- budgets ----------------------------------------------------------------------------------------------------


@pytest.fixture
def teams(env):
    seed_record(env["ssm"], "teama")
    seed_record(env["ssm"], "teamb")
    return env


def test_budget_crossing_is_notified_once_per_threshold_and_month(teams):
    env = teams
    store, sns = env["store"], env["sns"]
    # teama month-to-date estimate: 1.861 + 2.24208 = 4.10308 USD
    store.put({"pk": "TEAM#teama", "sk": "BUDGET", "budget_usd": 5.0})
    _prefs(store, "alice", email_budget=True)
    first = scheduler.run_budgets(CONFIG, "hpc-poc", now=NOW)
    assert first["alerts"] == [{"team": "teama", "threshold": 80, "percent": 82.1}]
    state = env["dynamo"].item("TEAM#teama", "BUDGETSTATE#2026-11")
    assert state["percent"] == 82.1 and state["spent_usd"] == pytest.approx(4.1031, abs=1e-3)
    topics = [p["TopicArn"] for p in sns.published]
    assert topics == [CONFIG["portal_topic_arn"], CONFIG["admin_topic_arn"]]
    assert sns.published[0]["MessageAttributes"]["user"]["StringValue"] == "alice"
    assert "82.1 %" in sns.published[0]["Message"]
    assert env["dynamo"].with_prefix("USER#alice", "NOTIF#")[0]["kind"] == "budget"

    assert scheduler.run_budgets(CONFIG, "hpc-poc", now=NOW)["alerts"] == []  # same month, same threshold
    assert len(sns.published) == 2

    store.put({"pk": "TEAM#teama", "sk": "BUDGET", "budget_usd": 4.0})       # now above 100 %
    exceeded = scheduler.run_budgets(CONFIG, "hpc-poc", now=NOW)
    assert [a["threshold"] for a in exceeded["alerts"]] == [100]
    assert "exceeded" in sns.published[-1]["Subject"]

    env["head"].outputs["JOBS"] = out.SACCT_JOBS.replace("2026-11-02", "2026-12-01")
    next_month = scheduler.run_budgets(CONFIG, "hpc-poc", now=NOW.replace(month=12, day=1, hour=23))
    assert [a["threshold"] for a in next_month["alerts"]] == [80, 100]


def test_budgets_without_any_budget_do_nothing(teams):
    assert scheduler.run_budgets(CONFIG, "hpc-poc", now=NOW) == {"task": "budgets", "teams": 0, "alerts": []}
    assert teams["head"].commands == []


def test_budget_banner_reads_the_scheduler_state(teams):
    from api.teams import notify
    store = teams["store"]
    store.put({"pk": "TEAM#teama", "sk": "BUDGET", "budget_usd": 4.0})
    scheduler.run_budgets(CONFIG, "hpc-poc", now=NOW)
    banner = notify.budget_banner(store, "teama", "2026-11")
    assert banner["level"] == "exceeded" and banner["percent"] == 102.6


# --- login health and handler -----------------------------------------------------------------------------------


def test_login_health_metric(env, mocker):
    cloudwatch = mocker.Mock()
    mocker.patch("awslambda.scheduler.cloudwatch_client", return_value=cloudwatch)
    assert scheduler.run_login_health(CONFIG, "hpc-poc") == {"task": "login_health", "running": 2, "healthy": 1}
    kwargs = cloudwatch.put_metric_data.call_args.kwargs
    assert kwargs["Namespace"] == "HPC"
    assert kwargs["MetricData"] == [{"MetricName": "LoginNodesHealthy",
                                     "Dimensions": [{"Name": "ClusterName", "Value": "hpc-poc"}],
                                     "Value": 1, "Unit": "Count"}]


def test_handler_dispatches_and_validates(env, mocker, monkeypatch):
    monkeypatch.setenv("PCUI_SCHEDULER_CLUSTER", "hpc-poc")
    run = mocker.patch.dict(scheduler.TASKS, {"jobs": mocker.Mock(return_value={"task": "jobs"})})
    assert scheduler.handler({"task": "jobs"}) == {"task": "jobs"}
    assert run["jobs"].call_args.args[1] == "hpc-poc"
    with pytest.raises(ValueError):
        scheduler.handler({"task": "rm"})
    monkeypatch.setenv("PCUI_SCHEDULER_CLUSTER", "hpc;id")
    with pytest.raises(ValueError):
        scheduler.handler({"task": "jobs"})
    monkeypatch.setenv("PCUI_SCHEDULER_CLUSTER", "other")
    with pytest.raises(ValueError):
        scheduler.handler({"task": "jobs"})


def test_handler_without_configuration_skips(mocker, monkeypatch, parameter_store):
    monkeypatch.setenv("PCUI_SCHEDULER_CLUSTER", "hpc-poc")
    lifecycle_store._config_cache.clear()
    assert scheduler.handler({"task": "budgets"}) == {"task": "budgets", "skipped": "team lifecycle not configured"}


def test_scheduler_commands_contain_no_billing(env):
    _prefs(env["store"], "carol", job_notify=True)
    scheduler.run_jobs(CONFIG, "hpc-poc", now=NOW)
    assert not any("billing" in c for c in env["head"].commands)
    assert json.dumps(env["dynamo"].with_prefix("USER#carol")).count("billing") == 0


def test_budget_estimate_prices_every_queue_like_the_usage_page(teams):
    # A build-queue job (not quota-controlled, so not in hpc-type-quota.conf) counts through the cluster config,
    # as on the Team usage page (seen live: the banner showed 0 while the usage page showed the build cost).
    import base64
    env = teams
    env["head"].outputs["JOBS"] = (
        "8|teama|alice|build|build-dy-build-1|2026-11-02T12:00:00|2026-11-02T14:00:00|7200|cpu=4,mem=15000M,node=1|COMPLETED")
    config = "Scheduling:\n  SlurmQueues:\n  - Name: build\n    ComputeResources:\n    - Name: build\n      InstanceType: m7i.xlarge\n"
    env["head"].outputs["CONFIG"] = base64.b64encode(config.encode()).decode()
    env["store"].put({"pk": "TEAM#teama", "sk": "BUDGET", "budget_usd": 1.0})
    scheduler.run_budgets(CONFIG, "hpc-poc", now=NOW)
    state = env["dynamo"].item("TEAM#teama", "BUDGETSTATE#2026-11")
    assert state["spent_usd"] == pytest.approx(2 * 0.2016, abs=1e-3)
    assert "base64 -w0 /opt/parallelcluster/shared/cluster-config.yaml" in env["head"].commands[-1]


# --- job alerts -------------------------------------------------------------------------------------------------

ALERT_JOBS = """\
40|carol|teama|g6e-xlarge|RUNNING|None|2026-11-19T09:00:00|2026-11-19T10:00:00|g6e-xlarge-dy-g6e-xlarge-1|train
26|dave|teama|g6e-2xlarge|PENDING|None|2026-11-19T19:00:00|N/A||big"""
ALERT_SINFO = """\
g6e-xlarge|allocated|1|none
g6e-xlarge|idle~|7|none
g6e-2xlarge|down~|8|(Code:InsufficientInstanceCapacity)Temporarily disabling node due to insufficient capacity"""


def _gpu_section(util, now=NOW, minutes=65):
    last = int(now.timestamp())
    samples = [f"{last - 60 * i} {util} {util}" for i in reversed(range(minutes))]
    return "\n".join(["map 40 g6e-xlarge-dy-g6e-xlarge-1", "node g6e-xlarge-dy-g6e-xlarge-1", *samples])


@pytest.fixture
def alerts_env(env):
    env["head"].outputs.update({"ALERTJOBS": ALERT_JOBS, "QUEUE": "teama|carol|g6e-xlarge|1|teama-g6e-xlarge|RUNNING",
                                "SINFO": ALERT_SINFO, "GPU": _gpu_section(0)})
    return env


def test_job_alerts_notify_the_submitter_in_the_portal_and_by_email_once(alerts_env, monkeypatch):
    env = alerts_env
    store, sns = env["store"], env["sns"]
    _prefs(store, "carol", subscription_arn="arn:aws:sns:us-west-2:123456789012:hpc-poc-portal-notify:sub-1")
    _prefs(store, "dave", subscription_arn="arn:aws:sns:us-west-2:123456789012:hpc-poc-portal-notify:sub-2",
           email_alerts=False)
    result = scheduler.run_job_alerts(CONFIG, "hpc-poc", now=NOW)
    assert result["task"] == "job_alerts" and result["jobs"] == 2
    assert sorted((a["kind"], a["job_id"], a["user"]) for a in result["alerts"]) == [
        ("gpu_idle", "40", "carol"), ("long_run", "40", "carol"), ("no_capacity", "26", "dave")]
    # bell notifications for the submitter only, with a Chinese text
    carol = env["dynamo"].with_prefix("USER#carol", "NOTIF#")
    assert sorted(n["kind"] for n in carol) == ["gpu_idle", "long_run"]
    assert all(n["job_id"] == "40" and n["instance_type"] == "g6e.xlarge" for n in carol)
    (dave,) = env["dynamo"].with_prefix("USER#dave", "NOTIF#")
    assert dave["kind"] == "no_capacity" and "系统会自动重试" in dave["text"] and dave["hours"] == 17.0
    assert dave["alternatives"] == [{"instance_type": "g6e.xlarge", "partition": "g6e-xlarge", "remaining": 1,
                                     "status": "startable"}]
    # e-mail: carol (email_alerts on by default, subscribed); dave switched alert e-mails off
    assert result["emailed"] == 2 and len(sns.published) == 2
    assert {p["MessageAttributes"]["user"]["StringValue"] for p in sns.published} == {"carol"}
    assert all(p["TopicArn"] == CONFIG["portal_topic_arn"] and p["Subject"].isascii() for p in sns.published)
    assert {p["Subject"] for p in sns.published} == {"HPC job 40: GPU idle for 60 min",
                                                    "HPC job 40: running for 26 hours"}
    assert all("一键取消：门户 → 我的作业" in p["Message"] for p in sns.published)
    # state per job; the next run (5 minutes later, still idle, still pending) notifies nothing again
    assert env["dynamo"].item("USER#carol", "ALERT#40")["idle_alerted"] is True
    assert env["dynamo"].item("USER#dave", "ALERT#26")["no_capacity"] is True
    env["head"].outputs["GPU"] = _gpu_section(0, now=NOW.replace(minute=5))
    again = scheduler.run_job_alerts(CONFIG, "hpc-poc", now=NOW.replace(minute=5))
    assert again["alerts"] == [] and len(sns.published) == 2
    assert len(env["dynamo"].with_prefix("USER#carol", "NOTIF#")) == 2
    commands = env["head"].commands[0]
    assert "squeue -a -h -t R,CF,PD -o '%i|%u|%a|%P|%T|%r|%V|%S|%N|%j'" in commands
    assert "%R|%T|%D|%E" in commands and " -i " not in commands and "scancel" not in commands


def test_job_alerts_save_state_only_after_the_bell_item_so_a_failure_is_retried(alerts_env, mocker):
    env = alerts_env
    real_put = datastore.Store.put

    def put(self, item, if_absent=False):
        if item["sk"].startswith("NOTIF#") and item.get("job_id") == "26":
            raise RuntimeError("throttled")
        return real_put(self, item, if_absent)

    patched = mocker.patch.object(datastore.Store, "put", autospec=True, side_effect=put)
    result = scheduler.run_job_alerts(CONFIG, "hpc-poc", now=NOW)
    # job 26 failed, job 40 is still delivered and saved
    assert result["failed"] == [{"job_id": "26", "step": "portal"}]
    assert sorted(a["kind"] for a in result["alerts"]) == ["gpu_idle", "long_run"]
    assert env["dynamo"].item("USER#carol", "ALERT#40")["idle_alerted"] is True
    assert env["dynamo"].item("USER#dave", "ALERT#26") is None
    # the next run sends the no-capacity alert of job 26, and nothing again for job 40
    patched.side_effect = lambda self, item, if_absent=False: real_put(self, item, if_absent)
    env["head"].outputs["GPU"] = _gpu_section(0, now=NOW.replace(minute=5))
    again = scheduler.run_job_alerts(CONFIG, "hpc-poc", now=NOW.replace(minute=5))
    assert [(a["kind"], a["job_id"]) for a in again["alerts"]] == [("no_capacity", "26")] and again["failed"] == []
    assert env["dynamo"].item("USER#dave", "ALERT#26")["no_capacity"] is True


def test_job_alerts_e_mail_failure_is_logged_and_the_bell_item_kept(alerts_env, mocker):
    env = alerts_env
    _prefs(env["store"], "carol", subscription_arn="arn:aws:sns:us-west-2:123456789012:hpc-poc-portal-notify:sub-1")
    mocker.patch("api.teams.notify.publish", side_effect=RuntimeError("topic policy"))
    result = scheduler.run_job_alerts(CONFIG, "hpc-poc", now=NOW)
    assert result["emailed"] == 0
    assert sorted(f["kind"] for f in result["failed"]) == ["gpu_idle", "long_run"]
    assert len(env["dynamo"].with_prefix("USER#carol", "NOTIF#")) == 2
    assert env["dynamo"].item("USER#carol", "ALERT#40")["idle_alerted"] is True


def test_job_alerts_without_a_subscription_stay_in_the_portal(alerts_env):
    result = scheduler.run_job_alerts(CONFIG, "hpc-poc", now=NOW)
    assert result["emailed"] == 0 and alerts_env["sns"].published == []
    assert len(alerts_env["dynamo"].with_prefix("USER#carol", "NOTIF#")) == 2


def test_job_alerts_thresholds_from_the_environment(alerts_env, monkeypatch):
    monkeypatch.setenv("ALERT_LONG_RUN_HOURS", "48")
    monkeypatch.setenv("ALERT_NO_CAPACITY_HOURS", "24")
    monkeypatch.setenv("ALERT_GPU_IDLE_PERCENT", "1")
    alerts_env["head"].outputs["GPU"] = _gpu_section(3)
    assert scheduler.run_job_alerts(CONFIG, "hpc-poc", now=NOW)["alerts"] == []
    assert "pcui-gpu /efs/.pcui-gpu 65" in alerts_env["head"].commands[0]


def test_handler_knows_the_job_alerts_task(env, monkeypatch, mocker):
    monkeypatch.setenv("PCUI_SCHEDULER_CLUSTER", "hpc-poc")
    run = mocker.patch.dict(scheduler.TASKS, {"job_alerts": mocker.Mock(return_value={"task": "job_alerts"})})
    assert scheduler.handler({"task": "job_alerts"}) == {"task": "job_alerts"}
    assert run["job_alerts"].call_args.args[1] == "hpc-poc"


def test_mail_subjects_are_ascii_even_for_chinese_job_names():
    job = {"job_id": "31", "name": "训练", "state": "COMPLETED", "end": "2026-11-20T11:58:00", "account": "teama",
           "partition": "g6e-xlarge", "exit_code": "0:0", "env": None}
    subject, message = scheduler.job_message(job, "g6e.xlarge")
    assert subject == "Job 31 (??) completed" and "您的作业 31（训练）已完成" in message
    subject, message = scheduler.budget_message("teama", "2026-11", 82.1, 4.1, 5.0, 80)
    assert subject.isascii() and "占月度预算" in message
