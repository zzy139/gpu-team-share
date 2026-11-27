# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Team quota, usage and cost API (blueprint ``team_quota`` mounted at ``/team-quota``).

* Read routes: ``admin`` or any ``team-<team>`` group; non-admins only ever get their own teams.
* Write routes: ``admin`` only, CSRF protected, validated and audited.

Quotas are per instance type: ``type_limits`` = {instance_type: machines the team may run at once}. Instance
types come from the cluster catalog (``catalog``): one queue with one compute resource and one GPU instance type.
Costs are estimated per job (machines x hours x on-demand price) and billed costs are allocated by the share of
the estimated cost. Slurm's internal billing TRES is never shown or accepted.
"""
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError
from flask import Blueprint, abort, jsonify, request

import api.PclusterApiHandler as handler
from api.PclusterApiHandler import authenticated
from api.costmonitoring.costexplorer_client import CostExplorerNotActiveException
from api.pcm_globals import logger
from api.security.csrf.csrf import csrf_needed
from api.teams import catalog, cluster, costs, datastore, lifecycle, lifecycle_store, pricing
from api.teams.audit import audit
from api.teams.directory import ad_command, env_owner_command, parse_ad_output, parse_env_owner
from api.teams.reasons import capacity_status
from api.teams.scope import ADMIN_GROUP, TEAM_GROUP_PREFIX, filter_teams, is_admin, resolve_team_scope
from api.teams.slurm import (
    ICE_REASON_RE,
    SlurmCommandError,
    add_member_cmd,
    assoc_cmd,
    cancel_user_jobs_cmd,
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
    parse_assoc,
    parse_qos,
    parse_queue,
    parse_sacct_jobs,
    parse_sinfo_nodes,
    qos_cmd,
    qos_name,
    queue_cmd,
    queue_usage,
    release_member_cmd,
    remove_member_cmd,
    run_command,
    run_sections,
    run_writes,
    set_fairshare_cmd,
    set_type_qos_cmd,
    sinfo_nodes_cmd,
    type_qos_limits,
    type_qos_name,
)
from api.teams.validation import (
    AUDIT_MAX_ROWS,
    AddMember,
    Advance,
    AuditQuery,
    ClusterRegion,
    ClusterTarget,
    CreateLifecycleTeam,
    CreateTeam,
    Offboard,
    Reenable,
    TeamList,
    Trend,
    UpdateTeam,
    Usage,
    normalize_email,
    parse_date,
    validate_team_name,
    validate_user_name,
)
from api.validation import validated

team_quota = Blueprint("team_quota", __name__)

ADMINS = {ADMIN_GROUP}
TEAM_PREFIXES = (TEAM_GROUP_PREFIX,)
DEFAULT_TREND_MONTHS = 6
AUDIT_MAX_MONTHS = 13
SOURCES = {"billed": "cost-explorer", "estimate": "pricing-api"}

_cost_client = None


class ClusterNotManaged(Exception):
    pass


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def cost_client():
    global _cost_client
    if _cost_client is None:
        _cost_client = costs.TeamCostExplorerClient(boto3.client("ce", region_name=costs.CE_REGION))
    return _cost_client


def _target(args):
    return args["cluster_name"], args.get("region") or handler.REGION


def _error(code, message):
    return jsonify({"code": code, "message": message}), code


def _today():
    return datetime.now(timezone.utc).replace(tzinfo=None).date()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project(args, cluster_name):
    """Cost Explorer Project tag to query. Only admins may pick a tag other than the cluster name."""
    project = args.get("project")
    if project and project != cluster_name and not is_admin():
        abort(403)
    return project or cluster_name


# --- error handlers ---------------------------------------------------------------------------------------------


@team_quota.errorhandler(SlurmCommandError)
def handle_slurm_error(err):
    logger.error(f"Slurm command failed: {err.message}", extra=dict(status=400, exception=type(err)))
    message = err.message if is_admin() else "头节点上的 Slurm 命令执行失败"
    return _error(400, message)


@team_quota.errorhandler(404)
def handle_not_found(_err):
    # The app-level 404 handler serves the frontend; API callers need JSON instead.
    return _error(404, "未找到")


@team_quota.errorhandler(CostExplorerNotActiveException)
def handle_costexplorer_not_active(err):
    logger.error(str(err), extra=dict(status=405, exception=type(err)))
    return _error(405, str(err))


@team_quota.errorhandler(datastore.DataStoreUnavailable)
def handle_datastore_unavailable(err):
    return _error(409, str(err))


# --- helpers ----------------------------------------------------------------------------------------------------


def _load_state(cluster_name, region):
    out = run_sections(cluster_name, region, [("ASSOC", assoc_cmd()), ("QOS", qos_cmd())])
    return parse_assoc(out.get("ASSOC", [])), parse_qos(out.get("QOS", []))


def _write(action, cluster_name, region, team, params, commands):
    try:
        run_writes(cluster_name, region, commands)
    except SlurmCommandError as err:
        audit(action, cluster_name, team, params, "failure", error=err.message)
        raise
    audit(action, cluster_name, team, params, "success")


def _reject(action, cluster_name, team, params, code, message):
    audit(action, cluster_name, team, params, "rejected", error=message)
    return _error(code, message)


def _catalog(cluster_name, region):
    return catalog.instance_catalog(cluster_name, region)


def _partitions_of(cluster_catalog):
    return [entry["partition"] for entry in cluster_catalog["types"]]


def _limits_by_type(team, qos, cluster_catalog):
    """{instance_type: machines} of the team's existing type QOS (0 = not allowed) for every catalog type."""
    existing = type_qos_limits(team, qos, _partitions_of(cluster_catalog))
    return {entry["instance_type"]: existing.get(entry["partition"], 0) for entry in cluster_catalog["types"]}


def _allowed_partitions(team, qos, cluster_catalog):
    return {p: n for p, n in type_qos_limits(team, qos, _partitions_of(cluster_catalog)).items() if n > 0}


def _optional_config():
    try:
        return lifecycle_store.load_config()
    except lifecycle_store.LifecycleConfigError as err:
        logger.warning(str(err))
        return None


