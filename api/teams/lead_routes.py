# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Team lead views (``/team-quota/lead/*``): callers in ``team-<team>`` (own team only) or ``admin`` (any team).

Leads see job METADATA of their team (id, user, name, queue, state, reason, times, machines, QOS) and who holds
the team's machines per instance type. They never receive a job script, its output or any file content: the
head-node commands below only print ``squeue`` metadata columns and ``sacctmgr`` QOS limits.
"""
from datetime import datetime, timezone

from flask import request

from api.PclusterApiHandler import authenticated
from api.teams import catalog, datastore, lifecycle_store, notify
from api.teams.reasons import explain_pending, reason_text
from api.teams.routes import ADMINS, TEAM_PREFIXES, _target, team_quota
from api.teams.scope import resolve_team_scope
from api.teams.slurm import (
    parse_qos,
    parse_queue,
    parse_sinfo,
    parse_sinfo_ice,
    parse_team_jobs,
    qos_cmd,
    queue_cmd,
    queue_usage,
    run_sections,
    sinfo_cmd,
    team_jobs_cmd,
    type_qos_limits,
)
from api.teams.validation import TeamList
from api.validation import validated

LEAD_JOB_FIELDS = ("job_id", "user", "name", "partition", "instance_type", "state", "reason", "reason_text",
                   "submit", "start", "time_limit", "elapsed", "nodes", "qos")


def _lead_team(args):
    scope = resolve_team_scope(args.get("team"))
    if scope is None or len(scope) != 1:
        raise ValueError("请选择团队（team 参数）")
    return scope[0]


def _banner(team):
    try:
        config = lifecycle_store.load_config()
        if not config:
            return None
        return notify.budget_banner(datastore.store(config), team, f"{datetime.now(timezone.utc):%Y-%m}")
    except Exception:  # the banner is informative
        return None


@team_quota.get("/lead/jobs")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=TeamList)
def lead_jobs():
    args = TeamList.load(request.args)
    team = _lead_team(args)
    cluster_name, region = _target(args)
    out = run_sections(cluster_name, region, [
        ("TEAMJOBS", team_jobs_cmd(team)), ("QOS", qos_cmd()), ("QUEUE", queue_cmd()), ("SINFO", sinfo_cmd()),
    ])
    cluster_catalog = catalog.instance_catalog(cluster_name, region)
    types = {e["partition"]: e["instance_type"] for e in cluster_catalog["types"]}
    usage = queue_usage(parse_queue(out.get("QUEUE", []))).get(team, {})
    context = {
        "types": types,
        "limits": type_qos_limits(team, parse_qos(out.get("QOS", [])), list(types)),
        "running": {p: u.get("running", 0) for p, u in usage.items()},
        "nodes": parse_sinfo(out.get("SINFO", [])),
        "ice": parse_sinfo_ice(out.get("SINFO", [])),
    }
    jobs = []
    for job in parse_team_jobs(out.get("TEAMJOBS", [])):
        job["instance_type"] = types.get(job["partition"])
        job["reason_text"] = (explain_pending(job["reason"], job["partition"], job["qos"], context)
                              if job["state"] == "PENDING" else reason_text(None))
        jobs.append({key: job.get(key) for key in LEAD_JOB_FIELDS})
    return {"team": team, "jobs": jobs}


@team_quota.get("/lead/quota")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=TeamList)
def lead_quota():
    """Per instance type: the team's machine count, machines running/pending/borrowed and who holds them."""
    args = TeamList.load(request.args)
    team = _lead_team(args)
    cluster_name, region = _target(args)
    out = run_sections(cluster_name, region, [("QOS", qos_cmd()), ("QUEUE", queue_cmd())])
    cluster_catalog = catalog.instance_catalog(cluster_name, region)
    partitions = [e["partition"] for e in cluster_catalog["types"]]
    limits = type_qos_limits(team, parse_qos(out.get("QOS", [])), partitions)
    usage = queue_usage(parse_queue(out.get("QUEUE", []))).get(team, {})
    types = []
    for entry in cluster_catalog["types"]:
        used = usage.get(entry["partition"], {})
        types.append({
            "instance_type": entry["instance_type"],
            "partition": entry["partition"],
            "gpus": entry["gpus"],
            "limit": limits.get(entry["partition"], 0),
            "running": used.get("running", 0),
            "pending": used.get("pending", 0),
            "borrowed": used.get("borrowed", 0),
            "holders": [{"user": user, **counts} for user, counts in sorted((used.get("users") or {}).items())],
        })
    return {"team": team, "types": types, "unmanaged_queues": cluster_catalog["unmanaged_queues"],
            "budget": _banner(team)}
