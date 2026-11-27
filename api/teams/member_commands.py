# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Member portal script (runs on the login node as the member), argv builders and output parsers.

``MEMBER_SCRIPT`` is a constant; requests only supply validated argv tokens. File access is limited to the
member's team directory and ``$HOME`` after ``realpath`` resolution (``../`` and symlinks that leave those roots
are rejected with exit code 4); Linux permissions of the member apply on top. A pasted job script travels as one
gzip+base64 argv token and is decoded on the node into a file, so it is never interpreted by a shell.

With a runtime environment (``env``), the job runs a generated wrapper: the ``#SBATCH`` lines of the member's
script, then ``srun --container-image=<image.sqsh> ...`` (container, Pyxis) or ``source hpc-activate <prefix>``
(conda) followed by the member's script. Only environments that resolve under ``<envs root>/`` (``/efs/envs``), that
the member can read (another team's environment only when it is 所有人: the kernel decides) and whose ``env.json``
says ``succeeded`` are accepted (exit code 9 otherwise). The job carries
``--comment=env:<scope>/<team>/<name>/<version>`` so history, resubmit and notifications know the environment.
"""
import base64
import binascii
import gzip
import hashlib
import re

from api.teams.member_exec import MemberCommandError
from api.teams.reasons import pending_text
from api.teams.validation import (
    BORROW_QOS,
    BORROW_MAX_MINUTES,
    DATE_RE,
    ENV_SCOPES,
    JOB_ID_RE,
    MEMBER_READ_LIMIT_MAX,
    NODES_MAX,
    SCRIPT_MAX_BYTES,
    TIME_MAX_MINUTES,
    parse_date,
    validate_env_name,
    validate_env_version,
    validate_job_id,
    validate_member_path,
    validate_team_name,
)

SCRIPT_TOKEN_MAX = 45000  # keeps the whole SSM document under the 64 KB limit
HISTORY_MAX_ROWS = 500
DIR_MAX_ENTRIES = 500
UUID_RE = re.compile(r"[0-9a-f]{32}")
OPTION_RE = re.compile(r"--[a-z-]+=[A-Za-z0-9._:/-]{1,128}")
ENV_COMMENT_PREFIX = "env:"
NO_ENV = "-"

MEMBER_SCRIPT = r"""set -euo pipefail
export PATH=/opt/slurm/bin:/usr/local/bin:/usr/bin:/bin
export LC_ALL=C
umask 027
me=$(id -un)
op="$1"; shift

# Succeeds when the resolved path $1 is one of the allowed roots ($2... plus $HOME) or below one of them.
under_roots() {
  local p="$1" r rr; shift
  for r in "$@" "$HOME"; do
    [ -n "$r" ] || continue
    rr=$(realpath -e -- "$r" 2>/dev/null) || continue
    [ "$rr" != "/" ] || continue
    if [ "$p" = "$rr" ] || [[ "$p" == "$rr"/* ]]; then return 0; fi
  done
  return 1
}

resolve() {
  realpath -e -- "$1" 2>/dev/null || exit 5
}

# Resolves $1 and checks it against the roots ($2...) before checking that it exists, so a path that leaves the
# roots (../, a symlink to another team's directory) is always 403 (exit 4), never 404, whether or not it exists.
resolve_in() {
  local path="$1" p; shift
  p=$(realpath -m -- "$path" 2>/dev/null) || exit 4
  under_roots "$p" "$@" || exit 4
  p=$(realpath -e -- "$path" 2>/dev/null) || exit 5
  under_roots "$p" "$@" || exit 4
  printf '%s\n' "$p"
}

cmd_jobs() {
  squeue -u "$me" -h -o '%i|%j|%a|%P|%T|%r|%V|%S|%l|%M|%b|%C|%m|%q' | tail -n 500 | gzip -c | base64 -w0
}

cmd_history() {
  sacct -u "$me" -X -n -P -S "$1" -E "$2" \
    --format=JobID,JobName,Account,Partition,QOS,State,Reason,Submit,Start,End,ElapsedRaw,ExitCode,AllocTRES,WorkDir \
    | tail -n 500 | gzip -c | base64 -w0
}

# Prints "@@STDOUT_EXISTS 0|1": whether the job's standard output file ($1, sbatch patterns %j %u %x %A %a %%
# expanded, relative to the work directory $4) exists, checked as the member (no access counts as missing).
stdout_exists() {
  local p="$1" id="$2" name="$3" wd="$4" base task=""
  local t_pp='%%' t_j='%j' t_u='%u' t_x='%x' t_A='%A' t_a='%a' mark=$'\001'
  shopt -u patsub_replacement 2>/dev/null || true
  if [ -z "$p" ]; then echo "@@STDOUT_EXISTS 0"; return 0; fi
  base="${id%%_*}"
  if [[ "$id" == *_* ]]; then task="${id#*_}"; fi
  p="${p//"$t_pp"/$mark}"; p="${p//"$t_j"/$base}"; p="${p//"$t_u"/$me}"; p="${p//"$t_x"/$name}"
  p="${p//"$t_A"/$base}"; p="${p//"$t_a"/$task}"; p="${p//$mark/%}"
  if [[ "$p" != /* ]]; then p="${wd%/}/$p"; fi
  if [ -e "$p" ]; then echo "@@STDOUT_EXISTS 1"; else echo "@@STDOUT_EXISTS 0"; fi
}

cmd_job() {
  local id="$1" out owner line so
  out=$(scontrol show job -o "$id" 2>/dev/null || true)
  if [ -n "$out" ]; then
    local re='(^|[[:space:]])UserId=([^([:space:]]+)' sre='(^|[[:space:]])StdOut=([^[:space:]]*)'
    if [[ "$out" =~ $re ]]; then owner="${BASH_REMATCH[2]}"; else owner=""; fi
    [ "$owner" = "$me" ] || exit 6
    line=$(printf '%s\n' "$out" | head -n 1)
    echo "@@SCONTROL"
    printf '%s\n' "$line"
    if [[ "$line" =~ $sre ]]; then so="${BASH_REMATCH[2]}"; else so=""; fi
    stdout_exists "$so" "$id" "" "/"
    return 0
  fi
  out=$(sacct -j "$id" -X -n -P \
    --format=JobID,User,JobName,Account,Partition,QOS,State,Reason,Submit,Start,End,ExitCode,AllocTRES,WorkDir,StdOut,StdErr \
    2>/dev/null | head -n 1 || true)
  # PrivateData=jobs hides other users' jobs, so "not visible" is reported as "not yours" (403).
  [ -n "$out" ] || exit 6
  owner=$(printf '%s' "$out" | cut -d'|' -f2)
  [ "$owner" = "$me" ] || exit 6
  echo "@@SACCT"
  printf '%s\n' "$out"
  # a '|' in the job name shifts the columns: StdOut and WorkDir are counted from the end
  stdout_exists "$(printf '%s' "$out" | awk -F'|' '{print $(NF-1)}')" "$id" \
    "$(printf '%s' "$out" | awk -F'|' '{n=$3; for (i = 4; i <= NF - 13; i++) n = n "|" $i; print n}')" \
    "$(printf '%s' "$out" | awk -F'|' '{print $(NF-2)}')"
}

cmd_read() {
  local path="$1" mode="$2" offset="$3" limit="$4" p size start; shift 4
  p=$(resolve_in "$path" "$@")
  [ -f "$p" ] || exit 4
  [ -r "$p" ] || exit 8
  size=$(stat -c %s -- "$p")
  if [ "$mode" = tail ]; then
    start=$(( size > limit ? size - limit : 0 ))
  else
    start="$offset"
  fi
  echo "@@SIZE $size"
  echo "@@OFFSET $start"
  echo "@@PATH $p"
  echo "@@DATA"
  dd if="$p" iflag=skip_bytes,count_bytes skip="$start" count="$limit" status=none | base64 -w0
  echo
}

cmd_list() {
  local path="$1" p; shift
  p=$(resolve_in "$path" "$@")
  [ -d "$p" ] || exit 4
  [ -r "$p" ] && [ -x "$p" ] || exit 8
  echo "@@PATH $p"
  echo "@@DATA"
  { find "$p" -mindepth 1 -maxdepth 1 -printf '%y\t%s\t%T@\t%f\0' 2>/dev/null | head -z -n 501 || true; } \
    | head -c 16384 | base64 -w0
  echo
}

# Resolves a runtime environment directory: it must lie under <envs root>/ and be readable by the member (the
# name directory of another team is 3770 unless it is 所有人), and its env.json must say "succeeded" (exit 9
# otherwise).
env_dir() {
  local d="$1" envs="$2" p er st
  er=$(realpath -e -- "$envs" 2>/dev/null) || exit 9
  p=$(realpath -e -- "$d" 2>/dev/null) || exit 9
  [[ "$p" == "$er/"* ]] || exit 9
  [ -f "$p/env.json" ] && [ -r "$p/env.json" ] || exit 9
  st=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status", ""))' "$p/env.json" \
    2>/dev/null || true)
  [ "$st" = succeeded ] || exit 9
  printf '%s\n' "$p"
}

# Prints the wrapper batch script: the #SBATCH header of the member's script, then the environment set-up and
# the member's script. Every path is quoted with printf %q.
write_wrapper() {
  local ed="$1" script="$2" wd="$3" tr="$4" shared="$5"
  echo '#!/bin/bash'
  awk 'NR == 1 && /^#!/ { next } /^#SBATCH/ { print; next } /^[[:space:]]*(#.*)?$/ { next } { exit }' "$script"
  echo '# Generated by the HPC portal: runs the script below in the selected runtime environment.'
  echo 'set -eo pipefail'
  if [ -f "$ed/image.sqsh" ]; then
    printf 'srun --container-image=%q --container-mounts=%q --container-mount-home --container-workdir=%q /bin/bash %q\n' \
      "$ed/image.sqsh" "$tr:$tr,$shared:$shared:ro" "$wd" "$script"
  elif [ -d "$ed/prefix" ]; then
    printf 'source %q %q\n' "$shared/tools/hpc-activate" "$ed/prefix"
    printf 'exec /bin/bash %q\n' "$script"
  else
    exit 9
  fi
}

cmd_submit() {
  local team_root="$1" workdir="$2" mode="$3" src="$4" uuid="$5" envdir="$6" shared_root="$7" envs_root="$8"
  local tr wd script jobs out run ed shared; shift 8
  tr=$(resolve "$team_root")
  wd=$(resolve_in "$workdir" "$tr")
  [ -d "$wd" ] || exit 4
  jobs="$tr/.pcui-jobs"
  if [ "$mode" = path ]; then
    script=$(resolve_in "$src" "$tr")
    [ -f "$script" ] || exit 4
    [ -r "$script" ] || exit 8
  fi
  if [ "$mode" != path ] || [ "$envdir" != - ]; then
    [ -d "$jobs" ] || mkdir -m 3770 -- "$jobs" 2>/dev/null || [ -d "$jobs" ]
    [ -d "$jobs/$me" ] || mkdir -m 0700 -- "$jobs/$me"
  fi
  if [ "$mode" != path ]; then
    script="$jobs/$me/$uuid.sbatch"
    set -o noclobber
    (umask 077; printf '%s' "$src" | base64 -d | gzip -dc > "$script")
    set +o noclobber
  fi
  run="$script"
  if [ "$envdir" != - ]; then
    shared=$(realpath -e -- "$shared_root" 2>/dev/null) || exit 9
    ed=$(env_dir "$envdir" "$envs_root")
    run="$jobs/$me/$uuid.wrap.sbatch"
    set -o noclobber
    (umask 077; write_wrapper "$ed" "$script" "$wd" "$tr" "$shared" > "$run")
    set +o noclobber
  fi
  out=$(cd -- "$wd" && sbatch --parsable --chdir="$wd" "$@" "$run")
  echo "@@JOB ${out%%;*}"
  echo "@@SCRIPT $script"
}

# Settings of one of the member's own jobs, to submit it again. Free-text fields go on their own marker lines.
# The script content is returned only for scripts the portal stored (<team>/.pcui-jobs/<me>/, at most 64 KiB);
# other scripts are returned as a path when they lie inside the team directory or $HOME.
cmd_resubmit() {
  local id="$1" tr fixed owner sl last p; shift
  tr=$(resolve "$1")
  field() { sacct -j "$id" -X -n -P --format="$1" 2>/dev/null | head -n 1 || true; }
  fixed=$(field JobID,User,Partition,NNodes,Timelimit,QOS,ReqTRES)
  [ -n "$fixed" ] || exit 6
  owner=$(printf '%s' "$fixed" | cut -d'|' -f2)
  [ "$owner" = "$me" ] || exit 6
  echo "@@FIELDS $fixed"
  echo "@@NAME $(field JobName)"
  echo "@@COMMENT $(field Comment)"
  echo "@@WORKDIR $(field WorkDir)"
  sl=$(field SubmitLine)
  last="${sl##* }"
  case "$last" in *.wrap.sbatch) last="${last%.wrap.sbatch}.sbatch" ;; esac
  p=$(realpath -e -- "$last" 2>/dev/null || true)
  if [ -n "$p" ] && [[ "$p" == "$tr/.pcui-jobs/$me/"* ]] && [ -f "$p" ] && [ -r "$p" ] \
      && [ "$(stat -c %s -- "$p")" -le 65536 ]; then
    echo "@@CONTENT $(gzip -c -- "$p" | base64 -w0)"
  elif [ -n "$p" ] && [ -f "$p" ] && under_roots "$p" "$tr"; then
    echo "@@PATH $p"
  fi
}

cmd_cancel() {
  local id="$1" owner
  owner=$(squeue -h -j "$id" -o %u 2>/dev/null | head -n 1 || true)
  [ -n "$owner" ] || exit 6
  [ "$owner" = "$me" ] || exit 6
  scancel -- "$id"
  echo "@@CANCELLED $id"
}

case "$op" in
  jobs) cmd_jobs "$@" ;;
  history) cmd_history "$@" ;;
  job) cmd_job "$@" ;;
  read) cmd_read "$@" ;;
  list) cmd_list "$@" ;;
  submit) cmd_submit "$@" ;;
  resubmit) cmd_resubmit "$@" ;;
  cancel) cmd_cancel "$@" ;;
  *) echo "unknown operation" >&2; exit 2 ;;
esac
"""


# --- argv builders (all values validated here, again, before they become argv tokens) --------------------------


def _date(value):
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ValueError("Invalid date")
    return parse_date(value).isoformat()


def _int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError("Invalid number")
    return str(value)


def _root(path):
    validate_member_path(path)
    if ".." in path.split("/"):
        raise ValueError("Invalid team root")
    return path


def jobs_argv():
    return ["jobs"]


def history_argv(start, end):
    return ["history", _date(start), _date(end)]


def job_argv(job_id):
    return ["job", validate_job_id(job_id)]


def read_argv(path, mode, offset, limit, team_root, *extra_roots):
    """``extra_roots``: further readable roots, e.g. the team's environment directory (build logs)."""
    if mode not in ("tail", "offset"):
        raise ValueError("Invalid read mode")
    return ["read", validate_member_path(path), mode, _int(offset, 0, 2 ** 53), _int(limit, 1, MEMBER_READ_LIMIT_MAX),
            _root(team_root), *[_root(r) for r in extra_roots]]


def list_argv(path, team_root):
    return ["list", validate_member_path(path), _root(team_root)]


def cancel_argv(job_id):
    return ["cancel", validate_job_id(job_id)]


def encode_script(content):
    """Return ``(token, sha256, nbytes)``: the gzip+base64 token of a pasted script, its hash and size."""
    if not isinstance(content, str):
        raise ValueError("Invalid script")
    data = content.encode("utf-8")
    if len(data) > SCRIPT_MAX_BYTES:
        raise ValueError(f"Script must not exceed {SCRIPT_MAX_BYTES} bytes")
    if b"\x00" in data:
        raise ValueError("Script must not contain NUL characters")
    token = base64.b64encode(gzip.compress(data, mtime=0)).decode("ascii")
    if len(token) > SCRIPT_TOKEN_MAX:
        raise MemberCommandError(413, "脚本过大，请改用脚本路径")
    return token, hashlib.sha256(data).hexdigest(), len(data)


def env_comment(env):
    """``env:<scope>/<team>/<name>/<version>`` for ``--comment``."""
    if env["scope"] not in ENV_SCOPES:
        raise ValueError("Invalid environment scope")
    return (f"{ENV_COMMENT_PREFIX}{env['scope']}/{validate_team_name(env['team'])}/{validate_env_name(env['name'])}/"
            f"{validate_env_version(env['version'])}")


def parse_env_comment(comment):
    """The ``env`` dict of a job comment written by ``env_comment``, or None."""
    match = re.fullmatch(r"env:(team|shared)/([a-z][a-z0-9_-]{0,31})/([a-z][a-z0-9-]{0,31})/"
                         r"([0-9A-Za-z][0-9A-Za-z._-]{0,31})", (comment or "").strip())
    if not match:
        return None
    return {"scope": match.group(1), "team": match.group(2), "name": match.group(3), "version": match.group(4)}


def env_path(env, team, envs_root):
    """Directory ``<envs root>/<env team>/<name>/<version>`` of a runtime environment, for scope ``team`` and the old
    scope ``shared`` alike. ``team`` is the caller's team; whether the caller may use another team's environment
    is decided on the login node by the kernel (only 所有人 environments are readable), see ``env_dir``."""
    validate_team_name(team)
    _root(envs_root)
    if env["scope"] not in ENV_SCOPES:
        raise ValueError("Invalid environment scope")
    env_team = validate_team_name(env["team"])
    name, version = validate_env_name(env["name"]), validate_env_version(env["version"])
    return f"{envs_root}/{env_team}/{name}/{version}"


def sbatch_args(body, team, entry):
    """sbatch options from a validated ``SubmitJob`` body. ``entry`` is the catalog entry of the instance type
    ({instance_type, partition, gpus, vcpus, memory_gib}); the partition is always derived from it.

    Defaults use whole machines (GPU queues allocate them exclusively): all GPUs, ``--mem=0`` (all memory).
    ``--qos`` is ``<team>-<partition>`` or ``borrow``; ``job_submit.lua`` enforces the same on the head node.
    Time: own-quota jobs get ``--time`` only when the member gave one (otherwise the partition limit applies,
    UNLIMITED); borrowed jobs always get ``--time=min(given or 240, 240)`` (never rejected for their time).
    """
    validate_team_name(team)
    if entry is None:
        raise ValueError(f"机型 {body['instance_type']} 在此集群上不可用")
    partition, gpus = entry["partition"], entry["gpus"]
    gpus_per_node = body.get("gpus_per_node")
    if gpus_per_node is None:
        gpus_per_node = gpus
    if gpus_per_node > gpus:
        raise ValueError(f"{entry['instance_type']} 每台只有 {gpus} 块 GPU")
    if body.get("cpus") and entry.get("vcpus") and body["cpus"] > entry["vcpus"]:
        raise ValueError(f"{entry['instance_type']} 每台只有 {entry['vcpus']} 个 vCPU")
    if body.get("mem_gb") and entry.get("memory_gib") and body["mem_gb"] > entry["memory_gib"]:
        raise ValueError(f"{entry['instance_type']} 每台只有 {entry['memory_gib']} GiB 内存")
    minutes = body.get("time_minutes")
    if body.get("borrow"):
        minutes = min(minutes or BORROW_MAX_MINUTES, BORROW_MAX_MINUTES)
    args = [
        f"--account={team}",
        f"--qos={BORROW_QOS if body.get('borrow') else team + '-' + partition}",
        f"--partition={partition}",
        f"--nodes={_int(body.get('nodes') or 1, 1, NODES_MAX)}",
        f"--job-name={body.get('job_name') or 'pcui-job'}",
    ]
    if minutes:
        args.append(f"--time={_int(minutes, 1, TIME_MAX_MINUTES)}")
    args.append(f"--mem={_int(body['mem_gb'], 1, 1536)}G" if body.get("mem_gb") else "--mem=0")
    if body.get("cpus"):
        args.append(f"--cpus-per-task={_int(body['cpus'], 1, 192)}")
    if gpus_per_node:
        args.append(f"--gres=gpu:{_int(gpus_per_node, 1, 8)}")
    if body.get("env"):
        args.append(f"--comment={env_comment(body['env'])}")
    return args


def submit_argv(team_root, workdir, script_path, script_token, job_uuid, options, env_dir=None, shared_root=None,
                envs_root=None):
    if UUID_RE.fullmatch(job_uuid or "") is None:
        raise ValueError("Invalid job uuid")
    if bool(script_path) == bool(script_token):
        raise ValueError("Give exactly one of script_path or script_content")
    if script_path:
        mode, source = "path", validate_member_path(script_path)
    else:
        if re.fullmatch(r"[A-Za-z0-9+/=]{1,%d}" % SCRIPT_TOKEN_MAX, script_token) is None:
            raise ValueError("Invalid script token")
        mode, source = "content", script_token
    for option in options:
        if not OPTION_RE.fullmatch(option) or ".." in option.split("/"):
            raise ValueError("Invalid sbatch option")
    if env_dir is None:
        env_token, shared_token, envs_token = NO_ENV, NO_ENV, NO_ENV
    else:
        env_token, shared_token, envs_token = _root(env_dir), _root(shared_root), _root(envs_root)
    return ["submit", _root(team_root), validate_member_path(workdir), mode, source, job_uuid, env_token,
            shared_token, envs_token, *options]


def resubmit_argv(job_id, team_root):
    return ["resubmit", validate_job_id(job_id), _root(team_root)]


# --- parsers ----------------------------------------------------------------------------------------------------


def _decode_gz(text):
    data = (text or "").strip()
    if not data:
        return ""
    try:
        return gzip.decompress(base64.b64decode(data, validate=True)).decode("utf-8", errors="replace")
    except (binascii.Error, OSError, EOFError, ValueError):
        raise MemberCommandError(502, "输出过大或不完整，请缩小查询范围") from None


def _split(line, count, flexible):
    """Split a ``|`` line into ``count`` columns; extra ``|`` belong to column ``flexible`` (a job name)."""
    cols = line.split("|")
    if len(cols) < count:
        return None
    extra = len(cols) - count
    if extra:
        cols = cols[:flexible] + ["|".join(cols[flexible:flexible + extra + 1])] + cols[flexible + extra + 1:]
    return cols


def _none(value):
    value = (value or "").strip()
    return None if value in ("", "None", "N/A", "Unknown") else value


def parse_squeue(text):
    jobs = []
    for line in _decode_gz(text).splitlines():
        cols = _split(line.rstrip("\n"), 14, 1)
        if not cols or not cols[0].strip():
            continue
        job_id, name, account, partition, state, reason, submit, start, limit, elapsed, gres, cpus, mem, qos = cols
        jobs.append({
            "job_id": job_id.strip(), "name": name, "account": account, "partition": partition, "state": state,
            "reason": _none(reason), "reason_text": pending_text(reason) if state == "PENDING" else "",
            "submit": _none(submit), "start": _none(start), "time_limit": _none(limit), "elapsed": _none(elapsed),
            "gres": _none(gres), "cpus": _none(cpus), "memory": _none(mem), "qos": _none(qos),
        })
    return jobs


def parse_history(text):
    jobs = []
    for line in _decode_gz(text).splitlines()[-HISTORY_MAX_ROWS:]:
        cols = _split(line.rstrip("\n"), 14, 1)
        if not cols or not cols[0].strip():
            continue
        (job_id, name, account, partition, qos, state, reason, submit, start, end, elapsed, exit_code, tres,
         workdir) = cols
        jobs.append({
            "job_id": job_id, "name": name, "account": account, "partition": partition, "qos": _none(qos),
            "state": state, "reason": _none(reason), "reason_text": pending_text(reason) if state == "PENDING" else "",
            "submit": _none(submit), "start": _none(start), "end": _none(end),
            "elapsed_seconds": int(elapsed) if elapsed.isdigit() else None, "exit_code": _none(exit_code),
            "alloc_tres": _none(tres), "workdir": _none(workdir),
        })
    return list(reversed(jobs))


KV_RE = re.compile(r"(?:^|\s)([A-Za-z][A-Za-z0-9_:/]*)=(\S*)")


def expand_pattern(pattern, job):
    """Expand the sbatch filename patterns %j %u %x %A %a %% that sacct may return unexpanded."""
    if not pattern:
        return None
    job_id = job.get("job_id") or ""
    array_job, _, array_task = job_id.partition("_")
    values = {"j": job_id.split("_")[0], "u": job.get("user") or "", "x": job.get("name") or "",
              "A": array_job, "a": array_task or "", "%": "%"}
    result = re.sub(r"%([juxAa%])", lambda m: values[m.group(1)], pattern)
    if not result.startswith("/") and job.get("workdir"):
        result = job["workdir"].rstrip("/") + "/" + result
    return result


def parse_job_detail(text):
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        raise MemberCommandError(404, "作业不存在")
    kind, line = lines[0].strip(), lines[1]
    if kind == "@@SCONTROL":
        kv = dict(KV_RE.findall(line))
        user = re.sub(r"\(.*", "", kv.get("UserId", ""))
        job = {
            "job_id": kv.get("JobId"), "name": kv.get("JobName"), "user": user, "account": kv.get("Account"),
            "partition": kv.get("Partition"), "qos": kv.get("QOS"), "state": kv.get("JobState"),
            "reason": _none(kv.get("Reason")), "submit": _none(kv.get("SubmitTime")),
            "start": _none(kv.get("StartTime")), "end": _none(kv.get("EndTime")),
            "time_limit": _none(kv.get("TimeLimit")), "elapsed": _none(kv.get("RunTime")),
            "exit_code": _none(kv.get("ExitCode")), "tres": _none(kv.get("ReqTRES") or kv.get("TRES")),
            "workdir": _none(kv.get("WorkDir")), "command": _none(kv.get("Command")),
            "stdout_path": _none(kv.get("StdOut")), "stderr_path": _none(kv.get("StdErr")), "source": "scontrol",
        }
    elif kind == "@@SACCT":
        cols = _split(line, 16, 2)
        if not cols:
            raise MemberCommandError(404, "作业不存在")
        (job_id, user, name, account, partition, qos, state, reason, submit, start, end, exit_code, tres, workdir,
         stdout, stderr) = cols
        job = {
            "job_id": job_id, "name": name, "user": user, "account": account, "partition": partition, "qos": _none(qos),
            "state": state, "reason": _none(reason), "submit": _none(submit), "start": _none(start), "end": _none(end),
            "time_limit": None, "elapsed": None, "exit_code": _none(exit_code), "tres": _none(tres),
            "workdir": _none(workdir), "command": None, "source": "sacct",
        }
        job["stdout_path"] = expand_pattern(_none(stdout), job)
        job["stderr_path"] = expand_pattern(_none(stderr), job)
    else:
        raise MemberCommandError(404, "作业不存在")
    job["reason_text"] = pending_text(job.get("reason")) if job.get("state") == "PENDING" else ""
    if (job.get("time_limit") or "").upper() in ("UNLIMITED", "INFINITE", "PARTITION_LIMIT"):
        job["time_limit"] = None
    exists = next((line.split()[1] for line in lines[2:] if line.startswith("@@STDOUT_EXISTS ")
                   and len(line.split()) == 2), None)
    job["stdout_exists"] = {"1": True, "0": False}.get(exists)
    return job


def _markers(text):
    values, data = {}, None
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if line == "@@DATA":
            data = "".join(lines[index + 1:]).strip()
            break
        if line.startswith("@@") and " " in line:
            key, value = line[2:].split(" ", 1)
            values[key] = value
    return values, data


def parse_read(text, limit):
    values, data = _markers(text)
    if data is None or "SIZE" not in values:
        raise MemberCommandError(502, "读取文件失败")
    try:
        content = base64.b64decode(data, validate=True) if data else b""
    except binascii.Error:
        raise MemberCommandError(502, "读取文件失败") from None
    size, offset = int(values["SIZE"]), int(values.get("OFFSET", "0"))
    return {
        "path": values.get("PATH"),
        "size": size,
        "offset": offset,
        "length": len(content),
        "content_b64": base64.b64encode(content).decode("ascii"),
        "truncated": offset > 0 or offset + len(content) < size,
        "limit": limit,
    }


def parse_list(text):
    values, data = _markers(text)
    if data is None:
        raise MemberCommandError(502, "读取目录失败")
    try:
        raw = base64.b64decode(data, validate=True) if data else b""
    except binascii.Error:
        raise MemberCommandError(502, "读取目录失败") from None
    chunks = raw.split(b"\0")
    complete, tail = chunks[:-1], chunks[-1]
    truncated = bool(tail) or len(complete) > DIR_MAX_ENTRIES
    entries = []
    for chunk in complete[:DIR_MAX_ENTRIES]:
        parts = chunk.decode("utf-8", errors="replace").split("\t", 3)
        if len(parts) != 4:
            continue
        kind, size, mtime, name = parts
        entries.append({
            "type": {"f": "file", "d": "dir", "l": "link"}.get(kind, "other"),
            "size": int(size) if size.isdigit() else 0,
            "mtime": float(mtime) if re.fullmatch(r"\d+(\.\d+)?", mtime) else None,
            "name": name,
        })
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"]))
    return {"path": values.get("PATH"), "entries": entries, "truncated": truncated}


def _time_minutes(value):
    """Minutes of a Slurm time limit ``[D-]HH:MM:SS`` / ``MM:SS`` / ``MM``; None for UNLIMITED and the like."""
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+)(?::(\d+))?", (value or "").strip())
    if not match:
        return None
    days, first, second, third = match.groups()
    if third is not None:  # [D-]HH:MM:SS
        hours, minutes, seconds = int(first or 0), int(second), int(third)
    elif first is not None:  # MM:SS or D-HH:MM
        hours, minutes, seconds = (int(first), int(second), 0) if days else (0, int(first), int(second))
    else:
        hours, minutes, seconds = 0, int(second), 0
    total = int(days or 0) * 1440 + hours * 60 + minutes + (1 if seconds else 0)
    return min(max(total, 1), TIME_MAX_MINUTES)


def parse_resubmit(text, type_by_partition):
    """A prefilled ``SubmitJob`` body from the ``resubmit`` operation. ``type_by_partition`` maps a partition to
    its catalog entry; a job of a queue outside the catalog has no instance type (the form asks for one)."""
    values = {}
    for line in (text or "").splitlines():
        if line.startswith("@@") and " " in line:
            key, value = line[2:].split(" ", 1)
            values[key] = value
        elif line.startswith("@@"):
            values[line[2:]] = ""
    if "FIELDS" not in values:
        raise MemberCommandError(404, "作业不存在")
    cols = values["FIELDS"].split("|")
    if len(cols) != 7:
        raise MemberCommandError(502, "作业记录格式无法识别")
    job_id, _user, partition, nodes, timelimit, qos, req_tres = cols
    entry = type_by_partition.get(partition)
    tres = {}
    for item in req_tres.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            tres[key] = value
    node_count = int(nodes) if nodes.isdigit() and int(nodes) > 0 else 1
    gpu_total = tres.get("gres/gpu")
    body = {
        "instance_type": entry["instance_type"] if entry else None,
        "nodes": min(node_count, NODES_MAX),
        "borrow": qos == BORROW_QOS,
        "env": parse_env_comment(values.get("COMMENT")),
    }
    minutes = _time_minutes(timelimit)  # None for UNLIMITED: the job had no time limit
    if body["borrow"]:
        minutes = min(minutes or BORROW_MAX_MINUTES, BORROW_MAX_MINUTES)
    if minutes:
        body["time_minutes"] = minutes
    if gpu_total and gpu_total.isdigit():
        body["gpus_per_node"] = int(gpu_total) // node_count
    name = values.get("NAME", "").strip()
    if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
        body["job_name"] = name
    workdir = values.get("WORKDIR", "").strip()
    if workdir.startswith("/") and not re.search(r"[\x00-\x1f\x7f]", workdir):
        body["workdir"] = workdir
    if values.get("CONTENT"):
        body["script_content"] = _decode_gz(values["CONTENT"])
    elif values.get("PATH"):
        body["script_path"] = values["PATH"].strip()
    return {"job_id": job_id, "partition": partition, "job": body}


def parse_submit(text):
    job_id = script = None
    for line in (text or "").splitlines():
        if line.startswith("@@JOB "):
            job_id = line[len("@@JOB "):].strip()
        elif line.startswith("@@SCRIPT "):
            script = line[len("@@SCRIPT "):].strip()
    if not job_id or not JOB_ID_RE.fullmatch(job_id):
        raise MemberCommandError(502, "提交作业失败：未返回作业号")
    return {"job_id": job_id, "script_path": script}