def _budgets(config, teams):
    """{team: budget_usd} from the data table (best effort; {} without a table)."""
    if not config or not teams:
        return {}
    try:
        items = datastore.store(config).batch_get([(datastore.team_pk(t), datastore.BUDGET_SK) for t in teams])
    except Exception as err:
        logger.warning(f"Budgets unavailable: {type(err).__name__}")
        return {}
    return {item["pk"][len("TEAM#"):]: item.get("budget_usd") for item in items}


def _save_budget(config, team, budget):
    store = datastore.store(config)
    if budget:
        store.put({"pk": datastore.team_pk(team), "sk": datastore.BUDGET_SK, "budget_usd": budget,
                   "updated_at": _now_iso()})
    else:
        store.delete(datastore.team_pk(team), datastore.BUDGET_SK)


def _running_by_type(usage, cluster_catalog):
    by_partition = catalog.by_partition(cluster_catalog)
    result = {}
    for partition, entry in sorted(usage.items()):
        key = by_partition[partition]["instance_type"] if partition in by_partition else partition
        result[key] = {
            "partition": partition,
            "running": entry["running"],
            "pending": entry["pending"],
            "borrowed": entry["borrowed"],
            "users": {u: dict(v) for u, v in sorted(entry["users"].items())},
        }
    return result


def _offboarded(config):
    """{team: {user: state}} from the SYS offboarding index (best effort)."""
    if not config:
        return {}
    try:
        items = datastore.store(config).query(datastore.SYS_PK, prefix="OFFBOARD#")
    except Exception as err:
        logger.warning(f"Offboarding state unavailable: {type(err).__name__}")
        return {}
    result = {}
    for item in items:
        if item.get("team") and item.get("user"):
            result.setdefault(item["team"], {})[item["user"]] = item.get("state")
    return result


def _team_row(team, qos, usage, total_fairshare, cluster_catalog, budgets, records):
    name = team["name"]
    fairshare = team.get("fairshare")
    team_usage = usage.get(name, {})
    by_partition = catalog.by_partition(cluster_catalog)
    machines = sum(e["running"] + e["borrowed"] for e in team_usage.values())
    gpus = sum((e["running"] + e["borrowed"]) * by_partition[p]["gpus"]
               for p, e in team_usage.items() if p in by_partition)
    record = records.get(name)
    return {
        "name": name,
        "fairshare": fairshare,
        "fairshare_share": round(fairshare / total_fairshare, 4) if fairshare and total_fairshare else None,
        "qos": qos_name(name) if qos_name(name) in qos else None,
        "type_limits": _limits_by_type(name, qos, cluster_catalog),
        "running_by_type": _running_by_type(team_usage, cluster_catalog),
        "members": [member["user"] for member in team["members"]],
        "running": {"jobs": sum(e["jobs"] for e in team_usage.values()), "machines": machines, "gpus": gpus},
        "budget_usd": budgets.get(name),
        "env_storage_cap_gb": record.get("env_storage_cap_gb") if record else None,
    }


# --- read routes ------------------------------------------------------------------------------------------------


@team_quota.get("/clusters")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=ClusterRegion)
def list_clusters():
    resolve_team_scope(None)  # a caller with a team-* group but no valid team gets 403
    region = request.args.get("region") or handler.REGION
    clusters = cluster.list_slurm_clusters(region)
    # The page preselects the first cluster: put the clusters this portal's team lifecycle manages first, so a
    # region that also holds other clusters does not open on one the portal cannot reach.
    config = _optional_config()
    managed = set(config["clusters"]) if config else set()
    clusters.sort(key=lambda c: c.get("name") not in managed)
    return {"clusters": clusters}


@team_quota.get("/catalog")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=ClusterTarget)
def get_catalog():
    """Instance types that team quotas can count, and the queues they cannot (``unmanaged_queues``)."""
    resolve_team_scope(None)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    return _catalog(cluster_name, region)


@team_quota.get("/capacity")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=TeamList)
def capacity():
    """机型与节点: per instance type the specs, the capacity ``status`` (available / startable / no_capacity /
    busy), the node states like ``sinfo`` (state, count, reason, node names) and per team the machine count,
    running, pending and borrowed machines. Admins see every team and also the node states of the queues
    outside the catalog (``partitions``); leads see their own team(s) only. Read-only."""
    args = TeamList.load(request.args)
    scope = resolve_team_scope(args.get("team"))
    cluster_name, region = _target(args)
    out = run_sections(cluster_name, region, [("ASSOC", assoc_cmd()), ("QOS", qos_cmd()), ("QUEUE", queue_cmd()),
                                              ("SINFO", sinfo_nodes_cmd())])
    teams = parse_assoc(out.get("ASSOC", []))
    qos = parse_qos(out.get("QOS", []))
    usage = queue_usage(parse_queue(out.get("QUEUE", [])))
    node_rows = parse_sinfo_nodes(out.get("SINFO", []))
    cluster_catalog = _catalog(cluster_name, region)
    names = [name for name in sorted(teams) if scope is None or name in scope]
    types = []
    for entry in cluster_catalog["types"]:
        partition = entry["partition"]
        rows = node_rows.get(partition, [])
        states = {}
        for row in rows:
            states[row["state"]] = states.get(row["state"], 0) + row["count"]
        ice = any(ICE_REASON_RE.search(row["reason"] or "") for row in rows) if rows else None
        team_rows = []
        for name in names:
            used = usage.get(name, {}).get(partition, {})
            team_rows.append({"team": name, "limit": type_qos_limits(name, qos, [partition]).get(partition, 0),
                              "running": used.get("running", 0), "pending": used.get("pending", 0),
                              "borrowed": used.get("borrowed", 0)})
        types.append({**entry, "status": capacity_status(states, ice), "no_capacity": bool(ice),
                      "node_total": sum(states.values()), "nodes": rows, "teams": team_rows})
    result = {"types": types, "teams": names, "unmanaged_queues": cluster_catalog["unmanaged_queues"]}
    if is_admin():
        managed = {entry["partition"] for entry in cluster_catalog["types"]}
        result["partitions"] = [{"partition": p, "nodes": rows} for p, rows in sorted(node_rows.items())
                                if p not in managed]
    return result


