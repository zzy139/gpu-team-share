# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Slurm command builders, output parsers and the head-node runner.

Builders return argv lists made only of constants and validated tokens. They
are joined with ``shlex.quote`` before being sent through ``ssm_command``, so a
request value can never be interpreted by the shell.

Team quotas are per instance type (one Slurm partition per instance type):

* QOS ``<team>-std`` is the association default with ``GrpTRES=node=0`` (fail closed);
* QOS ``<team>-<partition>`` (``Priority=100 GrpTRES=node=<machines>``) for every allowed instance type;
* QOS ``borrow`` and ``envbuild`` are granted to every team;
* ``job_submit.lua`` maps a job on partition ``p`` to QOS ``<account>-<p>`` (or keeps ``borrow``) using the
  allow file written by ``gen-type-quota.sh``, which PCUI runs after every quota write.
"""
import re
import shlex

from api.PclusterApiHandler import ssm_command
from api.teams import cluster
from api.teams.validation import (
    BORROW_QOS,
    ENVBUILD_QOS,
    FAIRSHARE_MAX,
    FAIRSHARE_MIN,
    QOS_NAME_MAX,
    QOS_SUFFIX,
    TYPE_LIMIT_MAX,
    parse_date,
    validate_int,
    validate_partition,
    validate_team_name,
    validate_user_name,
)

SLURM_BIN = "/opt/slurm/bin"
SACCTMGR = f"{SLURM_BIN}/sacctmgr"
SACCT = f"{SLURM_BIN}/sacct"
SQUEUE = f"{SLURM_BIN}/squeue"
SINFO = f"{SLURM_BIN}/sinfo"
SCANCEL = f"{SLURM_BIN}/scancel"
GEN_TYPE_QUOTA = "/opt/slurm/etc/gen-type-quota.sh"
TYPE_QUOTA_CONF = "/opt/slurm/etc/hpc-type-quota.conf"

NON_TEAM_ACCOUNTS = frozenset({"root", "pcdefault"})
SECTION_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
SECTION_MARKER = "@@"
QOS_PRIORITY = 100

# Runs the allow-file generator when it is installed (clusters built before per-type quotas do not have it).
GEN_TYPE_QUOTA_SCRIPT = r"""if [ -x "$1" ]; then "$1" >/dev/null; fi"""
# Prints the allow file, or nothing when it does not exist yet.
TYPE_QUOTA_READ_SCRIPT = r"""if [ -r "$1" ]; then cat -- "$1"; fi"""


class SlurmCommandError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def qos_name(team):
    return f"{validate_team_name(team)}{QOS_SUFFIX}"


def type_qos_name(team, partition):
    name = f"{validate_team_name(team)}-{validate_partition(partition)}"
    if partition == QOS_SUFFIX[1:] or len(name) > QOS_NAME_MAX:
        raise ValueError(f"QOS name '{name}' is invalid or longer than {QOS_NAME_MAX} characters")
    return name


def qos_list(team, partitions):
    """Association QOS list: ``<team>-std,borrow,envbuild,<team>-<p>...`` (partitions sorted)."""
    return ",".join([qos_name(team), BORROW_QOS, ENVBUILD_QOS] + [type_qos_name(team, p) for p in sorted(partitions)])


# --- read builders ----------------------------------------------------------------------------------------------


def assoc_cmd():
    return [SACCTMGR, "-n", "-P", "show", "assoc", "format=Account,User,Fairshare,QOS,DefaultQOS"]


def qos_cmd():
    return [SACCTMGR, "-n", "-P", "show", "qos", "format=Name,Priority,GrpTRES,MaxWall"]


def queue_cmd():
    """Running, configuring and pending jobs of every user: account|user|partition|nodes|qos|state."""
    return [SQUEUE, "-a", "-h", "-t", "R,CF,PD", "-o", "%a|%u|%P|%D|%q|%T"]


def team_jobs_cmd(team):
    """Job metadata of one team (lead view). Never the script, the output or any file content."""
    return [SQUEUE, "-a", "-h", "-A", validate_team_name(team), "-o", "%i|%u|%j|%P|%T|%r|%V|%S|%l|%M|%D|%q"]


def sinfo_cmd():
    """Node states per partition: partition|state|count|reason (``down~`` with reason
    ``(Code:InsufficientInstanceCapacity)...`` after an EC2 capacity error)."""
    return [SINFO, "-a", "-h", "-o", "%R|%T|%D|%E"]


def sinfo_nodes_cmd():
    """Like ``sinfo_cmd`` with the node names last: partition|state|count|reason|nodes (admin capacity view)."""
    return [SINFO, "-a", "-h", "-o", "%R|%T|%D|%E|%N"]


def _date_token(value):
    return parse_date(value).isoformat()


def jobs_cmd(start, end):
    return [
        SACCT, "-a", "-X", "-n", "-P", "-S", _date_token(start), "-E", _date_token(end),
        "--format=JobID,Account,User,Partition,NodeList,Start,End,ElapsedRaw,AllocTRES,State",
    ]


def type_quota_read_argv():
    return ["bash", "-c", TYPE_QUOTA_READ_SCRIPT, "pcui-type-quota", TYPE_QUOTA_CONF]


# --- write builders ---------------------------------------------------------------------------------------------


def _nodes(value):
    return validate_int(value, 1, TYPE_LIMIT_MAX, "machines")


def set_fairshare_cmd(team, fairshare):
    validate_int(fairshare, FAIRSHARE_MIN, FAIRSHARE_MAX, "fairshare")
    return [SACCTMGR, "-i", "modify", "account", "where", f"name={validate_team_name(team)}", "set",
            f"Fairshare={fairshare}"]


def create_account_cmd(team, fairshare):
    validate_team_name(team)
    validate_int(fairshare, FAIRSHARE_MIN, FAIRSHARE_MAX, "fairshare")
    return [SACCTMGR, "-i", "add", "account", team, f"Description={team}", "Organization=hpc",
            f"Fairshare={fairshare}"]


def create_std_qos_cmd(team):
    """``<team>-std``: the association default. node=0, so a job that bypasses the lua plugin pends."""
    return [SACCTMGR, "-i", "add", "qos", qos_name(team), f"Priority={QOS_PRIORITY}", "GrpTRES=node=0"]


def create_type_qos_cmd(team, partition, nodes):
    return [SACCTMGR, "-i", "add", "qos", type_qos_name(team, partition), f"Priority={QOS_PRIORITY}",
            f"GrpTRES=node={_nodes(nodes)}"]


def set_type_qos_cmd(team, partition, nodes):
    return [SACCTMGR, "-i", "modify", "qos", "where", f"name={type_qos_name(team, partition)}", "set",
            f"GrpTRES=node={_nodes(nodes)}"]


def delete_type_qos_cmd(team, partition):
    return [SACCTMGR, "-i", "delete", "qos", "where", f"name={type_qos_name(team, partition)}"]


def grant_qos_cmds(team, partitions, with_users=True):
    """Set the QOS list on the account and on every user association of the team (user lists do not inherit)."""
    qos = qos_list(team, partitions)
    commands = [[SACCTMGR, "-i", "modify", "account", "where", f"name={validate_team_name(team)}", "set",
                 f"QOS={qos}", f"DefaultQOS={qos_name(team)}"]]
    if with_users:
        commands.append([SACCTMGR, "-i", "modify", "user", "where", f"account={team}", "set",
                         f"QOS={qos}", f"DefaultQOS={qos_name(team)}"])
    return commands


def gen_type_quota_argv():
    return ["bash", "-c", GEN_TYPE_QUOTA_SCRIPT, "pcui-gen-type-quota", GEN_TYPE_QUOTA]


def delete_account_cmd(team):
    return [SACCTMGR, "-i", "delete", "account", "where", f"name={validate_team_name(team)}"]


def delete_qos_cmd(team):
    return [SACCTMGR, "-i", "delete", "qos", "where", f"name={qos_name(team)}"]


def add_member_cmd(team, user, partitions):
    return [SACCTMGR, "-i", "add", "user", validate_user_name(user), f"Account={validate_team_name(team)}",
            f"QOS={qos_list(team, partitions)}", f"DefaultQOS={qos_name(team)}"]


def remove_member_cmd(team, user):
    return [SACCTMGR, "-i", "delete", "user", "where", f"name={validate_user_name(user)}",
            f"account={validate_team_name(team)}"]


def cancel_user_jobs_cmd(team, user):
    return [SCANCEL, "-u", validate_user_name(user), "-A", validate_team_name(team)]


def hold_member_cmd(team, user):
    """No new jobs for the user in the team (running and pending jobs continue)."""
    return [SACCTMGR, "-i", "modify", "user", "where", f"name={validate_user_name(user)}",
            f"account={validate_team_name(team)}", "set", "MaxSubmitJobs=0"]


def release_member_cmd(team, user):
    return [SACCTMGR, "-i", "modify", "user", "where", f"name={validate_user_name(user)}",
            f"account={validate_team_name(team)}", "set", "MaxSubmitJobs=-1"]


def user_jobs_cmd(team, user):
    return [SQUEUE, "-h", "-u", validate_user_name(user), "-A", validate_team_name(team), "-o", "%i"]


# --- joining ----------------------------------------------------------------------------------------------------


def quote_argv(argv):
    return " ".join(shlex.quote(str(token)) for token in argv)


def join_commands(sections):
    """Build ``echo @@NAME ; <cmd> ; echo @@NAME2 ; <cmd2>`` from (name, argv) pairs. Names are code constants."""
    parts = []
    for name, argv in sections:
        if SECTION_RE.fullmatch(name) is None:
            raise ValueError(f"Invalid section name {name!r}")
        parts.append(quote_argv(["echo", f"{SECTION_MARKER}{name}"]))
        parts.append(quote_argv(argv))
    return " ; ".join(parts)


def chain_commands(commands):
    """Join write commands with ``&&`` so the first failure stops the chain."""
    if not commands:
        raise ValueError("No command to run")
    return " && ".join(quote_argv(argv) for argv in commands)


# --- parsers ----------------------------------------------------------------------------------------------------


def parse_sections(output):
    sections = {}
    current = None
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(SECTION_MARKER) and SECTION_RE.fullmatch(line[len(SECTION_MARKER):]):
            current = line[len(SECTION_MARKER):]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_grp_tres(value):
    result = {}
    for item in (value or "").split(","):
        if "=" not in item:
            continue
        key, val = item.split("=", 1)
        number = _to_int(val)
        result[key.strip()] = number if number is not None else val.strip()
    return result


def parse_assoc(lines):
    """Return {team: {name, fairshare, members: [{user, qos, default_qos}]}} for every non-system account."""
    teams = {}
    for line in lines:
        cols = line.strip().split("|")
        if len(cols) == 6:  # leading Cluster column
            cols = cols[1:]
        if len(cols) != 5:
            continue
        account, user, fairshare, qos, default_qos = (c.strip() for c in cols)
        if not account or account in NON_TEAM_ACCOUNTS:
            continue
        team = teams.setdefault(account, {"name": account, "fairshare": None, "members": []})
        if not user:
            team["fairshare"] = _to_int(fairshare)
        else:
            team["members"].append({
                "user": user,
                "qos": [q for q in qos.split(",") if q],
                "default_qos": default_qos or None,
            })
    for team in teams.values():
        team["members"].sort(key=lambda m: m["user"])
    return teams


def parse_qos(lines):
    result = {}
    for line in lines:
        cols = line.strip().split("|")
        if len(cols) != 4 or not cols[0]:
            continue
        name, priority, grp_tres, max_wall = (c.strip() for c in cols)
        result[name] = {
            "name": name,
            "priority": _to_int(priority),
            "grp_tres": parse_grp_tres(grp_tres),
            "max_wall": max_wall or None,
        }
    return result


def type_qos_limits(team, qos, partitions):
    """{partition: machines} of the ``<team>-<partition>`` QOS that exist, for the given partitions."""
    limits = {}
    for partition in partitions:
        try:
            name = type_qos_name(team, partition)
        except ValueError:
            continue
        info = qos.get(name)
        if info is not None:
            value = info["grp_tres"].get("node")
            limits[partition] = value if isinstance(value, int) else 0
    return limits


def gpus_in_tres(tres):
    """GPU count of an AllocTRES dict: the untyped gres/gpu, or the sum of typed entries when it is absent."""
    if isinstance(tres.get("gres/gpu"), int):
        return tres["gres/gpu"]
    return sum(v for k, v in tres.items() if k.startswith("gres/gpu:") and isinstance(v, int))


RUNNING_STATES = frozenset({"RUNNING", "CONFIGURING", "R", "CF"})
PENDING_STATES = frozenset({"PENDING", "PD"})


def parse_queue(lines):
    """Rows of ``queue_cmd``: [{account, user, partition, nodes, qos, state}] (running/configuring/pending)."""
    rows = []
    for line in lines:
        cols = line.strip().split("|")
        if len(cols) != 6 or not cols[0]:
            continue
        account, user, partition, nodes, qos, state = (c.strip() for c in cols)
        rows.append({"account": account, "user": user, "partition": partition, "nodes": _to_int(nodes) or 0,
                     "qos": qos, "state": state})
    return rows


def queue_usage(rows):
    """{team: {partition: {running, pending, jobs, users: {user: {running, pending}}}}} in machines.

    ``running`` counts the machines of running and configuring jobs, which is what ``GrpTRES=node`` limits.
    Borrowed jobs (QOS ``borrow``) are reported separately under ``borrowed``.
    """
    usage = {}
    for row in rows:
        state = "running" if row["state"] in RUNNING_STATES else "pending" if row["state"] in PENDING_STATES else None
        if state is None or row["account"] in NON_TEAM_ACCOUNTS:
            continue
        entry = usage.setdefault(row["account"], {}).setdefault(
            row["partition"], {"running": 0, "pending": 0, "borrowed": 0, "jobs": 0, "users": {}})
        entry["jobs"] += 1
        if row["qos"] == BORROW_QOS:
            if state == "running":
                entry["borrowed"] += row["nodes"]
        else:
            entry[state] += row["nodes"]
        user = entry["users"].setdefault(row["user"], {"running": 0, "pending": 0})
        user[state] += row["nodes"]
    return usage


ICE_REASON_RE = re.compile(r"InsufficientInstanceCapacity|InsufficientCapacity|_no_capacity|insufficient capacity",
                           re.IGNORECASE)


def _sinfo_rows(lines, with_nodes=False):
    """(partition, state, count, reason, nodes) of ``sinfo_cmd`` / ``sinfo_nodes_cmd`` lines. Old 3-column lines
    (``%R|%T|%D``) are still accepted (no reason). A '|' in the reason belongs to the reason."""
    for line in lines:
        cols = line.strip().split("|")
        if len(cols) < 3 or not cols[0].strip():
            continue
        nodes = ""
        if with_nodes and len(cols) >= 5:
            nodes = cols[-1].strip()
            cols = cols[:-1]
        partition, state, count = (c.strip() for c in cols[:3])
        reason = "|".join(cols[3:]).strip()
        if reason.lower() in ("none", "n/a"):
            reason = ""
        yield partition.rstrip("*"), state, _to_int(count) or 0, reason, nodes


def parse_sinfo(lines):
    """{partition: {state: nodes}} from ``sinfo_cmd``."""
    result = {}
    for partition, state, nodes, _reason, _names in _sinfo_rows(lines):
        bucket = result.setdefault(partition, {})
        bucket[state] = bucket.get(state, 0) + nodes
    return result


def parse_sinfo_ice(lines):
    """{partition: True|False}: whether a node of the partition carries an EC2 insufficient-capacity reason.
    Partitions of 3-column lines (no reason column) are absent: unknown."""
    result = {}
    for line in lines:
        if len(line.strip().split("|")) < 4:
            continue
        for partition, _state, _nodes, reason, _names in _sinfo_rows([line]):
            result[partition] = result.get(partition, False) or bool(ICE_REASON_RE.search(reason))
    return result


def parse_sinfo_nodes(lines):
    """{partition: [{state, count, reason, nodes}]} from ``sinfo_nodes_cmd``."""
    result = {}
    for partition, state, count, reason, names in _sinfo_rows(lines, with_nodes=True):
        result.setdefault(partition, []).append({"state": state, "count": count, "reason": reason or None,
                                                 "nodes": names or None})
    return result


def parse_team_jobs(lines):
    jobs = []
    for line in lines:
        cols = line.rstrip("\n").split("|")
        if len(cols) < 12 or not cols[0].strip():
            continue
        extra = len(cols) - 12  # a '|' in the job name
        name = "|".join(cols[2:3 + extra])
        cols = cols[:2] + [name] + cols[3 + extra:]
        job_id, user, name, partition, state, reason, submit, start, limit, elapsed, nodes, qos = cols
        jobs.append({
            "job_id": job_id.strip(), "user": user, "name": name, "partition": partition, "state": state,
            "reason": reason if reason not in ("", "None") else None, "submit": submit,
            "start": start if start not in ("", "N/A", "None") else None, "time_limit": limit,
            "elapsed": elapsed, "nodes": _to_int(nodes) or 0, "qos": qos,
        })
    return jobs


def parse_sacct_jobs(lines):
    jobs = []
    for line in lines:
        cols = line.strip().split("|")
        if len(cols) != 10 or not cols[0]:
            continue
        job_id, account, user, partition, nodelist, start, end, elapsed, alloc, state = cols
        jobs.append({
            "job_id": job_id,
            "account": account,
            "user": user,
            "partition": partition,
            "nodelist": nodelist,
            "start": start,
            "end": end,
            "elapsed": _to_int(elapsed) or 0,
            "alloc_tres": parse_grp_tres(alloc),
            "state": state,
        })
    return jobs


def parse_type_quota_conf(lines):
    """Parse the allow file: ``{"types": {partition: instance_type}, "multi": [partition],
    "quota": {team: {partition: machines}}}``."""
    result = {"types": {}, "multi": [], "quota": {}}
    for line in lines:
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == "type":
            result["types"][parts[1]] = parts[2]
        elif len(parts) == 2 and parts[0] == "multi":
            result["multi"].append(parts[1])
        elif len(parts) == 4 and parts[0] == "quota" and parts[3].isdigit():
            result["quota"].setdefault(parts[1], {})[parts[2]] = int(parts[3])
    return result


# --- runner -----------------------------------------------------------------------------------------------------


def run_command(cluster_name, region, command):
    instance_id = cluster.head_node(cluster_name, region)
    try:
        return ssm_command(region, instance_id, "root", command)
    except Exception as err:
        raise SlurmCommandError(str(err) or "Slurm command failed on the head node") from None


def run_sections(cluster_name, region, sections):
    return parse_sections(run_command(cluster_name, region, join_commands(sections)))


def run_writes(cluster_name, region, commands):
    return run_command(cluster_name, region, chain_commands(commands))
