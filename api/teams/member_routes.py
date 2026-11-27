# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Business-user self-service portal (``/team-quota/member/*``), for callers in a ``member-<team>`` group.

* The cluster user is derived from the verified ID token email (see ``member_identity``), never from the request.
* Jobs, logs, files, submit, resubmit and cancel run on the LOGIN node as that user (``member_exec``); there is no
  fallback to the head node. Team-wide figures (quota per instance type, machines in use, node states for pending
  reasons, usage) are read on the head node, because ``PrivateData`` hides other users' jobs from members; they
  are aggregated for the member's own team only and never include other users' job details.
* Templates, notifications and preferences live in the portal data table, keyed by the member's user name.
"""
import base64
import binascii
import gzip
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from botocore.exceptions import BotoCoreError, ClientError
from flask import request

import api.PclusterApiHandler as handler
from api.PclusterApiHandler import authenticated
from api.pcm_globals import logger
from api.security.csrf.csrf import csrf_needed
from api.teams import catalog, datastore, envs, job_alerts, lifecycle_store, notify
from api.teams.audit import audit
from api.teams.member_commands import (
    MEMBER_SCRIPT,
    cancel_argv,
    encode_script,
    env_path,
    history_argv,
    job_argv,
    jobs_argv,
    list_argv,
    parse_history,
    parse_job_detail,
    parse_list,
    parse_read,
    parse_resubmit,
    parse_squeue,
    parse_submit,
    read_argv,
    resubmit_argv,
    sbatch_args,
    submit_argv,
)
from api.teams.member_exec import MemberCommandError, check_result, run_as_member
from api.teams.member_identity import MEMBER_GROUP_PREFIX, member_identity, resolve_member_team
from api.teams.reasons import capacity_status, explain_pending
from api.teams.routes import ADMINS, _error, _teams_view, _usage_view, team_quota
from api.teams.scope import TEAM_GROUP_PREFIX, caller_claims, caller_teams, is_admin
from api.teams.slurm import (
    parse_qos,
    parse_queue,
    parse_sinfo,
    parse_sinfo_ice,
    qos_cmd,
    queue_cmd,
    queue_usage,
    run_sections,
    sinfo_cmd,
    type_qos_limits,
)
from api.teams.validation import (
    BORROW_MAX_MINUTES,
    ENV_TEXT_MAX_BYTES,
    NODES_MAX,
    TEMPLATES_MAX,
    UUID_HEX_RE,
    CancelJob,
    ClusterRegion,
    EnvAction,
    EnvCreate,
    EnvVisibility,
    MemberDir,
    MemberFile,
    MemberHistory,
    MemberQuery,
    MemberUsage,
    NotificationsRead,
    Preferences,
    SubmitJob,
    Template,
    TemplatePatch,
    validate_env_name,
    validate_env_version,
    validate_job_id,
    validate_team_name,
)
from api.validation import validated

MEMBERS = set()
MEMBER_PREFIXES = (MEMBER_GROUP_PREFIX,)
# Environment routes: members (create, retry, delete own, change the visibility of names they created), team leads
# (list, delete, change visibility) and admins (all but create).
ENV_PREFIXES = (MEMBER_GROUP_PREFIX, TEAM_GROUP_PREFIX)


@team_quota.errorhandler(MemberCommandError)
def handle_member_error(err):
    return _error(err.status, err.message)


def _config():
    config = lifecycle_store.load_config()
    if config is None:
        raise MemberCommandError(503, "成员门户尚未配置，请联系管理员")
    return config


def _context(args, team=None):
    config = _config()
    identity = member_identity(config)
    team = resolve_member_team(identity, team if team is not None else args.get("team"))
    cluster_name = args["cluster_name"]
    if cluster_name not in config["clusters"]:
        raise MemberCommandError(400, f"集群 {cluster_name} 未开放给成员门户")
    return SimpleNamespace(
        config=config, identity=identity, user=identity["user"], team=team,
        record=identity["records"][team], cluster_name=cluster_name,
        region=args.get("region") or config["region"] or handler.REGION,
    )


def _identity():
    config = _config()
    identity = member_identity(config)
    return SimpleNamespace(config=config, identity=identity, user=identity["user"], email=identity["email"])


def _run(ctx, argv):
    code, stdout, stderr = run_as_member(ctx.config, ctx.cluster_name, ctx.region, ctx.user, MEMBER_SCRIPT, argv)
    return check_result(code, stdout, stderr)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _month():
    return f"{datetime.now(timezone.utc):%Y-%m}"


def _catalog(ctx):
    return catalog.instance_catalog(ctx.cluster_name, ctx.region)


def _team_state(ctx, cluster_catalog, with_nodes=False):
    """Team-wide quota state on the head node: {limits: {partition: n}, usage: {partition: ...}, nodes, ice}."""
    sections = [("QOS", qos_cmd()), ("QUEUE", queue_cmd())]
    if with_nodes:
        sections.append(("SINFO", sinfo_cmd()))
    out = run_sections(ctx.cluster_name, ctx.region, sections)
    partitions = [entry["partition"] for entry in cluster_catalog["types"]]
    return {
        "limits": type_qos_limits(ctx.team, parse_qos(out.get("QOS", [])), partitions),
        "usage": queue_usage(parse_queue(out.get("QUEUE", []))).get(ctx.team, {}),
        "nodes": parse_sinfo(out.get("SINFO", [])),
        "ice": parse_sinfo_ice(out.get("SINFO", [])),
    }


def _banner(config, team):
    try:
        return notify.budget_banner(datastore.store(config), team, _month())
    except Exception as err:  # the banner is informative; never fail the page because of it
        logger.warning(f"Budget banner unavailable: {type(err).__name__}")
        return None


def _shared_root(ctx):
    return lifecycle_store.shared_root(ctx.config)


def _envs_root(ctx):
    return lifecycle_store.envs_root(ctx.config)


@team_quota.get("/member/me")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=ClusterRegion)
def member_me():
    config = _config()
    identity = member_identity(config)
    return {
        "email": identity["email"],
        "user": identity["user"],
        "teams": [{"team": t, "team_path": identity["records"][t]["team_path"]} for t in identity["teams"]],
        "clusters": [{"name": name, "region": config["region"]} for name in config["clusters"]],
    }


@team_quota.get("/member/options")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberQuery)
def member_options():
    """Instance types of the cluster with their specs (GPU model and memory, vCPU, memory), the team's machine
    count, machines in use and remaining, ``has_quota`` and the capacity ``status`` (available 有空闲 / startable
    需启动约5分钟 / no_capacity 暂无容量 / busy 全部在用，需排队; null when unknown)."""
    ctx = _context(MemberQuery.load(request.args))
    cluster_catalog = _catalog(ctx)
    state = _team_state(ctx, cluster_catalog, with_nodes=True)
    types = []
    for entry in cluster_catalog["types"]:
        partition = entry["partition"]
        limit = state["limits"].get(partition, 0)
        running = state["usage"].get(partition, {}).get("running", 0)
        ice = state["ice"].get(partition) if partition in state["ice"] else None
        types.append({**entry, "limit": limit, "running": running, "remaining": max(limit - running, 0),
                      "has_quota": limit > 0, "borrow_allowed": True,
                      "status": capacity_status(state["nodes"].get(partition), ice)})
    try:  # runtime environments for the dropdown (own team, then 所有人 of others); the form works without them
        listing = envs.parse_list(envs.run_member(ctx, envs.list_argv(_envs_root(ctx), ctx.team)))
        env_options, env_error = envs.dropdown(listing["envs"], ctx.team), None
    except MemberCommandError as err:
        env_options, env_error = [], err.message
    return {
        "envs": env_options,
        "envs_error": env_error,
        "types": types,
        "unmanaged_queues": cluster_catalog["unmanaged_queues"],
        "limits": {"nodes_max": NODES_MAX, "borrow_max_minutes": BORROW_MAX_MINUTES},
        "team": ctx.team,
        "team_root": ctx.record["team_path"],
        "shared_root": _shared_root(ctx),
        "envs_root": _envs_root(ctx),
        "budget": _banner(ctx.config, ctx.team),
    }


def _explain(ctx, jobs):
    """Replace the plain pending reason with a contextual one (quota full / no EC2 capacity), best effort."""
    if not any(job.get("state") == "PENDING" for job in jobs):
        return jobs
    try:
        cluster_catalog = _catalog(ctx)
        state = _team_state(ctx, cluster_catalog, with_nodes=True)
    except Exception as err:
        logger.warning(f"Pending reason context unavailable: {type(err).__name__}")
        return jobs
    types = {entry["partition"]: entry["instance_type"] for entry in cluster_catalog["types"]}
    context = {
        "types": types,
        "limits": state["limits"],
        "running": {p: u.get("running", 0) for p, u in state["usage"].items()},
        "nodes": state["nodes"],
        "ice": state["ice"],
    }
    for job in jobs:
        job["instance_type"] = types.get(job.get("partition"))
        if job.get("state") == "PENDING":
            job["reason_text"] = explain_pending(job.get("reason"), job.get("partition"), job.get("qos"), context)
    return jobs


def _alerts(ctx, jobs):
    """``alerts`` of each job from the scheduler's ALERT# state: idle (疑似空闲), long_run (已运行超24小时),
    no_capacity (暂无容量). Best effort: without the data table the list is empty."""
    try:
        items = datastore.store(ctx.config).query(datastore.user_pk(ctx.user), prefix=job_alerts.STATE_PREFIX)
    except Exception as err:
        logger.warning(f"Job alerts unavailable: {type(err).__name__}")
        items = []
    by_job = {item["sk"][len(job_alerts.STATE_PREFIX):]: item for item in items}
    for job in jobs:
        job["alerts"] = job_alerts.active_alerts(by_job.get(job.get("job_id")), job.get("state"))
    return jobs


@team_quota.get("/member/jobs")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberQuery)
def member_jobs():
    ctx = _context(MemberQuery.load(request.args))
    return {"jobs": _alerts(ctx, _explain(ctx, parse_squeue(_run(ctx, jobs_argv()))))}


@team_quota.get("/member/history")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberHistory)
def member_history():
    args = MemberHistory.load(request.args)
    ctx = _context(args)
    return {"jobs": parse_history(_run(ctx, history_argv(args["start"], args["end"])))}


@team_quota.get("/member/jobs/<job_id>")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberQuery)
def member_job(job_id):
    validate_job_id(job_id)
    ctx = _context(MemberQuery.load(request.args))
    job = parse_job_detail(_run(ctx, job_argv(job_id)))
    if job.get("user") != ctx.user:  # the script already enforces this; checked again here
        raise MemberCommandError(403, "只能查看自己的作业")
    _explain(ctx, [job])
    _alerts(ctx, [job])
    word = (job.get("state") or "").split(" ")[0].upper()
    output = _output_state(ctx, job, word)
    return {**job, **output, "end_is_projected": word not in FINAL_STATES, "team_root": ctx.record["team_path"]}


FINAL_STATES = frozenset({"COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL",
                          "PREEMPTED", "DEADLINE", "REVOKED", "SPECIAL_EXIT"})
NOT_STARTED_STATES = frozenset({"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "RESV_DEL_HOLD"})
ZERO_ELAPSED = frozenset({None, "0:00", "00:00", "00:00:00", "0"})


def _deleted_env(ctx, job):
    """{by, at, team, name, version} when the job is an environment build (``env-<name>``) whose output lies in a
    version directory that was deleted (tombstone ``TEAM#<t> | ENVDEL#<name>#<version>``), else None."""
    name = job.get("name") or ""
    root = re.escape(_envs_root(ctx).rstrip("/"))
    match = re.match(root + r"/([a-z][a-z0-9_-]{0,31})/([a-z][a-z0-9-]{0,31})/([0-9A-Za-z][0-9A-Za-z._-]{0,31})/",
                     job.get("stdout_path") or "")
    if not name.startswith("env-") or not match or match.group(2) != name[len("env-"):]:
        return None
    team, env_name, version = match.groups()
    try:
        item = datastore.store(ctx.config).get(datastore.team_pk(team), datastore.env_deleted_sk(env_name, version))
    except Exception as err:  # informative only
        logger.warning(f"Deleted-environment lookup failed: {type(err).__name__}")
        return None
    if not item:
        return None
    return {"by": item.get("by"), "at": item.get("at"), "team": team, "name": env_name, "version": version}


def _output_state(ctx, job, word):
    """``output_state`` of the job's standard output for the detail page:

    * ``not_started``: pending or machines starting (the file appears once the job runs, about 5 minutes);
    * ``ok``: the file exists (checked on the login node as the member);
    * ``env_deleted``: an environment build whose environment version was deleted (``env_deleted`` {by, at});
    * ``never_started``: the job ended without running (e.g. cancelled while pending), so there is no output;
    * ``missing``: the job ran but the file is absent or not readable (work directory not writable, output path
      does not exist, file removed).
    """
    exists = job.pop("stdout_exists", None)
    if word in NOT_STARTED_STATES:
        return {"output_state": "not_started"}
    if exists:
        return {"output_state": "ok"}
    deleted = _deleted_env(ctx, job)
    if deleted:
        return {"output_state": "env_deleted", "env_deleted": deleted}
    ran = job.get("start") is not None and not (job.get("source") == "scontrol"
                                                 and job.get("elapsed") in ZERO_ELAPSED)
    if word in FINAL_STATES and not ran:
        return {"output_state": "never_started"}
    return {"output_state": "missing"}


@team_quota.get("/member/jobs/<job_id>/resubmit")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberQuery)
def member_resubmit(job_id):
    """A prefilled submit body for one of the member's own jobs (instance type, machines, GPUs, time limit,
    environment, script)."""
    validate_job_id(job_id)
    ctx = _context(MemberQuery.load(request.args))
    cluster_catalog = _catalog(ctx)
    return parse_resubmit(_run(ctx, resubmit_argv(job_id, ctx.record["team_path"])),
                          catalog.by_partition(cluster_catalog))


