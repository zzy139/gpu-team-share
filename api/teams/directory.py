# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Head-node command builders for Simple AD (adcli + ldapsearch + ldapmodify), the EFS team directory and the
ownership transfer of runtime environments (offboarding).

Simple AD has no AWS API for users and groups, so the head node runs ``adcli``. The head node reads the AD
administrator password from Secrets Manager itself: the SSM command only carries the secret ARN, and the
password is handed to adcli / ldapsearch through stdin or a process-substitution file descriptor. It never
appears in a command line, an environment variable, the SSM command parameters or the output.

The scripts are constants; every variable part is a separate, validated, ``shlex.quote``d argv token.
"""
from api.teams.slurm import quote_argv
from api.teams.validation import AD_GROUP_RE, EMAIL_RE, NAME_RE as TEAM_NAME_RE, RESERVED_NAMES, USER_PREFIX_RE

AD_EXIT_NOT_INSTALLED = 3
AD_MARKER = "@@AD"
DIR_MARKER = "@@TEAMDIR"
ENVDIR_MARKER = "@@ENVDIR"
ENVOWNER_MARKER = "@@ENVOWNER"
ENVTEAM_MARKER = "@@ENVTEAMDIR"
TEAM_DIR_MODE = "drwxrws---"   # 2770 root:<team>
ENV_DIR_MODE = "drwxrws--T"    # 3770 root:<team>: sticky, so on the CLI only the owner deletes an environment
ENV_TEAM_DIR_MODE = "drwxr-s--x"  # 2751 root:<team>: /efs/envs/<team>, the team lists it, others only traverse

AD_SCRIPT = r"""set -euo pipefail
trap 'rc=$?; if [ "$rc" -ne 0 ]; then echo "@@AD error $rc"; fi' EXIT
region="$1"; secret="$2"; domain="$3"; dc="$4"; admin="$5"; group="$6"; shift 6
for tool in adcli ldapsearch ldapmodify; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "adcli/ldapsearch/ldapmodify is not installed on the head node" >&2
    exit 3
  fi
done
AWS_CLI=$(command -v aws || echo /usr/local/bin/aws)
pw=$("$AWS_CLI" secretsmanager get-secret-value --region "$region" --secret-id "$secret" --query SecretString --output text)
if [ "${pw:0:1}" = "{" ]; then
  pw=$(printf '%s' "$pw" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("password") or d.get("Password") or "", end="")')
fi
if [ "${#pw}" -eq 0 ]; then
  echo "The AD administrator secret is empty" >&2
  exit 3