def _teams_view(cluster_name, region, scope):
    out = run_sections(cluster_name, region, [("ASSOC", assoc_cmd()), ("QOS", qos_cmd()), ("QUEUE", queue_cmd())])
    teams = parse_assoc(out.get("ASSOC", []))
    qos = parse_qos(out.get("QOS", []))
    usage = queue_usage(parse_queue(out.get("QUEUE", [])))
    cluster_catalog = _catalog(cluster_name, region)
    names = [name for name in sorted(teams) if scope is None or name in scope]
    config = _optional_config()
    records = {}
    if config is not None and cluster_name in config["clusters"]:
        records = {r["team"]: r for r in lifecycle_store.list_records(config) if r["team"] in names}
    budgets = _budgets(config, names)
    total_fairshare = sum(t["fairshare"] or 0 for t in teams.values())
    rows = [_team_row(teams[name], qos, usage, total_fairshare, cluster_catalog, budgets, records) for name in names]
    if is_admin():
        offboarded = _offboarded(config)
        for row in rows:
            row["offboarded"] = offboarded.get(row["name"], {})
    return {
        "teams": filter_teams(rows, scope),
        "catalog": cluster_catalog["types"],
        "unmanaged_queues": cluster_catalog["unmanaged_queues"],
    }


@team_quota.get("/teams")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=TeamList)
def list_teams():
    args = TeamList.load(request.args)
    scope = resolve_team_scope(args.get("team"))
    cluster_name, region = _target(args)
    return {**_teams_view(cluster_name, region, scope), "editable": is_admin()}


def _billed_costs(project, start, end, notes):
    """Return (compute, total) from Cost Explorer, or (None, None) with a note when unavailable."""
    ce_end = costs.ce_end(end.isoformat())
    if start.isoformat() >= ce_end:
        notes.append("billed-future-period")
        return None, None
    try:
        client = cost_client()
        compute = costs.cached_cost(client, costs.compute_filter(project), start.isoformat(), ce_end, "MONTHLY")
        total = costs.cached_cost(client, costs.project_filter(project), start.isoformat(), ce_end, "MONTHLY")
        return costs.sum_amounts(compute), costs.sum_amounts(total)
    except CostExplorerNotActiveException:
        notes.append("billed-cost-explorer-not-active")
    except Exception as err:
        logger.warning(f"Cost Explorer query failed: {err}")
        notes.append("billed-unavailable")
    return None, None


def job_type_resolver(cluster_name, region, notes):
    """Return ``(type_of, prices)``: the instance type of a job and on-demand prices per instance-hour."""
    try:
        mapping = cluster.compute_resource_instance_types(cluster_name, region)
        cluster_catalog = _catalog(cluster_name, region)
    except Exception as err:
        logger.warning(f"Cluster catalog unavailable: {err}")
        notes.append("estimate-prices-unavailable")
        return (lambda job: None), {}
    by_partition = catalog.by_partition(cluster_catalog)

    def type_of(job):
        entry = by_partition.get(job.get("partition"))
        if entry:
            return entry["instance_type"]
        return cluster.instance_type_for_job(job.get("partition"), job.get("nodelist"), mapping)

    types = {itype for types_ in mapping.values() for itype in types_ or []}
    try:
        prices = pricing.instance_prices(types, region)
    except Exception as err:
        logger.warning(f"Instance prices unavailable: {err}")
        notes.append("estimate-prices-unavailable")
        prices = {}
    return type_of, prices


def _member_rows(estimates, scope, user=None):
    rows = []
    for (team, login), entry in sorted(estimates["members"].items()):
        if scope is not None and team not in scope:
            continue
        if user is not None and login != user:
            continue
        rows.append({"team": team, "user": login, **entry})
    return rows


def _usage_view(args, cluster_name, region, scope, project, user=None, totals=False):
    """Team usage rows for ``scope`` with ``by_type`` and ``by_member``. With ``user`` (member portal), only that
    user's member row is returned and also given as ``mine``."""
    start, end = parse_date(args["start"]), parse_date(args["end"])
    out = run_sections(cluster_name, region, [("ASSOC", assoc_cmd()), ("JOBS", jobs_cmd(args["start"], args["end"]))])
    teams = parse_assoc(out.get("ASSOC", []))
    jobs = parse_sacct_jobs(out.get("JOBS", []))

    notes = []
    type_of, prices = job_type_resolver(cluster_name, region, notes)
    estimates = costs.estimate(jobs, type_of, prices, start, end)
    names = sorted(set(teams) | set(estimates["teams"]))
    team_cost = {name: estimates["teams"].get(name, {}).get("estimate_cost", 0.0) for name in names}
    compute, total = _billed_costs(project, start, end, notes)
    shared = None if compute is None else round(max(total - compute, 0.0), 6)
    allocation = costs.allocate(team_cost, compute, shared)
    if any(e["unpriced_instance_hours"] > 0 for e in estimates["teams"].values()):
        notes.append("estimate-has-unpriced-hours")
    if compute is not None and sum(team_cost.values()) <= 0:
        notes.append("billed-unallocated-no-usage")

    rows = []
    for name in names:
        rows.append({"name": name, **(estimates["teams"].get(name) or costs.empty_estimate()),
                     **allocation["teams"][name]})

    response = {
        "period": {"start": args["start"], "end": args["end"]},
        "project": project,
        "teams": filter_teams(rows, scope),
        "by_member": _member_rows(estimates, scope, user),
        "billed_available": compute is not None,
        "prices": prices,
        "sources": SOURCES,
        "notes": notes,
    }
    if user is not None:
        team = scope[0]
        response["mine"] = {"user": user, **(estimates["members"].get((team, user)) or costs.empty_estimate())}
    if totals:
        response["totals"] = {
            "compute_cost": compute,
            "shared_cost": shared,
            "total_cost": total,
            "instance_hours": round(sum(e["instance_hours"] for e in estimates["teams"].values()), 4),
            "gpu_hours": round(sum(e["gpu_hours"] for e in estimates["teams"].values()), 4),
            "estimate_cost": round(sum(team_cost.values()), 4),
            "unallocated_compute_cost": allocation["unallocated"]["compute_cost"],
            "unallocated_shared_cost": allocation["unallocated"]["shared_cost"],
        }
    return response


@team_quota.get("/usage")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=Usage)
def usage():
    args = Usage.load(request.args)
    scope = resolve_team_scope(args.get("team"))
    cluster_name, region = _target(args)
    project = _project(args, cluster_name)
    return _usage_view(args, cluster_name, region, scope, project, totals=is_admin())