@team_quota.get("/member/files")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberFile)
def member_file():
    args = MemberFile.load(request.args)
    ctx = _context(args)
    params = {"path": args["path"], "mode": args["mode"], "offset": args["offset"], "limit": args["limit"]}
    try:  # the team directory, $HOME and the team's environments (build logs)
        out = _run(ctx, read_argv(args["path"], args["mode"], args["offset"], args["limit"], ctx.record["team_path"],
                                  f"{_envs_root(ctx)}/{ctx.team}"))
    except MemberCommandError as err:
        audit("member_read_file", ctx.cluster_name, ctx.team, params, "rejected", error=err.message)
        raise
    audit("member_read_file", ctx.cluster_name, ctx.team, params, "success")
    return parse_read(out, args["limit"])


@team_quota.get("/member/dir")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberDir)
def member_dir():
    args = MemberDir.load(request.args)
    ctx = _context(args)
    return parse_list(_run(ctx, list_argv(args["path"], ctx.record["team_path"])))


@team_quota.post("/member/jobs")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@csrf_needed
@validated(params=MemberQuery, body=SubmitJob)
def member_submit():
    args = MemberQuery.load(request.args)
    body = SubmitJob.load(request.json)
    ctx = _context(args, team=body.get("team") or args.get("team"))
    entry = catalog.by_type(_catalog(ctx)).get(body["instance_type"])
    options = sbatch_args(body, ctx.team, entry)
    team_root, shared_root, envs_root = ctx.record["team_path"], _shared_root(ctx), _envs_root(ctx)
    env_dir = env_path(body["env"], ctx.team, envs_root) if body.get("env") else None
    params = {key: body.get(key) for key in ("instance_type", "nodes", "gpus_per_node", "cpus", "mem_gb",
                                             "time_minutes", "borrow", "job_name", "env")}
    params["partition"] = entry["partition"]
    token = None
    if body.get("script_content"):
        token, digest, size = encode_script(body["script_content"])
        params.update({"script_sha256": digest, "script_bytes": size})
    else:
        params["script_path"] = body["script_path"]
    workdir = body.get("workdir") or team_root
    params["workdir"] = workdir
    argv = submit_argv(team_root, workdir, body.get("script_path"), token, uuid.uuid4().hex, options,
                       env_dir, shared_root if env_dir else None, envs_root if env_dir else None)
    try:
        result = parse_submit(_run(ctx, argv))
    except MemberCommandError as err:
        audit("member_submit", ctx.cluster_name, ctx.team, params, "failure", error=err.message)
        raise
    audit("member_submit", ctx.cluster_name, ctx.team, {**params, "job_id": result["job_id"]}, "success")
    return result, 201