fi
base="DC=${domain//./,DC=}"
ldq() {
  ldapsearch -x -LLL -o ldif-wrap=no -H "ldap://$dc" -D "$admin@$domain" -y <(printf '%s' "$pw") -b "$base" "$@"
}
adc() {
  local action="$1"; shift
  printf '%s' "$pw" | adcli "$action" --domain="$domain" --domain-controller="$dc" --login-user="$admin" --stdin-password "$@" >/dev/null
}
group_dn() {
  local out; out=$(ldq "(&(objectClass=group)(sAMAccountName=$group))" dn)
  sed -n 's/^dn: //p' <<<"$out" | head -n 1
}
user_exists() {
  local out; out=$(ldq "(&(objectClass=user)(sAMAccountName=$1))" dn)
  grep -q '^dn:' <<<"$out"
}
user_dn() {
  local out; out=$(ldq "(&(objectClass=user)(sAMAccountName=$1))" dn)
  sed -n 's/^dn: //p' <<<"$out" | head -n 1
}
set_uac() {  # set_uac <dn> <userAccountControl>: 514 disables the account, 512 enables it
  printf 'dn: %s\nchangetype: modify\nreplace: userAccountControl\nuserAccountControl: %s\n' "$1" "$2" \
    | ldapmodify -x -H "ldap://$dc" -D "$admin@$domain" -y <(printf '%s' "$pw") >/dev/null
}
is_member() {
  local gdn out; gdn=$(group_dn)
  [ -n "$gdn" ] || return 1
  out=$(ldq "(&(objectClass=user)(sAMAccountName=$1)(memberOf=$gdn))" dn)
  grep -q '^dn:' <<<"$out"
}
for op in "$@"; do
  kind="${op%%:*}"; arg="${op#*:}"
  case "$kind" in
    lookup)
      out=$(ldq "(&(objectClass=user)(sAMAccountName=$arg))" mail)
      if ! grep -q '^dn:' <<<"$out"; then
        echo "@@AD user $arg absent"
      else
        mail=$(sed -n 's/^mail: //p' <<<"$out" | head -n 1)
        if [ -n "$mail" ]; then echo "@@AD user $arg mail=$mail"; else echo "@@AD user $arg nomail"; fi
      fi
      ;;
    probe-group)
      if [ -n "$(group_dn)" ]; then echo "@@AD group $group exists"; else echo "@@AD group $group absent"; fi
      ;;
    probe-member)
      if is_member "$arg"; then echo "@@AD member $arg present"; else echo "@@AD member $arg absent"; fi
      ;;
    create-group)
      if [ -n "$(group_dn)" ]; then
        echo "@@AD group $group exists"
      else
        adc create-group "$group" --description="PCUI team $group"
        echo "@@AD group $group created"
      fi
      ;;
    create-user)
      user="${arg%%:*}"; mail="${arg#*:}"
      if user_exists "$user"; then
        echo "@@AD user $user exists"
      else
        adc create-user "$user" --display-name="$user" --mail="$mail"
        echo "@@AD user $user created"
      fi
      ;;
    add)
      if is_member "$arg"; then
        echo "@@AD member $arg present"
      else
        adc add-member "$group" "$arg"
        echo "@@AD member $arg added"
      fi
      ;;
    disable|enable)
      udn=$(user_dn "$arg")
      if [ -z "$udn" ]; then
        echo "@@AD user $arg absent"
      else
        if [ "$kind" = disable ]; then set_uac "$udn" 514; else set_uac "$udn" 512; fi
        echo "@@AD user $arg ${kind}d"
      fi
      ;;
    remove)
      if is_member "$arg"; then
        adc remove-member "$group" "$arg"
        echo "@@AD member $arg removed"
      else
        echo "@@AD member $arg absent"
      fi
      ;;
    *)
      echo "unknown AD operation" >&2
      exit 2
      ;;
  esac
done
"""

# /efs/<team> (2770) with the legacy /efs/<team>/envs (3770, kept: old paths link from there) and the
# environment directory /efs/envs/<team> (2751 root:<team>) below the root-owned /efs/envs (0755).
TEAM_DIR_SCRIPT = r"""set -euo pipefail
dir="$1"; group="$2"; team="$3"
for _ in 1 2 3 4 5; do
  if getent group "$group" >/dev/null 2>&1; then break; fi
  sss_cache -E >/dev/null 2>&1 || true
  sleep 2
done
if ! getent group "$group" >/dev/null 2>&1; then
  echo "AD group $group does not resolve on the head node yet; retry in a minute" >&2
  exit 4
fi
mkdir -p -- "$dir"
[ ! -L "$dir" ] || { echo "$dir is a symbolic link" >&2; exit 5; }
chgrp -- "$group" "$dir"
chmod 2770 -- "$dir"
mkdir -p -- "$dir/envs"
[ ! -L "$dir/envs" ] || { echo "$dir/envs is a symbolic link" >&2; exit 5; }
chown root -- "$dir/envs"
chgrp -- "$group" "$dir/envs"
chmod 3770 -- "$dir/envs"
root="$(dirname -- "$dir")/envs"
mkdir -p -- "$root"
[ ! -L "$root" ] || { echo "$root is a symbolic link" >&2; exit 5; }
chown root:root -- "$root"
chmod 0755 -- "$root"
mkdir -p -- "$root/$team"
[ ! -L "$root/$team" ] || { echo "$root/$team is a symbolic link" >&2; exit 5; }
chown root -- "$root/$team"
chgrp -- "$group" "$root/$team"
chmod 2751 -- "$root/$team"
echo "@@TEAMDIR $(stat -c '%A %G' -- "$dir")"
echo "@@ENVDIR $(stat -c '%A %G' -- "$dir/envs")"
echo "@@ENVTEAMDIR $(stat -c '%A %G' -- "$root/$team")"
"""

TEAM_DIR_PROBE_SCRIPT = r"""dir="$1"; team="$2"
envteam="$(dirname -- "$dir")/envs/$team"
for d in "$dir" "$dir/envs" "$envteam"; do
  if [ "$d" = "$dir" ]; then m=@@TEAMDIR; elif [ "$d" = "$envteam" ]; then m=@@ENVTEAMDIR; else m=@@ENVDIR; fi
  if [ -d "$d" ] && [ ! -L "$d" ]; then echo "$m $(stat -c '%A %G' -- "$d")"; else echo "$m missing"; fi