def _month_starts(months, today):
    first = today.replace(day=1)
    starts = []
    for _ in range(months):
        starts.append(first)
        first = (first - timedelta(days=1)).replace(day=1)
    return list(reversed(starts))


def _next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _monthly_costs(project, start, end, notes):
    ce_end = costs.ce_end(end.isoformat())
    try:
        client = cost_client()
        compute = costs.cached_cost(client, costs.compute_filter(project), start.isoformat(), ce_end, "MONTHLY")
        total = costs.cached_cost(client, costs.project_filter(project), start.isoformat(), ce_end, "MONTHLY")
    except CostExplorerNotActiveException:
        notes.append("billed-cost-explorer-not-active")
        return None
    except Exception as err:
        logger.warning(f"Cost Explorer query failed: {err}")
        notes.append("billed-unavailable")
        return None
    by_month = {}
    for item in compute:
        by_month.setdefault(item["period"]["start"][:7], [0.0, 0.0])[0] += item["amount"]
    for item in total:
        by_month.setdefault(item["period"]["start"][:7], [0.0, 0.0])[1] += item["amount"]
    return by_month


@team_quota.get("/trend")
@authenticated(ADMINS, group_prefixes=TEAM_PREFIXES)
@validated(params=Trend)
def trend():
    args = Trend.load(request.args)
    scope = resolve_team_scope(args.get("team"))
    cluster_name, region = _target(args)
    months = args.get("months") or DEFAULT_TREND_MONTHS
    project = _project(args, cluster_name)
    today = _today()
    tomorrow = today + timedelta(days=1)
    starts = _month_starts(months, today)
    periods = [(s, min(_next_month(s), tomorrow)) for s in starts]

    sections = [("ASSOC", assoc_cmd())]
    sections += [(f"MONTH_{s:%Y-%m}", jobs_cmd(s.isoformat(), e.isoformat())) for s, e in periods]
    out = run_sections(cluster_name, region, sections)
    known_teams = set(parse_assoc(out.get("ASSOC", [])))

    notes = []
    type_of, prices = job_type_resolver(cluster_name, region, notes)
    billed = _monthly_costs(project, starts[0], tomorrow, notes)
    result = []
    for s, e in periods:
        key = f"{s:%Y-%m}"
        estimates = costs.estimate(parse_sacct_jobs(out.get(f"MONTH_{key}", [])), type_of, prices, s, e)
        names = sorted(known_teams | set(estimates["teams"]))
        team_cost = {name: estimates["teams"].get(name, {}).get("estimate_cost", 0.0) for name in names}
        compute = shared = None
        if billed is not None:
            month_compute, month_total = billed.get(key, [0.0, 0.0])
            compute, shared = round(month_compute, 6), round(max(month_total - month_compute, 0.0), 6)
        allocation = costs.allocate(team_cost, compute, shared)
        rows = []
        for name in names:
            entry = estimates["teams"].get(name) or costs.empty_estimate()
            rows.append({
                "name": name,
                "instance_hours": entry["instance_hours"],
                "gpu_hours": entry["gpu_hours"],
                "estimate_cost": entry["estimate_cost"],
                "compute_cost": allocation["teams"][name]["compute_cost"],
                "shared_cost": allocation["teams"][name]["shared_cost"],
            })
        month = {"month": key, "teams": filter_teams(rows, scope)}
        if is_admin():
            month["totals"] = {"compute_cost": compute, "shared_cost": shared}
        result.append(month)
    return {"months": result, "sources": SOURCES, "notes": notes, "billed_available": billed is not None}


@team_quota.get("/audit")
@authenticated(ADMINS)
@validated(params=AuditQuery)
def audit_log():
    """Audit entries of one month (default: the current one) or of the months between ``from`` and ``to``,
    filtered by actor, team and action. At most 500 rows, newest first."""
    args = AuditQuery.load(request.args)
    config = _lifecycle_config()
    store = datastore.store(config)
    low = args.get("start")
    high = args.get("end")
    if low and high and parse_date(low) > parse_date(high):
        raise ValueError("开始日期不能晚于结束日期")
    if args.get("month"):
        months = [args["month"]]
    elif low or high:
        first = parse_date(low) if low else parse_date(high).replace(day=1)
        last = parse_date(high) if high else _today()
        months, cursor = [], first.replace(day=1)
        while cursor <= last:
            months.append(f"{cursor:%Y-%m}")
            cursor = _next_month(cursor)
        if len(months) > AUDIT_MAX_MONTHS:
            raise ValueError(f"时间范围不能超过 {AUDIT_MAX_MONTHS} 个月")
    else:
        months = [f"{_today():%Y-%m}"]
    between = (low or "0000", (parse_date(high) + timedelta(days=1)).isoformat() if high else "9999")
    actor = (args.get("actor") or "").lower()
    entries, truncated = [], False
    for month in sorted(months, reverse=True):
        for item in store.query(datastore.audit_pk(month), between=between, newest_first=True):
            if actor and actor not in (item.get("actor") or "").lower() \
                    and actor not in (item.get("actor_email") or "").lower():
                continue
            if args.get("team") and item.get("team") != args["team"]:
                continue
            if args.get("action") and item.get("action") != args["action"]:
                continue
            if len(entries) >= AUDIT_MAX_ROWS:
                truncated = True
                break
            entries.append({key: item.get(key) for key in (
                "time", "actor", "actor_email", "action", "cluster", "team", "params", "result", "error")})
        if truncated:
            break
    return {"months": sorted(months), "entries": entries, "truncated": truncated}


# --- write routes (admin only) ----------------------------------------------------------------------------------


def _type_qos_commands(team, current, wanted):
    """(create/modify commands, delete commands) to go from ``current`` to ``wanted`` ({partition: machines})."""
    writes, deletes = [], []
    for partition, nodes in sorted(wanted.items()):
        if nodes > 0 and partition not in current:
            writes.append(create_type_qos_cmd(team, partition, nodes))
        elif nodes > 0 and current[partition] != nodes:
            writes.append(set_type_qos_cmd(team, partition, nodes))
        elif nodes == 0 and partition in current:
            deletes.append(delete_type_qos_cmd(team, partition))
    return writes, deletes


def _check_qos_names(team, partitions):
    for partition in partitions:
        type_qos_name(team, partition)  # ValueError (400) when the QOS name would be invalid or too long