@team_quota.post("/member/jobs/<job_id>/cancel")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@csrf_needed
@validated(params=MemberQuery, body=CancelJob)
def member_cancel(job_id):
    validate_job_id(job_id)
    ctx = _context(MemberQuery.load(request.args))
    try:
        _run(ctx, cancel_argv(job_id))
    except MemberCommandError as err:
        audit("member_cancel", ctx.cluster_name, ctx.team, {"job_id": job_id}, "failure", error=err.message)
        raise
    audit("member_cancel", ctx.cluster_name, ctx.team, {"job_id": job_id}, "success")
    return {"job_id": job_id, "result": "cancelled"}


@team_quota.get("/member/team")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberUsage)
def member_team():
    args = MemberUsage.load(request.args)
    ctx = _context(args)
    scope = [ctx.team]
    teams_view = _teams_view(ctx.cluster_name, ctx.region, scope)
    usage = _usage_view(args, ctx.cluster_name, ctx.region, scope, ctx.cluster_name, user=ctx.user)
    quota = next(iter(teams_view["teams"]), None)
    if quota is not None:  # members see the team's figures, not who holds the machines
        quota = {**quota, "running_by_type": {k: {**v, "users": {}} for k, v in quota["running_by_type"].items()},
                 "members": []}
    return {
        "team": ctx.team,
        "quota": quota,
        "catalog": teams_view["catalog"],
        "usage": next(iter(usage["teams"]), None),
        "mine": usage["mine"],
        "period": usage["period"],
        "billed_available": usage["billed_available"],
        "sources": usage["sources"],
        "notes": usage["notes"],
        "budget": _banner(ctx.config, ctx.team),
    }