done
"""

# Offboarding: every environment directory /efs/envs/<team>/<name>/<version> owned by <from> goes to <to> (the
# team lead). chown never follows symbolic links (-h, -P), only files owned by <from> change (--from), and the
# owner field of a regular env.json (never a symlink) is rewritten atomically.
ENV_OWNER_SCRIPT = r"""set -euo pipefail
root="$1"; from="$2"; to="$3"
if [ ! -d "$root" ] || [ -L "$root" ]; then echo "@@ENVOWNER 0"; exit 0; fi
id -u -- "$to" >/dev/null 2>&1 || { echo "user $to does not resolve on the head node" >&2; exit 4; }
id -u -- "$from" >/dev/null 2>&1 || { echo "@@ENVOWNER 0"; exit 0; }
n=0; dirs=()
while IFS= read -r -d '' d; do
  # The version directory and env.json change owner now (delete/publish checks use them); the rest of the tree
  # (a conda prefix can hold tens of thousands of files on EFS) is handed over in the background below, so the
  # request stays within the API Gateway time limit.
  chown -h --from="$from" -- "$to" "$d"
  dirs+=("$d")
  f="$d/env.json"
  if [ -f "$f" ] && [ ! -L "$f" ]; then
    python3 - "$f" "$to" <<'PY'
import json, os, sys
path, owner = sys.argv[1], sys.argv[2]
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
with os.fdopen(fd) as handle:
    data = json.load(handle)