@team_quota.post("/teams")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget, body=CreateTeam)
def create_team():
    cluster_name, region = _target(ClusterTarget.load(request.args))
    body = CreateTeam.load(request.json)
    team = body["name"]
    partitions = catalog.resolve_type_limits(_catalog(cluster_name, region), body["type_limits"])
    _check_qos_names(team, partitions)
    allowed = {p: n for p, n in partitions.items() if n > 0}
    teams, qos = _load_state(cluster_name, region)
    if team in teams or qos_name(team) in qos:
        return _reject("create_team", cluster_name, team, body, 409, f"团队 {team} 或 QOS {qos_name(team)} 已存在")
    config = _optional_config() if body.get("budget_usd") else None
    if body.get("budget_usd") and config is None:
        return _reject("create_team", cluster_name, team, body, 409,
                       "设置预算需要门户数据表（请先部署 pcui-team-lifecycle 堆栈）")
    commands = [create_account_cmd(team, body["fairshare"]), create_std_qos_cmd(team)]
    commands += [create_type_qos_cmd(team, p, n) for p, n in sorted(allowed.items())]
    commands += grant_qos_cmds(team, allowed, with_users=False)
    commands.append(gen_type_quota_argv())
    _write("create_team", cluster_name, region, team, body, commands)
    if config is not None:
        _save_budget(config, team, body["budget_usd"])
    return {"team": team, "result": "created"}, 201


@team_quota.patch("/teams/<team>")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget, body=UpdateTeam)
def update_team(team):
    """Fairshare, per-type machine counts (0 deletes the type's QOS; running jobs keep running), budget and the
    environment storage cap."""
    validate_team_name(team)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    body = UpdateTeam.load(request.json)
    teams, qos = _load_state(cluster_name, region)
    if team not in teams:
        return _reject("update_team", cluster_name, team, body, 404, f"团队 {team} 不存在")
    commands, deletes = [], []
    if body.get("type_limits"):
        cluster_catalog = _catalog(cluster_name, region)
        wanted = catalog.resolve_type_limits(cluster_catalog, body["type_limits"])
        _check_qos_names(team, wanted)
        current = type_qos_limits(team, qos, _partitions_of(cluster_catalog))
        commands, deletes = _type_qos_commands(team, current, wanted)
        final = {**{p: n for p, n in current.items() if n > 0}, **wanted}
        allowed = {p: n for p, n in final.items() if n > 0}
        if qos_name(team) not in qos:
            commands.insert(0, create_std_qos_cmd(team))
        commands += grant_qos_cmds(team, allowed, with_users=bool(teams[team]["members"]))
        commands += deletes + [gen_type_quota_argv()]
    if body.get("fairshare") is not None:
        commands.append(set_fairshare_cmd(team, body["fairshare"]))
    config, record = None, None
    if {"budget_usd", "env_storage_cap_gb"} & set(body):
        config = _lifecycle_config()
        if "env_storage_cap_gb" in body:
            _check_managed(config, cluster_name)
            record = lifecycle_store.get_record(config, team)
            if record is None or record["state"] == "deleted":
                return _reject("update_team", cluster_name, team, body, 409,
                               f"团队 {team} 没有生命周期记录，不能设置环境存储上限")
    if commands:
        _write("update_team", cluster_name, region, team, body, commands)
    if "budget_usd" in body:
        _save_budget(config, team, body["budget_usd"])
    if record is not None:
        record["env_storage_cap_gb"] = body["env_storage_cap_gb"] or 0
        lifecycle_store.put_record(config, record)
    if not commands:
        audit("update_team", cluster_name, team, body, "success")
    return {"team": team, "result": "updated"}, 200


def _lifecycle_config(required=True):
    config = lifecycle_store.load_config()
    if config is None and required:
        raise ApiError(409, "团队生命周期尚未配置（请先部署 pcui-team-lifecycle 堆栈）")
    return config


def _check_managed(config, cluster_name):
    if cluster_name not in config["clusters"]:
        raise ClusterNotManaged(f"集群 {cluster_name} 不由团队生命周期管理")


def _members_from_body(config, members):
    result = []
    for member in members or []:
        email, user = normalize_email(member["email"], config["email_domains"])
        result.append({"email": email, "user": user, "link_existing": bool(member.get("link_existing"))})
    if len({m["user"] for m in result}) != len(result):
        raise ValueError("成员邮箱（及其用户名）不能重复")
    return result


def _pending_steps():
    return [{"id": step, "status": lifecycle.PENDING} for step in lifecycle.STEPS]


def _safe_aws(action, cluster_name, team, params, func, *args, **kwargs):
    """Run an AWS call; a ClientError becomes an audited 400 with the AWS error code and message."""
    try:
        return func(*args, **kwargs), None
    except ClientError as err:
        message = lifecycle._client_error_message(err)
        audit(action, cluster_name, team, params, "failure", error=message)
        return None, _error(400, message)


@team_quota.errorhandler(ApiError)
def handle_api_error(err):
    return _error(err.code, err.message)


@team_quota.errorhandler(ClusterNotManaged)
def handle_cluster_not_managed(err):
    return _error(400, str(err))


@team_quota.errorhandler(lifecycle_store.LifecycleConfigError)
def handle_lifecycle_config_error(err):
    logger.error(str(err), extra=dict(status=400, exception=type(err)))
    return _error(400, str(err))


# --- team lifecycle (admin only) --------------------------------------------------------------------------------


@team_quota.get("/lifecycle/teams")
@authenticated(ADMINS)
@validated(params=ClusterTarget)
def list_lifecycle_teams():
    cluster_name, _region = _target(ClusterTarget.load(request.args))
    config = _lifecycle_config(required=False)
    if config is None:
        return {"configured": False, "records": [], "email_domains": []}
    _check_managed(config, cluster_name)
    return {
        "configured": True,
        "records": [lifecycle_store.public_view(r) for r in lifecycle_store.list_records(config)],
        "email_domains": config["email_domains"],
    }


@team_quota.get("/lifecycle/teams/<team>")
@authenticated(ADMINS)
@validated(params=ClusterTarget)
def get_lifecycle_team(team):
    validate_team_name(team)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    config = _lifecycle_config()
    _check_managed(config, cluster_name)
    record = lifecycle_store.get_record(config, team)
    if record is None:
        return _error(404, f"团队 {team} 没有生命周期记录")
    steps, _ctx = lifecycle.probe(config, record, cluster_name, region, lifecycle.clients(config))
    return {"team": team, "state": record["state"], "steps": lifecycle.step_list(steps)}