@team_quota.get("/member/budget")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=MemberQuery)
def member_budget():
    """Budget banner of the member's team: month-to-date estimate vs. budget (``level`` warning at 80 %,
    exceeded at 100 %), or ``budget: null`` without a budget."""
    ctx = _context(MemberQuery.load(request.args))
    return {"team": ctx.team, "budget": _banner(ctx.config, ctx.team)}


# --- templates --------------------------------------------------------------------------------------------------


def _template_sk(template_id):
    if not isinstance(template_id, str) or UUID_HEX_RE.fullmatch(template_id) is None:
        raise MemberCommandError(404, "模板不存在")
    return f"TPL#{template_id}"


def _template_view(item):
    return {"id": item["sk"][len("TPL#"):], "name": item.get("name"), "job": item.get("job") or {},
            "created_at": item.get("created_at"), "updated_at": item.get("updated_at")}


def _template_params(template_id, job):
    return {"template_id": template_id, "instance_type": job.get("instance_type"),
            "script": "content" if job.get("script_content") else "path"}


@team_quota.get("/member/templates")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=ClusterRegion)
def member_templates():
    me = _identity()
    items = datastore.store(me.config).query(datastore.user_pk(me.user), prefix="TPL#")
    return {"templates": sorted((_template_view(i) for i in items), key=lambda t: (t["name"] or "").lower()),
            "max": TEMPLATES_MAX}