if isinstance(data, dict):
    data["owner"] = owner
    st = os.stat(path, follow_symlinks=False)
    tmp = path + ".pcui-tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)
    os.chown(tmp, st.st_uid, st.st_gid, follow_symlinks=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
PY
  fi
  n=$((n + 1))
done < <(find "$root" -mindepth 2 -maxdepth 2 -type d -user "$from" -print0)
if [ "${#dirs[@]}" -gt 0 ]; then
  setsid nohup chown -R -h -P --from="$from" -- "$to" "${dirs[@]}" </dev/null >/dev/null 2>&1 &
fi
echo "@@ENVOWNER $n"
"""


def _user(value):
    if not isinstance(value, str) or USER_PREFIX_RE.fullmatch(value) is None:
        raise ValueError(f"Invalid AD user name {value!r}")
    return value


def _group(value):
    if not isinstance(value, str) or AD_GROUP_RE.fullmatch(value) is None:
        raise ValueError(f"Invalid AD group name {value!r}")
    return value


def _email(value):
    if not isinstance(value, str) or EMAIL_RE.fullmatch(value) is None:
        raise ValueError("Invalid email address")
    return value


def _op(op):
    """Validate one ``kind:arg`` operation token."""
    kind, _, arg = op.partition(":")
    if kind in ("lookup", "probe-member", "add", "remove", "disable", "enable"):
        return f"{kind}:{_user(arg)}"
    if kind in ("probe-group", "create-group"):
        return f"{kind}:"
    if kind == "create-user":
        user, _, email = arg.partition(":")
        if email.split("@", 1)[0] != _user(user):
            raise ValueError("The AD user name must be the local part of the email")
        return f"create-user:{user}:{_email(email)}"
    raise ValueError(f"Unknown AD operation {kind!r}")


def ad_argv(config, group, ops):
    """argv of one AD script run. ``ops`` are ``lookup:<u>``, ``create-group``, ``create-user:<u>:<email>``,
    ``add:<u>``, ``remove:<u>``, ``probe-group``, ``probe-member:<u>``, ``disable:<u>``, ``enable:<u>``."""
    if not ops:
        raise ValueError("No AD operation")
    return [
        "bash", "-c", AD_SCRIPT, "pcui-ad",
        config["region"], config["ad_admin_secret_arn"], config["ad_domain"],
        config["ad_domain_controllers"][0], config["ad_admin_user"], _group(group),
        *[_op(op) for op in ops],
    ]


def ad_command(config, group, ops):
    return quote_argv(ad_argv(config, group, ops))


def _dir_path(path):
    if (not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/") or "//" in path
            or path.rstrip("/") != path or path.count("/") < 2):
        raise ValueError("Invalid team directory")
    return path


def _team(value):
    if not isinstance(value, str) or TEAM_NAME_RE.fullmatch(value) is None or value in RESERVED_NAMES:
        raise ValueError(f"Invalid team name {value!r}")
    return value


def team_dir_argv(path, group, team):
    """``path`` is the team directory /efs/<dir>; ``team`` names /efs/envs/<team> (the team name, which can differ
    from the directory name of older teams)."""
    return ["bash", "-c", TEAM_DIR_SCRIPT, "pcui-team-dir", _dir_path(path), _group(group), _team(team)]


def team_dir_command(path, group, team):
    return quote_argv(team_dir_argv(path, group, team))


def team_dir_probe_argv(path, team):
    return ["bash", "-c", TEAM_DIR_PROBE_SCRIPT, "pcui-team-dir-probe", _dir_path(path), _team(team)]


def env_owner_argv(envs_team_path, from_user, to_user):
    """Hand the environments of ``from_user`` under ``envs_team_path`` (/efs/envs/<team>) over to ``to_user``."""
    return ["bash", "-c", ENV_OWNER_SCRIPT, "pcui-env-owner", _dir_path(envs_team_path), _user(from_user),
            _user(to_user)]


def env_owner_command(envs_team_path, from_user, to_user):
    return quote_argv(env_owner_argv(envs_team_path, from_user, to_user))


def probe_sections(config, group, users, path, team):
    """(name, argv) sections for ``slurm.join_commands``: AD state of the group and users, and the team dirs."""
    ops = ["probe-group"] + [f"lookup:{u}" for u in users] + [f"probe-member:{u}" for u in users]
    return [("ADPROBE", ad_argv(config, group, ops)), ("DIRPROBE", team_dir_probe_argv(path, team))]


# --- parsers ----------------------------------------------------------------------------------------------------


def parse_ad_output(lines):
    """Parse ``@@AD`` lines into ``{users, groups, members, created, error}``.

    ``users[u]`` is ``{"state": "absent"|"mail"|"nomail"|"exists"|"created", "mail": str|None}``.
    """
    result = {"users": {}, "groups": {}, "members": {}, "created": [], "error": None}
    if isinstance(lines, str):
        lines = lines.splitlines()
    for raw in lines:
        parts = raw.strip().split(" ")
        if len(parts) < 3 or parts[0] != AD_MARKER:
            continue
        kind = parts[1]
        if kind == "error":
            result["error"] = parts[2]
        elif kind == "user" and len(parts) == 4:
            user, state = parts[2], parts[3]
            if state.startswith("mail="):
                result["users"][user] = {"state": "mail", "mail": state[len("mail="):].lower()}
            else:
                result["users"][user] = {"state": state, "mail": None}
                if state == "created":
                    result["created"].append(user)
        elif kind == "group" and len(parts) == 4:
            result["groups"][parts[2]] = parts[3]
        elif kind == "member" and len(parts) == 4:
            result["members"][parts[2]] = parts[3]
    return result


def _parse_dir_line(lines, marker):
    for raw in lines:
        parts = raw.strip().split()
        if not parts or parts[0] != marker:
            continue
        if len(parts) == 2 and parts[1] == "missing":
            return {"exists": False, "mode": None, "group": None}
        if len(parts) == 3:
            return {"exists": True, "mode": parts[1], "group": parts[2]}
    return {"exists": False, "mode": None, "group": None, "unknown": True}


def parse_team_dir(lines):
    """Return ``{"dir": ..., "envs": ..., "env_team": ...}`` (each ``{exists, mode, group}``) from the
    ``@@TEAMDIR``/``@@ENVDIR``/``@@ENVTEAMDIR`` lines."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    return {"dir": _parse_dir_line(lines, DIR_MARKER), "envs": _parse_dir_line(lines, ENVDIR_MARKER),
            "env_team": _parse_dir_line(lines, ENVTEAM_MARKER)}


def team_dir_ready(state, group):
    expected = (("dir", TEAM_DIR_MODE), ("envs", ENV_DIR_MODE), ("env_team", ENV_TEAM_DIR_MODE))
    for key, mode in expected:
        entry = state.get(key) or {}
        if not (entry.get("exists") and entry.get("mode") == mode and entry.get("group") == group):
            return False
    return True


def parse_env_owner(lines):
    """Number of environment directories handed over, or None when the marker is missing."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    for raw in lines:
        parts = raw.strip().split()
        if len(parts) == 2 and parts[0] == ENVOWNER_MARKER and parts[1].isdigit():
            return int(parts[1])
    return None