@team_quota.post("/lifecycle/teams")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget, body=CreateLifecycleTeam)
def create_lifecycle_team():
    cluster_name, region = _target(ClusterTarget.load(request.args))
    body = CreateLifecycleTeam.load(request.json)
    team = body["name"]
    config = _lifecycle_config()
    _check_managed(config, cluster_name)
    members = _members_from_body(config, body.get("members"))
    lead = normalize_email(body["lead"], config["email_domains"])[0] if body.get("lead") else None
    params = {"fairshare": body["fairshare"], "type_limits": body["type_limits"],
              "budget_usd": body.get("budget_usd"), "env_storage_cap_gb": body.get("env_storage_cap_gb"),
              "members": [m["user"] for m in members], "lead": bool(lead)}
    partitions = catalog.resolve_type_limits(_catalog(cluster_name, region), body["type_limits"])
    _check_qos_names(team, partitions)
    previous = lifecycle_store.get_record(config, team)
    if previous and previous["state"] != "deleted":
        return _reject("lifecycle_create", cluster_name, team, params, 409,
                       f"团队 {team} 已处于 {previous['state']} 状态")
    teams, qos = _load_state(cluster_name, region)
    if team in teams or qos_name(team) in qos:
        return _reject("lifecycle_create", cluster_name, team, params, 409,
                       f"团队 {team} 或 QOS {qos_name(team)} 已存在")
    spec = {"fairshare": body["fairshare"], "type_limits": dict(body["type_limits"]), "members": members,
            "lead": lead}
    record = lifecycle_store.new_record(config, team, spec, previous=previous)
    record["env_storage_cap_gb"] = body.get("env_storage_cap_gb") or 0
    lifecycle_store.put_record(config, record)
    if body.get("budget_usd"):
        _save_budget(config, team, body["budget_usd"])
    audit("lifecycle_create", cluster_name, team, params, "success")
    return {"team": team, "state": record["state"], "steps": _pending_steps()}, 202


@team_quota.post("/lifecycle/teams/<team>/advance")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget, body=Advance)
def advance_lifecycle_team(team):
    validate_team_name(team)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    config = _lifecycle_config()
    _check_managed(config, cluster_name)
    record = lifecycle_store.get_record(config, team)
    if record is None:
        return _error(404, f"团队 {team} 没有生命周期记录")
    if record["state"] == "deleted":
        return _error(409, f"团队 {team} 已删除，请重新创建")
    return lifecycle.advance(config, record, cluster_name, region, lifecycle.clients(config))


@team_quota.delete("/teams/<team>")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget)
def delete_team(team):
    """Remove the Slurm objects (member associations, account, QOS) and the PCUI groups of the team.

    The team directory and its environments, the ECR repository, AD groups and users, secrets and PCUI users are
    kept and listed.
    """
    validate_team_name(team)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    teams, qos = _load_state(cluster_name, region)
    if team not in teams:
        return _reject("delete_team", cluster_name, team, {}, 404, f"团队 {team} 不存在")
    config = _lifecycle_config(required=False)
    if config is not None:
        _check_managed(config, cluster_name)
    record = lifecycle_store.get_record(config, team) if config else None
    users = [member["user"] for member in teams[team]["members"]]
    type_partitions = sorted(type_qos_limits(team, qos, _partitions_of(_catalog(cluster_name, region))))
    commands = [remove_member_cmd(team, user) for user in users] + [delete_account_cmd(team)]
    if qos_name(team) in qos:
        commands.append(delete_qos_cmd(team))
    commands += [delete_type_qos_cmd(team, p) for p in type_partitions]
    commands.append(gen_type_quota_argv())
    _write("delete_team", cluster_name, region, team, {"members": users}, commands)
    removed = [f"slurm-user:{user}" for user in users] + ["slurm-account"]
    if qos_name(team) in qos:
        removed.append("slurm-qos")
    removed += [f"slurm-qos:{type_qos_name(team, p)}" for p in type_partitions]
    cognito_users = set()
    if config is not None:
        c = lifecycle.clients(config)
        groups = (record["cognito_group"], record["member_group"]) if record else (
            f"{TEAM_GROUP_PREFIX}{team}", f"{lifecycle_store.MEMBER_GROUP_PREFIX}{team}")
        for group in groups:
            members, error = _safe_aws("delete_team", cluster_name, team, {"group": group},
                                       lifecycle.cognito_group_users, c, group)
            if error:
                return error
            cognito_users |= set(members or {})
            deleted, error = _safe_aws("delete_team", cluster_name, team, {"group": group},
                                       lifecycle.delete_cognito_group, c, group)
            if error:
                return error
            if deleted:
                removed.append(f"cognito-group:{group}")
    retained = {}
    if record is not None:
        cognito_users |= set(record["cognito_users_created"])
        retained = {
            "ad_group": record["ad_group"],
            "ad_users": sorted(set(record["ad_users_created"]) | set(users)),
            "team_path": record["team_path"],
            "ecr_repo": record["ecr_repo"],
            "secrets": sorted(set(record["ad_secrets"]) | set(record["pcui_secrets"])),
            "cognito_users": sorted(cognito_users),
        }
        record["state"] = "deleted"
        record["deleted_at"] = lifecycle_store._now()
        record["spec"] = None
        lifecycle_store.put_record(config, record)
    elif cognito_users:
        retained = {"cognito_users": sorted(cognito_users)}
    audit("delete_team", cluster_name, team, {"removed": removed}, "success")
    return {"team": team, "result": "deleted", "removed": removed, "retained": retained}, 200


def _member_partitions(cluster_name, region, team, qos):
    return _allowed_partitions(team, qos, _catalog(cluster_name, region))


def _add_member_legacy(cluster_name, region, team, user, teams, qos):
    params = {"user": user}
    if any(member["user"] == user for member in teams[team]["members"]):
        return _reject("add_member", cluster_name, team, params, 409, f"用户 {user} 已是团队 {team} 的成员")
    _write("add_member", cluster_name, region, team, params,
           [add_member_cmd(team, user, _member_partitions(cluster_name, region, team, qos))])
    return {"team": team, "user": user, "result": "added"}, 201