@team_quota.post("/member/templates")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@csrf_needed
@validated(params=ClusterRegion, body=Template)
def member_template_create():
    me = _identity()
    body = Template.load(request.json)
    store = datastore.store(me.config)
    if len(store.query(datastore.user_pk(me.user), prefix="TPL#")) >= TEMPLATES_MAX:
        audit("member_template_create", None, None, {"name": body["name"]}, "rejected",
              error=f"at most {TEMPLATES_MAX} templates")
        raise MemberCommandError(409, f"最多只能保存 {TEMPLATES_MAX} 个模板，请先删除一个")
    template_id, now = uuid.uuid4().hex, _now_iso()
    item = {"pk": datastore.user_pk(me.user), "sk": f"TPL#{template_id}", "name": body["name"],
            "job": request.json["job"], "created_at": now, "updated_at": now}
    store.put(item)
    audit("member_template_create", None, None, {"name": body["name"], **_template_params(template_id, body["job"])},
          "success")
    return _template_view(item), 201


@team_quota.patch("/member/templates/<template_id>")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@csrf_needed
@validated(params=ClusterRegion, body=TemplatePatch)
def member_template_update(template_id):
    me = _identity()
    sk = _template_sk(template_id)
    body = TemplatePatch.load(request.json)
    store = datastore.store(me.config)
    item = store.get(datastore.user_pk(me.user), sk)
    if item is None:
        raise MemberCommandError(404, "模板不存在")
    if "name" in body:
        item["name"] = body["name"]
    if "job" in body:
        item["job"] = request.json["job"]
    item["updated_at"] = _now_iso()
    store.put(item)
    audit("member_template_update", None, None, {"name": item["name"], **_template_params(template_id, item["job"])},
          "success")
    return _template_view(item)


@team_quota.delete("/member/templates/<template_id>")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@csrf_needed
@validated(params=ClusterRegion)
def member_template_delete(template_id):
    me = _identity()
    sk = _template_sk(template_id)
    store = datastore.store(me.config)
    if store.get(datastore.user_pk(me.user), sk) is None:
        raise MemberCommandError(404, "模板不存在")
    store.delete(datastore.user_pk(me.user), sk)
    audit("member_template_delete", None, None, {"template_id": template_id}, "success")
    return {"id": template_id, "result": "deleted"}


# --- notifications and preferences ------------------------------------------------------------------------------


@team_quota.get("/member/notifications")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=ClusterRegion)
def member_notifications():
    me = _identity()
    return notify.list_notifications(datastore.store(me.config), me.user)


@team_quota.post("/member/notifications/read")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@csrf_needed
@validated(params=ClusterRegion, body=NotificationsRead)
def member_notifications_read():
    me = _identity()
    notify.mark_read(datastore.store(me.config), me.user, _now_iso())
    return {"result": "read"}


@team_quota.get("/member/preferences")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@validated(params=ClusterRegion)
def member_preferences():
    me = _identity()
    prefs = notify.get_prefs(datastore.store(me.config), me.user)
    return notify.public_prefs(me.config, prefs, me.email)


@team_quota.put("/member/preferences")
@authenticated(MEMBERS, group_prefixes=MEMBER_PREFIXES)
@csrf_needed
@validated(params=ClusterRegion, body=Preferences)
def member_preferences_update():
    """Job-end notifications in the portal (``job_notify``) and by e-mail (``email_jobs``), budget alerts by e-mail
    (``email_budget``). E-mail goes to the verified login address; SNS asks the user to confirm it once."""
    me = _identity()
    body = Preferences.load(request.json)
    store = datastore.store(me.config)
    try:
        prefs = notify.update_preferences(me.config, store, me.user, me.email, body)
    except Exception as err:
        audit("member_preferences", None, None, body, "failure", error=type(err).__name__)
        raise
    audit("member_preferences", None, None, {**body, "subscribed": bool(prefs.get("subscription_arn"))}, "success")
    return notify.public_prefs(me.config, prefs, me.email)



# --- runtime environments ---------------------------------------------------------------------------------------


def _team_record(config, team):
    record = lifecycle_store.get_record(config, team)
    return record if record and record.get("state") == "active" and record.get("team_path") else None


def _env_access(args, team=None):
    """Who is calling for which team: member (runs as their cluster user), team lead and/or admin.

    Members of ``member-<team>`` work on the login node as themselves; leads (``team-<team>``) and admins who are
    not members of the team read through the head node and may delete or change visibility but not build.
    """
    config = _config()
    cluster_name = args["cluster_name"]
    if cluster_name not in config["clusters"]:
        raise MemberCommandError(400, f"集群 {cluster_name} 未开放给成员门户")
    try:
        identity = member_identity(config)
    except MemberCommandError:
        identity = None
    member_teams = identity["teams"] if identity else []
    lead_teams, admin = caller_teams(), is_admin()
    if not team:
        candidates = sorted(set(member_teams) | set(lead_teams))
        if len(candidates) != 1:
            raise MemberCommandError(400, "请选择团队")
        team = candidates[0]
    validate_team_name(team)
    member, lead = team in member_teams, team in lead_teams
    if not (member or lead or admin):
        raise MemberCommandError(403, "无权访问这个团队的环境")
    record = identity["records"][team] if member else _team_record(config, team)
    if record is None:
        raise MemberCommandError(404, f"团队 {team} 不存在")
    user = identity["user"] if member else None
    claims = caller_claims()
    actor = user or claims.get("username") or claims.get("email") or "admin"
    return SimpleNamespace(config=config, cluster_name=cluster_name,
                           region=args.get("region") or config["region"] or handler.REGION, team=team,
                           record=record, admin=admin, lead=lead, member_user=user, user=user, actor=actor)


def _env_names(scope, team, name, version):
    if scope not in ("team", "shared"):
        raise MemberCommandError(404, "未找到")
    return validate_team_name(team), validate_env_name(name), validate_env_version(version)


def _cap_gb(access):
    return (access.record or {}).get("env_storage_cap_gb") or 0


def _env_listing(access):
    """Own team's environments and the 所有人 environments of other teams: as the member on the login node (the
    kernel decides what is readable) or, for leads and admins, as root on the head node (the name mode decides)."""
    if access.member_user:
        values = envs.run_member(access, envs.list_argv(_envs_root(access), access.team))
    else:
        values = envs.run_root(access, envs.root_argv("list", _envs_root(access), access.team))
    return envs.parse_list(values)


def _env_ref(team, name, version):
    return f"team/{team}/{name}/{version}"


def _start_codebuild(access, name, version, source, values, image_ref=None, dockerfile=None):
    """StartBuild for a version whose env.json the member just wrote; the build id goes back into env.json."""
    ids = (envs.first(values, "IDS") or "").split()
    envs_root = _envs_root(access)
    try:
        if len(ids) != 2:
            raise ValueError("The cluster did not return the owner and group ids")
        build_id = envs.start_build(access.config, access.team, name, version, source, ids[0], ids[1],
                                    image_ref=image_ref, dockerfile=dockerfile)
    except (ClientError, BotoCoreError, ValueError) as err:
        code = err.response.get("Error", {}).get("Code", "") if isinstance(err, ClientError) else type(err).__name__
        failure = f"Could not start the CodeBuild build ({re.sub(r'[^A-Za-z0-9]', '', code)[:60] or 'error'})"
        try:
            envs.run_member(access, envs.mark_argv(envs_root, access.team, name, version, failure=failure))
        except MemberCommandError as mark_err:
            logger.warning(f"Could not mark the environment failed: {mark_err.message}")
        raise MemberCommandError(502, envs.failure_text(failure)) from None
    try:
        envs.run_member(access, envs.mark_argv(envs_root, access.team, name, version, build_id=build_id))
    except MemberCommandError as err:  # the build writes its id into env.json itself when it starts
        logger.warning(f"Could not record the CodeBuild build id: {err.message}")
    return build_id


@team_quota.get("/member/envs")
@authenticated(ADMINS, group_prefixes=ENV_PREFIXES)
@validated(params=MemberQuery)
def member_envs():
    """One list: the team's environments and the 所有人 environments of other teams (``team`` field), with
    visibility, status, size, the team's storage use against its cap, what the caller may do with each entry (and
    why not), and the teams present for the filter."""
    access = _env_access(MemberQuery.load(request.args), request.args.get("team"))
    listing = _env_listing(access)
    own = [e for e in listing["envs"] if e.get("team") == access.team]
    envs.merge_build_status(access.config, own)
    owned = envs.owned_names(own, access.team, access.member_user)
    use = envs.usage(own, _cap_gb(access))
    others = sorted({e.get("team") for e in listing["envs"] if e.get("team") and e.get("team") != access.team})
    return {
        "team": access.team,
        "envs": [envs.entry_view(e, access, owned) for e in listing["envs"]],
        "teams": [access.team] + others,
        "truncated": listing["truncated"],
        "usage": use,
        "warning": envs.usage_warning(use),
        "roles": {"member": access.member_user is not None, "lead": access.lead, "admin": access.admin},
        "limits": {"spec_max_bytes": ENV_TEXT_MAX_BYTES, "dockerfile_max_bytes": envs.DOCKERFILE_B64_MAX * 3 // 4,
                   "registries": list(envs.PUBLIC_REGISTRIES),
                   "team_repository": f"{access.config['account_id']}.dkr.ecr.{access.config['region']}.amazonaws.com/"
                                      f"{lifecycle_store.ecr_repo_name(access.config, access.team)}"},
    }