@team_quota.post("/teams/<team>/members")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget, body=AddMember)
def add_member(team):
    """Add a member. With ``email`` (lifecycle-managed team): AD user (created if missing, collision rule),
    initial AD password, PCUI user and ``member-<team>`` group, then the Slurm association. Idempotent."""
    validate_team_name(team)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    body = AddMember.load(request.json)
    params = {"user": body.get("user") or normalize_email(body["email"])[1]}
    teams, qos = _load_state(cluster_name, region)
    if team not in teams:
        return _reject("add_member", cluster_name, team, params, 404, f"团队 {team} 不存在")
    if qos_name(team) not in qos:
        return _reject("add_member", cluster_name, team, params, 409, f"QOS {qos_name(team)} 不存在")
    config = _lifecycle_config(required=False)
    if config is not None:
        _check_managed(config, cluster_name)
    record = lifecycle_store.get_record(config, team) if config else None
    if body.get("user"):
        if record is not None:
            return _reject("add_member", cluster_name, team, params, 400,
                           f"团队 {team} 由团队生命周期管理，请用邮箱添加成员")
        return _add_member_legacy(cluster_name, region, team, body["user"], teams, qos)
    if record is None or record["state"] != "active":
        return _reject("add_member", cluster_name, team, params, 409,
                       f"团队 {team} 没有有效的生命周期记录，请用用户名添加成员")
    email, user = normalize_email(body["email"], config["email_domains"])
    params = {"user": user, "link_existing": body.get("link_existing", False)}
    member = {"email": email, "user": user, "link_existing": bool(body.get("link_existing"))}
    c = lifecycle.clients(config)
    steps = []
    try:
        lookups = lifecycle.ad_lookup(config, cluster_name, region, record["ad_group"], [user])
        lifecycle.ensure_ad_members(config, c, record, cluster_name, region, [member], lookups)
        steps.append({"id": "ad", "status": lifecycle.DONE})
        lifecycle.ensure_cognito_group(c, record["member_group"], f"PCUI member of {team}")
        created = lifecycle.ensure_cognito_user(config, c, record, email, user)
        c.cognito.admin_add_user_to_group(UserPoolId=lifecycle.user_pool_id(), Username=email,
                                          GroupName=record["member_group"])
        steps.append({"id": "cognito", "status": lifecycle.DONE, "message": created})
    except lifecycle.LifecycleStepError as err:
        return _reject("add_member", cluster_name, team, params, err.status, err.message)
    except SlurmCommandError as err:
        return _reject("add_member", cluster_name, team, params, 400, err.message)
    except ClientError as err:
        return _reject("add_member", cluster_name, team, params, 400, lifecycle._client_error_message(err))
    if any(m["user"] == user for m in teams[team]["members"]):
        steps.append({"id": "slurm", "status": lifecycle.DONE, "message": "exists"})
        audit("add_member", cluster_name, team, params, "success")
    else:
        _write("add_member", cluster_name, region, team, params,
               [add_member_cmd(team, user, _member_partitions(cluster_name, region, team, qos))])
        steps.append({"id": "slurm", "status": lifecycle.DONE})
    return {"team": team, "user": user, "email": email, "result": "added", "steps": steps}, 201


@team_quota.delete("/teams/<team>/members/<user>")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget)
def remove_member(team, user):
    """Remove the Slurm association and, for lifecycle teams, the AD group and ``member-<team>`` memberships.
    AD and PCUI users are kept."""
    validate_team_name(team)
    validate_user_name(user)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    params = {"user": user}
    teams, _qos = _load_state(cluster_name, region)
    if team not in teams:
        return _reject("remove_member", cluster_name, team, params, 404, f"团队 {team} 不存在")
    if not any(member["user"] == user for member in teams[team]["members"]):
        return _reject("remove_member", cluster_name, team, params, 404, f"用户 {user} 不是团队 {team} 的成员")
    _write("remove_member", cluster_name, region, team, params, [remove_member_cmd(team, user)])
    steps = [{"id": "slurm", "status": lifecycle.DONE}]
    config = _lifecycle_config(required=False)
    record = lifecycle_store.get_record(config, team) if config and cluster_name in config["clusters"] else None
    if record is not None and record["state"] == "active":
        try:
            ad_out = run_command(cluster_name, region, ad_command(config, record["ad_group"], [f"remove:{user}"]))
            steps.append({"id": "ad", "status": lifecycle.DONE if "@@AD error" not in ad_out else lifecycle.FAILED})
            c = lifecycle.clients(config)
            for email, username in (lifecycle.cognito_group_users(c, record["member_group"]) or {}).items():
                if email.split("@", 1)[0] == user:
                    c.cognito.admin_remove_user_from_group(UserPoolId=lifecycle.user_pool_id(), Username=username,
                                                           GroupName=record["member_group"])
            steps.append({"id": "cognito", "status": lifecycle.DONE})
        except SlurmCommandError as err:
            return _reject("remove_member", cluster_name, team, params, 400, err.message)
        except ClientError as err:
            return _reject("remove_member", cluster_name, team, params, 400, lifecycle._client_error_message(err))
        audit("remove_member_groups", cluster_name, team, params, "success")
    return {"team": team, "user": user, "result": "removed", "steps": steps}, 200


# --- offboarding (admin only) -----------------------------------------------------------------------------------


def _lifecycle_member(cluster_name, team, user):
    config = _lifecycle_config()
    _check_managed(config, cluster_name)
    record = lifecycle_store.get_record(config, team)
    if record is None or record["state"] != "active":
        raise ApiError(409, f"团队 {team} 没有有效的生命周期记录")
    return config, record


def _cognito_username(c, record, user):
    """(email, Cognito username) of the member ``user`` in ``member-<team>``, or (None, None)."""
    for email, username in (lifecycle.cognito_group_users(c, record["member_group"]) or {}).items():
        if email.split("@", 1)[0] == user:
            return email, username
    return None, None


def _team_lead_user(c, record):
    leads = sorted(lifecycle.cognito_group_users(c, record["cognito_group"]) or {})
    return leads[0].split("@", 1)[0] if leads else None