@team_quota.post("/member/envs")
@authenticated(ADMINS, group_prefixes=ENV_PREFIXES)
@csrf_needed
@validated(params=MemberQuery, body=EnvCreate)
def member_env_create():
    """New environment version: registry import or conda env (Slurm job of the member on the build queue), or
    private ECR image / Dockerfile (CodeBuild). Root first prepares the name directory with the chosen visibility
    (仅本团队 ``team`` / 所有人 ``all``; an existing name keeps its visibility), then the member creates the version.
    409 when the version exists or the team's storage cap is reached."""
    args = MemberQuery.load(request.args)
    body = EnvCreate.load(request.json)
    access = _env_access(args, body.get("team") or args.get("team"))
    params = {"name": body["name"], "version": body["version"], "source": body["source"],
              "image": body.get("image"), "visibility": body["visibility"]}
    if not access.member_user:
        audit("env_create", access.cluster_name, access.team, params, "rejected", error="not a team member")
        raise MemberCommandError(403, "只有有集群账号的团队成员可以创建环境")
    envs_root = _envs_root(access)
    try:
        meta, spec_name, spec_text, builder, image = envs.plan_create(body, access.config, access.team,
                                                                     access.member_user, _now_iso())
        if spec_text is not None:
            _, digest, size = envs._token(spec_text, "specification")
            params.update({"spec": spec_name, "spec_sha256": digest, "spec_bytes": size})
        argv = envs.create_argv(envs_root, _shared_root(access), access.team, body["name"], body["version"], meta,
                                spec_name, spec_text, _cap_gb(access) * envs.CAP_UNIT, builder)
        prepare = envs.root_argv("prepare", envs_root, access.record["team_path"], access.team, body["name"],
                                 body["visibility"])
    except (ValueError, MemberCommandError) as err:
        audit("env_create", access.cluster_name, access.team, params, "rejected",
              error=getattr(err, "message", str(err)))
        raise
    params["builder"] = builder
    try:
        prepared = envs.run_root(access, prepare)
        envs.result_of(prepared, "prepared")
        visibility = envs.first(prepared, "VISIBILITY")
        params["visibility_applied"] = visibility
        values = envs.run_member(access, argv)
        envs.result_of(values, "created")
        build_id = None
        if builder == "codebuild":
            build_id = _start_codebuild(access, body["name"], body["version"], "ecr" if image else "dockerfile",
                                        values, image_ref=image["image_ref"] if image else None,
                                        dockerfile=body.get("dockerfile"))
    except MemberCommandError as err:
        audit("env_create", access.cluster_name, access.team, params,
              "rejected" if err.status in (403, 409, 413) else "failure", error=err.message)
        raise
    job_id = envs.first(values, "JOB")
    use = envs.usage([{"size_bytes": int(envs.first(values, "TOTAL") or 0)}], _cap_gb(access))
    audit("env_create", access.cluster_name, access.team, {**params, "job_id": job_id, "build_id": build_id},
          "success")
    path = envs.first(values, "DIR")
    return {
        "env": {"id": _env_ref(access.team, body["name"], body["version"]), "scope": "team", "team": access.team,
                "name": body["name"], "version": body["version"], "kind": meta["kind"], "source": body["source"],
                "builder": builder, "status": "queued", "job_id": job_id, "build_id": build_id, "path": path,
                "visibility": visibility, "log_path": f"{path}/build.log" if path else None},
        "usage": use,
        "warning": envs.usage_warning(use),
    }, 201


@team_quota.post("/member/envs/<scope>/<team>/<name>/<version>/retry")
@authenticated(ADMINS, group_prefixes=ENV_PREFIXES)
@csrf_needed
@validated(params=MemberQuery, body=EnvAction)
def member_env_retry(scope, team, name, version):
    """Build a failed version again in the same directory (attempt counter + 1). Owner only."""
    _env_names(scope, team, name, version)
    if scope != "team":
        raise MemberCommandError(404, "未找到")
    access = _env_access(MemberQuery.load(request.args), team)
    params = {"name": name, "version": version}
    if not access.member_user:
        audit("env_retry", access.cluster_name, team, params, "rejected", error="not a team member")
        raise MemberCommandError(403, "只有环境的创建者可以重新构建")
    try:
        values = envs.run_member(access, envs.retry_argv(_envs_root(access), _shared_root(access), team,
                                                         name, version, _cap_gb(access) * envs.CAP_UNIT))
        envs.result_of(values, "retried")
        build_id = None
        if envs.first(values, "BUILDER") == "codebuild":
            build_id = _retry_codebuild(access, name, version, values)
    except (MemberCommandError, ValueError) as err:
        audit("env_retry", access.cluster_name, team, params, "failure", error=getattr(err, "message", str(err)))
        raise
    job_id = envs.first(values, "JOB")
    audit("env_retry", access.cluster_name, team, {**params, "job_id": job_id, "build_id": build_id}, "success")
    return {"id": _env_ref(team, name, version), "result": "queued", "job_id": job_id, "build_id": build_id}, 202


def _retry_codebuild(access, name, version, values):
    source = envs.first(values, "SOURCE")
    if source == "dockerfile":
        try:
            dockerfile = gzip.decompress(base64.b64decode(envs.first(values, "SPEC") or "", validate=True))
            dockerfile = dockerfile.decode("utf-8")
        except (binascii.Error, OSError, EOFError, ValueError):
            dockerfile = None
        if not dockerfile:
            return _retry_rejected(access, name, version, "The stored Dockerfile is missing or unreadable")
        return _start_codebuild(access, name, version, "dockerfile", values, dockerfile=dockerfile)
    if source == "image":
        try:  # env.json is writable by its owner: the image reference is validated again
            image = envs.parse_image_ref(envs.first(values, "IMAGE") or "", access.config, access.team)
        except (ValueError, MemberCommandError):
            image = None
        if not image or not image["private"]:
            return _retry_rejected(access, name, version, "The stored image reference is not the team repository")
        return _start_codebuild(access, name, version, "ecr", values, image_ref=image["image_ref"])
    return _retry_rejected(access, name, version, "Unknown build source")


def _retry_rejected(access, name, version, failure):
    try:
        envs.run_member(access, envs.mark_argv(_envs_root(access), access.team, name, version, failure=failure))
    except MemberCommandError as err:
        logger.warning(f"Could not mark the environment failed: {err.message}")
    raise MemberCommandError(409, failure)


def _tombstone(access, team, name, version):
    """Remember who deleted a version and when (job details explain a missing output under a deleted env)."""
    try:
        datastore.store(access.config).put({
            "pk": datastore.team_pk(team), "sk": datastore.env_deleted_sk(name, version), "by": access.actor,
            "at": _now_iso(), "ttl": datastore.ttl_after(datastore.ENVDEL_TTL_DAYS)})
    except Exception as err:  # the deletion itself succeeded; the tombstone is informative
        logger.warning(f"Could not record the deleted environment: {type(err).__name__}")


@team_quota.delete("/member/envs/<scope>/<team>/<name>/<version>")
@authenticated(ADMINS, group_prefixes=ENV_PREFIXES)
@csrf_needed
@validated(params=MemberQuery)
def member_env_delete(scope, team, name, version):
    """Delete a version (its creator, a lead of the team or an admin). Runs as root on the head node; running
    builds are cancelled; the last version of a name takes the name (and its visibility) with it."""
    _env_names(scope, team, name, version)
    if scope != "team":  # published copies no longer exist
        raise MemberCommandError(404, "未找到")
    access = _env_access(MemberQuery.load(request.args), team)
    params = {"name": name, "version": version}
    owner = None if (access.admin or access.lead) else access.member_user
    try:
        values = envs.run_root(access, envs.root_argv("delete", _envs_root(access), team, name, version, owner))
        envs.result_of(values, "deleted")
    except MemberCommandError as err:
        audit("env_delete", access.cluster_name, team, params, "rejected" if err.status < 500 else "failure",
              error=err.message)
        raise
    for build_id in values.get("BUILD", []):
        envs.stop_build(access.config, build_id, team, name, version)
    _tombstone(access, team, name, version)
    audit("env_delete", access.cluster_name, team, {**params, "as": "owner" if owner else "manager"}, "success")
    return {"id": _env_ref(team, name, version), "result": "deleted"}


@team_quota.put("/member/envs/team/<team>/<name>/visibility")
@authenticated(ADMINS, group_prefixes=ENV_PREFIXES)
@csrf_needed
@validated(params=MemberQuery, body=EnvVisibility)
def member_env_visibility(team, name):
    """Change who can use every version of an environment name: 仅本团队 (``team``) or 所有人 (``all``). Allowed for
    a member who created a version of the name (checked as root on the head node), a lead of the team and admins."""
    validate_team_name(team), validate_env_name(name)
    body = EnvVisibility.load(request.json)
    access = _env_access(MemberQuery.load(request.args), team)
    role = "admin" if access.admin else ("lead" if access.lead else "creator")
    actor = access.member_user if role == "creator" else None
    params = {"name": name, "new": body["visibility"], "as": role}
    try:
        values = envs.run_root(access, envs.root_argv("visibility", _envs_root(access), team, name,
                                                      body["visibility"], actor))
        envs.result_of(values, "changed")
    except MemberCommandError as err:
        if getattr(err, "result", None) == "not_owner":
            err = MemberCommandError(403, "只有这个环境的创建者、团队负责人或管理员可以更改可见范围")
        audit("env_visibility", access.cluster_name, team, params, "rejected" if err.status < 500 else "failure",
              error=err.message)
        raise err from None
    old = envs.first(values, "OLD")
    audit("env_visibility", access.cluster_name, team, {"name": name, "old": old, "new": body["visibility"],
                                                        "as": role}, "success")
    return {"team": team, "name": name, "visibility": body["visibility"], "old": old}