def _ad_op(config, cluster_name, region, record, op):
    parsed = parse_ad_output(run_command(cluster_name, region, ad_command(config, record["ad_group"], [op])))
    if parsed["error"]:
        raise lifecycle.LifecycleStepError(lifecycle._ad_error(parsed))
    return parsed


@team_quota.post("/teams/<team>/members/<user>/offboard")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget, body=Offboard)
def offboard_member(team, user):
    """Disable the PCUI and AD logins of a member, stop new jobs and hand the member's environments to the lead.

    ``jobs=cancel`` cancels the member's jobs in the team and removes the Slurm association now; ``jobs=finish``
    blocks new jobs (MaxSubmitJobs=0) and the scheduler removes the association once the jobs ended. Data is kept.
    """
    validate_team_name(team)
    validate_user_name(user)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    body = Offboard.load(request.json)
    params = {"user": user, "jobs": body["jobs"]}
    teams, _qos = _load_state(cluster_name, region)
    if team not in teams:
        return _reject("offboard_member", cluster_name, team, params, 404, f"团队 {team} 不存在")
    config, record = _lifecycle_member(cluster_name, team, user)
    c = lifecycle.clients(config)
    steps = []
    try:
        email, username = _cognito_username(c, record, user)
        if username is None:
            return _reject("offboard_member", cluster_name, team, params, 404,
                           f"用户 {user} 不是团队 {team} 的门户成员")
        c.cognito.admin_disable_user(UserPoolId=lifecycle.user_pool_id(), Username=username)
        c.cognito.admin_user_global_sign_out(UserPoolId=lifecycle.user_pool_id(), Username=username)
        steps.append({"id": "cognito", "status": lifecycle.DONE})
        _ad_op(config, cluster_name, region, record, f"disable:{user}")
        steps.append({"id": "ad", "status": lifecycle.DONE})
        in_slurm = any(m["user"] == user for m in teams[team]["members"])
        if not in_slurm:
            steps.append({"id": "slurm", "status": lifecycle.DONE, "message": "没有 Slurm 关联"})
        elif body["jobs"] == "cancel":
            run_writes(cluster_name, region, [cancel_user_jobs_cmd(team, user), remove_member_cmd(team, user)])
            steps.append({"id": "slurm", "status": lifecycle.DONE, "message": "作业已取消，Slurm 关联已删除"})
        else:
            run_writes(cluster_name, region, [hold_member_cmd(team, user)])
            steps.append({"id": "slurm", "status": lifecycle.IN_PROGRESS,
                          "message": "已禁止提交新作业；正在运行的作业结束后删除 Slurm 关联"})
        lead = _team_lead_user(c, record)
        if lead and lead != user:
            moved = parse_env_owner(run_command(cluster_name, region, env_owner_command(f"{lifecycle_store.envs_root(config)}/{team}", user, lead)))
            steps.append({"id": "envs", "status": lifecycle.DONE, "message": f"{moved or 0} 个环境已转给 {lead}"})
        else:
            steps.append({"id": "envs", "status": lifecycle.DONE, "message": "没有团队负责人，环境所有者不变"})
    except lifecycle.LifecycleStepError as err:
        return _reject("offboard_member", cluster_name, team, params, err.status, err.message)
    except SlurmCommandError as err:
        return _reject("offboard_member", cluster_name, team, params, 400, err.message)
    except ClientError as err:
        return _reject("offboard_member", cluster_name, team, params, 400, lifecycle._client_error_message(err))
    state = "finishing" if body["jobs"] == "finish" and in_slurm else "offboarded"
    item = {"team": team, "user": user, "mode": body["jobs"], "state": state, "lead": lead, "at": _now_iso(),
            "cluster": cluster_name}
    store = datastore.store(config)
    store.put({"pk": datastore.user_pk(user), "sk": datastore.offboard_sk(team), **item})
    store.put({"pk": datastore.SYS_PK, "sk": datastore.offboard_index_sk(team, user), **item})
    audit("offboard_member", cluster_name, team, {**params, "email": email, "lead": lead}, "success")
    return {"team": team, "user": user, "state": state, "steps": steps}, 200


@team_quota.post("/teams/<team>/members/<user>/reenable")
@authenticated(ADMINS)
@csrf_needed
@validated(params=ClusterTarget, body=Reenable)
def reenable_member(team, user):
    """Undo an offboarding: PCUI and AD logins enabled, Slurm association restored with the team QOS list."""
    validate_team_name(team)
    validate_user_name(user)
    cluster_name, region = _target(ClusterTarget.load(request.args))
    params = {"user": user}
    teams, qos = _load_state(cluster_name, region)
    if team not in teams:
        return _reject("reenable_member", cluster_name, team, params, 404, f"团队 {team} 不存在")
    config, record = _lifecycle_member(cluster_name, team, user)
    c = lifecycle.clients(config)
    steps = []
    try:
        _email, username = _cognito_username(c, record, user)
        if username is None:
            return _reject("reenable_member", cluster_name, team, params, 404,
                           f"用户 {user} 不是团队 {team} 的门户成员")
        c.cognito.admin_enable_user(UserPoolId=lifecycle.user_pool_id(), Username=username)
        steps.append({"id": "cognito", "status": lifecycle.DONE})
        _ad_op(config, cluster_name, region, record, f"enable:{user}")
        steps.append({"id": "ad", "status": lifecycle.DONE})
        if any(m["user"] == user for m in teams[team]["members"]):
            run_writes(cluster_name, region, [release_member_cmd(team, user)])
        else:
            run_writes(cluster_name, region,
                       [add_member_cmd(team, user, _member_partitions(cluster_name, region, team, qos))])
        steps.append({"id": "slurm", "status": lifecycle.DONE})
    except lifecycle.LifecycleStepError as err:
        return _reject("reenable_member", cluster_name, team, params, err.status, err.message)
    except SlurmCommandError as err:
        return _reject("reenable_member", cluster_name, team, params, 400, err.message)
    except ClientError as err:
        return _reject("reenable_member", cluster_name, team, params, 400, lifecycle._client_error_message(err))
    store = datastore.store(config)
    store.delete(datastore.user_pk(user), datastore.offboard_sk(team))
    store.delete(datastore.SYS_PK, datastore.offboard_index_sk(team, user))
    audit("reenable_member", cluster_name, team, params, "success")
    return {"team": team, "user": user, "state": "active", "steps": steps}, 200
