# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Runtime environment catalog: container images (Enroot squashfs) and conda environments on the shared file system.

Layout: ``<envs root>/<team>/<name>/<version>/`` (``/efs/envs/...``), each version with ``env.json`` (status
model), ``spec/`` (Dockerfile, environment.yml or requirements.txt), ``build.log`` and ``image.sqsh`` or ``prefix/``.

Visibility is decided by the kernel, per environment name: ``<envs root>/<team>`` is 2751 root:<team> (the team
lists it, others only traverse), the name directory is root:<team> 3770 (仅本团队) or 3775 (所有人, sticky: members
add versions and delete only their own on the CLI), versions are created by members 2755 with files 0644/0755.
Members cannot change the mode of the root-owned name directory; the portal does it as root (``prepare``,
``visibility``) after its role checks. ``<envs root>/_public/<team>__<name>`` (relative links ``../<team>/<name>``,
root-owned directory) indexes the names visible to everyone, so other teams can find them without listing
``<envs root>/<team>``.

Where the work runs:
* Members create, list and retry environments on the LOGIN node as themselves (``MEMBER_ENV_SCRIPT``). Public
  registry imports and conda environments are built by a Slurm job of the member on the ``build`` queue
  (``hpc-env-build``); images of the team's private ECR repository and Dockerfiles are built by CodeBuild, which
  opens only ``envs/<team>/<name>/<version>`` of the file system.
* Preparing a name, changing its visibility and deleting run as root on the HEAD node (``ENV_ROOT_PY``). Every path
  is opened one component at a time without following symbolic links, so a member cannot redirect a root operation
  elsewhere by swapping a directory for a link; deletions move the directory into a root-only trash first.

Both programs are constants; requests only supply validated argv tokens. Free text (env.json content, specs) travels
as gzip+base64 tokens and is never interpreted by a shell. Expected outcomes are reported as ``@@RESULT <word>``.
"""
import base64
import binascii
import gzip
import json
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from api.pcm_globals import logger
from api.teams import lifecycle_store
from api.teams.member_commands import encode_script
from api.teams.member_exec import MemberCommandError, check_result, run_as_member
from api.teams.slurm import SlurmCommandError, quote_argv, run_command
from api.teams.validation import (
    ENV_NAME_RE,
    ENV_VERSION_RE,
    USER_PREFIX_RE,
    validate_env_name,
    validate_env_version,
    validate_member_path,
    validate_team_name,
)

PUBLIC_REGISTRIES = ("docker.io", "nvcr.io", "public.ecr.aws", "ghcr.io")
IMAGE_REF_MAX = 256
IMAGE_PATH_RE = re.compile(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*")
TAG_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
ECR_HOST_RE = re.compile(r"\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com(\.cn)?")
BUILD_ID_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,160}")
FAILURE_RE = re.compile(r"[A-Za-z0-9 ._:()/,-]{1,200}")
SPEC_NAMES = ("environment.yml", "requirements.txt", "Dockerfile")
DOCKERFILE_B64_MAX = 20000  # CodeBuild environment variable value; larger Dockerfiles are rejected with 413
ENV_TOKEN_MAX = 36000       # compressed spec; keeps the whole SSM command (program + tokens) near the job-submit size
COMMAND_MAX = 56000         # checked by a test: the submit command with the largest script token is about 53 KB
CAP_UNIT = 10 ** 9          # env_storage_cap_gb is in GB
WARN_PERCENT, FULL_PERCENT = 80, 100
CODEBUILD_FAILED = ("FAILED", "FAULT", "STOPPED", "TIMED_OUT")
VISIBILITIES = ("team", "all")
OTHER_TEAM_REASON = "这是其他团队公开的环境，只能使用"

# --- programs that run on the cluster -----------------------------------------------------------------------------

ENV_COMMON_PY = r'''import base64, gzip, json, os, pwd, re, stat, subprocess, sys, time

NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
TEAM_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,31}")
USER_RE = re.compile(r"[a-z][a-z0-9._-]{0,19}")
ROOT_RE = re.compile(r"/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")
BUILD_ID_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,160}")
JOB_RE = re.compile(r"\d{1,10}")
JSON_MAX, SPEC_MAX, LIST_MAX = 65536, 65536, 200
SPEC_NAMES = ("environment.yml", "requirements.txt", "Dockerfile")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# Another team's <envs root>/<team> is 2751: searchable, not readable, so it is opened with O_PATH (Linux).
PATH_FLAGS = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
PUBLIC = "_public"
TEXT = {"kind": 16, "source": 16, "builder": 16, "image": 300, "ref": 300, "status": 16, "failure": 300,
        "job_id": 16, "build_id": 160, "owner": 32, "created_at": 32, "queued_at": 32, "started_at": 32,
        "finished_at": 32, "description": 256, "software": 256, "python": 16, "spec": 32}


class Stop(Exception):
    def __init__(self, result):
        super().__init__(result)
        self.result = result


def need(pattern, value):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise Stop("invalid")
    return value


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def real_root(path):
    """Canonical form of an absolute root directory (the only place where links are resolved)."""
    if not isinstance(path, str) or ROOT_RE.fullmatch(path) is None or ".." in path.split("/"):
        raise Stop("invalid")
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise Stop("not_found")
    return real


def open_path(root, parts=()):
    """fd of root/parts..., opening every component with O_NOFOLLOW (a symbolic link anywhere is 'denied')."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    for part in [p for p in root.split("/") if p] + list(parts):
        try:
            nfd = os.open(part, DIR_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            os.close(fd)
            raise Stop("not_found")
        except OSError:
            os.close(fd)
            raise Stop("denied")
        os.close(fd)
        fd = nfd
    return fd


def read_bytes(dfd, name, limit):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        data = f.read(limit + 1)
    return data if len(data) <= limit else None


def read_json(dfd, name="env.json"):
    raw = read_bytes(dfd, name, JSON_MAX)
    try:
        data = json.loads(raw.decode("utf-8")) if raw is not None else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def write_file(dfd, name, data, mode, uid=None, gid=None):
    """Write name atomically in the directory dfd (temporary file created exclusively, never through a link)."""
    tmp = "." + name + ".pcui-tmp"
    try:
        os.unlink(tmp, dir_fd=dfd)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        if uid is not None:
            os.fchown(f.fileno(), uid, gid if gid is not None else -1)
        os.fchmod(f.fileno(), mode)
    os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)


def write_json(dfd, data, mode=0o644, uid=None, gid=None):
    write_file(dfd, "env.json", json.dumps(data, indent=1, sort_keys=True).encode("utf-8"), mode, uid, gid)


def update_json(dfd, changes):
    data = read_json(dfd)
    if data is None:
        raise Stop("not_ready")
    st = os.stat("env.json", dir_fd=dfd, follow_symlinks=False)
    data.update(changes)
    write_json(dfd, data, stat.S_IMODE(st.st_mode) | 0o444)
    return data


def user_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None


def subdirs(dfd):
    with os.scandir(dfd) as it:
        return sorted(e.name for e in it if e.is_dir(follow_symlinks=False))


def is_file(dfd, name):
    try:
        return stat.S_ISREG(os.stat(name, dir_fd=dfd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def visibility_of(nfd):
    """'all' when the name directory lets others in (3775), else 'team' (3770)."""
    return "all" if os.fstat(nfd).st_mode & 0o001 else "team"


def view(team, name, version, vfd, path, visibility):
    item = {"scope": "team", "team": team, "name": name, "version": version, "path": path,
            "visibility": visibility, "dir_owner": user_name(os.fstat(vfd).st_uid),
            "has_log": is_file(vfd, "build.log")}
    data = read_json(vfd)
    if data is None:
        item["status"] = "unknown"
        return item
    for key, limit in TEXT.items():
        if isinstance(data.get(key), str):
            item[key] = data[key][:limit]
    for key in ("size_bytes", "attempt"):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            item[key] = value
    detected = data.get("detected")
    if isinstance(detected, dict):
        item["detected"] = {k: detected[k][:32] for k in ("cuda", "python") if isinstance(detected.get(k), str)}
    return item


def name_versions(nfd, team, name, path, out, state):
    """Append the versions of the open name directory nfd (path = its absolute path)."""
    visibility = visibility_of(nfd)
    for version in subdirs(nfd):
        if not VERSION_RE.fullmatch(version):
            continue
        if len(out) >= LIST_MAX:
            state["truncated"] = True
            return
        try:
            vfd = os.open(version, DIR_FLAGS, dir_fd=nfd)
        except OSError:
            continue
        try:
            out.append(view(team, name, version, vfd, path + "/" + version, visibility))
        finally:
            os.close(vfd)


def open_name(er, team, name):
    """fd of <envs root>/<team>/<name>. The team directory is only searched (another team's is 2751), the name
    directory is opened for reading: that fails unless the caller is in the team or the name is 所有人 (3775)."""
    efd = open_path(er)
    try:
        tfd = os.open(team, PATH_FLAGS, dir_fd=efd)
    except FileNotFoundError:
        raise Stop("not_found")
    except OSError:
        raise Stop("denied")
    finally:
        os.close(efd)
    try:
        if not stat.S_ISDIR(os.fstat(tfd).st_mode):
            raise Stop("denied")
        try:
            return os.open(name, DIR_FLAGS, dir_fd=tfd)
        except FileNotFoundError:
            raise Stop("not_found")
        except OSError:
            raise Stop("denied")
    finally:
        os.close(tfd)


def public_names(er):
    """(team, name) pairs of the _public index (link names <team>__<name>; names have no '_')."""
    try:
        pfd = open_path(er, [PUBLIC])
    except Stop:
        return []
    try:
        with os.scandir(pfd) as it:
            links = sorted(e.name for e in it if e.is_symlink())
    finally:
        os.close(pfd)
    pairs = []
    for link in links:
        team, sep, name = link.rpartition("__")
        if sep and TEAM_RE.fullmatch(team) and NAME_RE.fullmatch(name):
            pairs.append((team, name))
    return pairs


def list_envs(envs_root, team):
    """The caller's team (every name) and the 所有人 names of other teams that the caller can read."""
    need(TEAM_RE, team)
    er = real_root(envs_root)
    state, result = {"truncated": False}, {"envs": []}
    try:
        tfd = open_path(er, [team])
    except Stop:
        tfd = None
    if tfd is not None:
        try:
            for name in subdirs(tfd):
                if not NAME_RE.fullmatch(name):
                    continue
                try:
                    nfd = os.open(name, DIR_FLAGS, dir_fd=tfd)
                except OSError:
                    continue
                try:
                    name_versions(nfd, team, name, "%s/%s/%s" % (er, team, name), result["envs"], state)
                finally:
                    os.close(nfd)
        finally:
            os.close(tfd)
    for other, name in public_names(er):
        if other == team or state["truncated"]:
            continue
        try:
            nfd = open_name(er, other, name)
        except Stop:
            continue  # not readable for the caller (仅本团队, or removed)
        try:
            if visibility_of(nfd) == "all":  # root reads everything: the mode decides, not the index
                name_versions(nfd, other, name, "%s/%s/%s" % (er, other, name), result["envs"], state)
        finally:
            os.close(nfd)
    result["truncated"] = state["truncated"]
    data = base64.b64encode(gzip.compress(json.dumps(result, separators=(",", ":")).encode("utf-8"))).decode()
    print("@@LIST " + data)
    return "listed"


def team_total(efd):
    total = 0
    for name in subdirs(efd):
        try:
            nfd = os.open(name, DIR_FLAGS, dir_fd=efd)
        except OSError:
            continue
        try:
            for version in subdirs(nfd):
                try:
                    vfd = os.open(version, DIR_FLAGS, dir_fd=nfd)
                except OSError:
                    continue
                try:
                    data = read_json(vfd) or {}
                finally:
                    os.close(vfd)
                size = data.get("size_bytes")
                if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                    total += size
        finally:
            os.close(nfd)
    return total


def decode_token(token, limit):
    try:
        data = gzip.decompress(base64.b64decode(token, validate=True))
    except Exception:
        raise Stop("invalid")
    if len(data) > limit or b"\x00" in data:
        raise Stop("invalid")
    return data


def run(main, argv):
    sys.stdout.flush()
    try:
        result = main(argv)
    except Stop as stop:
        result = stop.result
    print("@@RESULT " + result)
    sys.stdout.flush()
    return 0
'''

MEMBER_ENV_PY = ENV_COMMON_PY + r'''

def sbatch(vfd, path, team, name, shared):
    """Submit the constant builder as the member on the build queue; the job id goes into env.json."""
    tool = shared + "/tools/hpc-env-build"
    if not os.access(tool, os.X_OK):
        raise Stop("unavailable")
    proc = subprocess.run(["sbatch", "--parsable", "--account=" + team, "--partition=build", "--qos=envbuild",
                           "--job-name=env-" + name, "--time=180", "--output=" + path + "/build.log",
                           "--open-mode=append", "--chdir=" + path, tool, path],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    job = (proc.stdout or "").strip().split(";")[0]
    if proc.returncode != 0 or not JOB_RE.fullmatch(job):
        lines = [l for l in (proc.stderr or "").splitlines() if l.strip()]
        update_json(vfd, {"status": "failed", "finished_at": now(),
                          "failure": ("Could not submit the build job: " + (lines[-1] if lines else "sbatch failed"))[:300]})
        sys.stderr.write(proc.stderr or "sbatch failed\n")
        sys.stdout.flush()
        sys.exit(1)
    update_json(vfd, {"job_id": job})
    print("@@JOB " + job)


def own(vfd):
    if os.fstat(vfd).st_uid != os.getuid():
        raise Stop("not_owner")


def check_cap(efd, cap):
    total = team_total(efd)
    print("@@TOTAL %d" % total)
    if cap > 0 and total >= cap:
        raise Stop("cap")


def op_create(envs_root, shared_root, team, name, version, meta, spec_name, spec, cap, builder):
    need(TEAM_RE, team), need(NAME_RE, name), need(VERSION_RE, version), need(re.compile(r"\d{1,20}"), cap)
    if builder not in ("slurm", "codebuild") or spec_name not in ("-",) + SPEC_NAMES:
        raise Stop("invalid")
    data = json.loads(decode_token(meta, JSON_MAX).decode("utf-8"))
    if not isinstance(data, dict):
        raise Stop("invalid")
    spec_data = decode_token(spec, SPEC_MAX) if spec_name != "-" else None
    er, sr = real_root(envs_root), real_root(shared_root)
    tfd = open_path(er, [team])
    check_cap(tfd, int(cap))
    try:  # the root-owned name directory is prepared by the portal (root) first; it decides the visibility
        nfd = os.open(name, DIR_FLAGS, dir_fd=tfd)
    except FileNotFoundError:
        raise Stop("not_found")
    except OSError:
        raise Stop("denied")
    try:
        os.mkdir(version, 0o755, dir_fd=nfd)
    except FileExistsError:
        raise Stop("exists")
    except PermissionError:
        raise Stop("denied")
    vfd = os.open(version, DIR_FLAGS, dir_fd=nfd)
    os.fchmod(vfd, 0o2755)
    data.update({"owner": user_name(os.getuid()), "status": "queued", "builder": builder,
                 "spec": spec_name if spec_data is not None else None})
    write_json(vfd, data, 0o644)
    if spec_data is not None:
        os.mkdir("spec", 0o755, dir_fd=vfd)
        sfd = os.open("spec", DIR_FLAGS, dir_fd=vfd)
        write_file(sfd, spec_name, spec_data, 0o644)
        os.close(sfd)
    path = "%s/%s/%s/%s" % (er, team, name, version)
    print("@@DIR " + path)
    if builder == "slurm":
        sbatch(vfd, path, team, name, sr)
    else:
        print("@@IDS %d %d" % (os.getuid(), os.fstat(tfd).st_gid))
    return "created"


def op_retry(envs_root, shared_root, team, name, version, cap):
    need(TEAM_RE, team), need(NAME_RE, name), need(VERSION_RE, version), need(re.compile(r"\d{1,20}"), cap)
    er, sr = real_root(envs_root), real_root(shared_root)
    efd = open_path(er, [team])
    vfd = open_path(er, [team, name, version])
    own(vfd)
    data = read_json(vfd)
    if data is None or data.get("status") != "failed":
        raise Stop("state")
    builder = data.get("builder")
    if builder not in ("slurm", "codebuild"):
        raise Stop("invalid")
    check_cap(efd, int(cap))
    attempt = data.get("attempt") if isinstance(data.get("attempt"), int) else 1
    update_json(vfd, {"status": "queued", "failure": None, "job_id": None, "build_id": None, "started_at": None,
                      "finished_at": None, "queued_at": now(), "attempt": attempt + 1})
    path = "%s/%s/%s/%s" % (er, team, name, version)
    print("@@DIR " + path)
    print("@@BUILDER " + builder)
    if builder == "slurm":
        sbatch(vfd, path, team, name, sr)
        return "retried"
    print("@@SOURCE " + str(data.get("source") or ""))
    image = data.get("image")
    if isinstance(image, str) and re.fullmatch(r"[A-Za-z0-9._/:@-]{1,300}", image):
        print("@@IMAGE " + image)
    if data.get("source") == "dockerfile":
        try:
            sfd = os.open("spec", DIR_FLAGS, dir_fd=vfd)
        except OSError:
            raise Stop("not_found")
        content = read_bytes(sfd, "Dockerfile", SPEC_MAX)
        os.close(sfd)
        if content is None:
            raise Stop("not_found")
        print("@@SPEC " + base64.b64encode(gzip.compress(content)).decode())
    print("@@IDS %d %d" % (os.getuid(), os.fstat(efd).st_gid))
    return "retried"


def op_mark(envs_root, team, name, version, build_id, failure):
    need(TEAM_RE, team), need(NAME_RE, name), need(VERSION_RE, version)
    vfd = open_path(real_root(envs_root), [team, name, version])
    own(vfd)
    data = read_json(vfd)
    if data is None:
        raise Stop("not_ready")
    changes = {}
    if build_id != "-":
        need(BUILD_ID_RE, build_id)
        if data.get("status") == "queued" and not data.get("build_id"):
            changes["build_id"] = build_id
    if failure != "-":
        need(re.compile(r"[A-Za-z0-9 ._:()/,-]{1,200}"), failure)
        if data.get("status") in ("queued", "building"):
            changes.update({"status": "failed", "failure": failure, "finished_at": now()})
    if changes:
        update_json(vfd, changes)
    return "marked"


OPS = {"env-list": (2, list_envs), "env-create": (10, op_create), "env-retry": (6, op_retry),
       "env-mark": (6, op_mark)}


def main(argv):
    if not argv or argv[0] not in OPS or len(argv) - 1 != OPS[argv[0]][0]:
        raise Stop("invalid")
    return OPS[argv[0]][1](*argv[1:])


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
'''

ENV_ROOT_PY = ENV_COMMON_PY + r'''

def background(work):
    """Run work() in a detached grandchild (SSM returns at once; deletions on EFS can take minutes)."""
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)
        return
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        work()
    finally:
        os._exit(0)


def root_dir(parent, name, mode=0o755):
    """Create or open a directory that must belong to root (this process) and be writable by root only."""
    try:
        os.mkdir(name, mode, dir_fd=parent)
    except FileExistsError:
        pass
    try:
        fd = os.open(name, DIR_FLAGS, dir_fd=parent)
    except OSError:
        raise Stop("denied")
    st = os.fstat(fd)
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        os.close(fd)
        raise Stop("denied")
    os.fchmod(fd, mode)
    return fd


def trash(mount, parent, name):
    """Move parent/name into <mount>/.pcui-trash (root only) and delete it there in the background."""
    mfd = open_path(mount)
    tfd = root_dir(mfd, ".pcui-trash", 0o700)
    target = "%d-%d-%s" % (time.time(), os.getpid(), name)
    os.rename(name, target, src_dir_fd=parent, dst_dir_fd=tfd)
    path = mount + "/.pcui-trash/" + target
    background(lambda: subprocess.run(["rm", "-rf", "--one-file-system", "--", path],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))


def stop_running(data, owner_uid):
    """Cancel the build of a queued/building version. env.json can be edited by team members, so the job is only
    cancelled when it belongs to the directory owner (scancel --user filters job ids)."""
    if data.get("status") not in ("queued", "building"):
        return
    job, owner = data.get("job_id"), user_name(owner_uid)
    if isinstance(job, str) and JOB_RE.fullmatch(job) and owner and USER_RE.fullmatch(owner):
        subprocess.run(["scancel", "--user=" + owner, "--", job], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    build = data.get("build_id")
    if isinstance(build, str) and BUILD_ID_RE.fullmatch(build):
        print("@@BUILD " + build)


VIS_MODES = {"team": 0o3770, "all": 0o3775}


def envs_dir(envs_root):
    """(path, fd) of the envs root, which must belong to root and be writable by root only."""
    er = real_root(envs_root)
    efd = open_path(er)
    st = os.fstat(efd)
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        os.close(efd)
        raise Stop("denied")
    return er, efd


def root_owned(fd):
    if os.fstat(fd).st_uid != os.geteuid():
        os.close(fd)
        raise Stop("denied")
    return fd


def team_dir(efd, team, team_root):
    """fd of <envs root>/<team> (2751 root:<team>). When it is missing and team_root is given, it is created with
    the group of the team directory (the same rule as the layout migration: no group-name lookup)."""
    try:
        return root_owned(os.open(team, DIR_FLAGS, dir_fd=efd))
    except FileNotFoundError:
        if team_root == "-":
            raise Stop("not_found")
    except OSError:
        raise Stop("denied")
    gfd = root_owned(open_path(real_root(team_root)))
    gid = os.fstat(gfd).st_gid
    os.close(gfd)
    try:
        os.mkdir(team, 0o700, dir_fd=efd)
    except FileExistsError:
        pass
    fd = root_owned(os.open(team, DIR_FLAGS, dir_fd=efd))
    os.fchown(fd, -1, gid)
    os.fchmod(fd, 0o2751)  # after chown, which clears the set-group-id bit
    return fd


def open_name_dir(tfd, name):
    try:
        return root_owned(os.open(name, DIR_FLAGS, dir_fd=tfd))
    except FileNotFoundError:
        raise Stop("not_found")
    except OSError:
        raise Stop("denied")


def public_link(efd, team, name, visible):
    """Add or remove the index link _public/<team>__<name> -> ../<team>/<name> (relative, as the migration)."""
    pfd = root_dir(efd, PUBLIC, 0o755)
    link, target = team + "__" + name, "../%s/%s" % (team, name)
    try:
        if not visible:
            try:
                os.unlink(link, dir_fd=pfd)
            except FileNotFoundError:
                pass
            return
        try:
            if os.readlink(link, dir_fd=pfd) == target:
                return
        except OSError:
            pass
        tmp = "." + link + ".pcui-tmp"
        try:
            os.unlink(tmp, dir_fd=pfd)
        except FileNotFoundError:
            pass
        os.symlink(target, tmp, dir_fd=pfd)
        os.replace(tmp, link, src_dir_fd=pfd, dst_dir_fd=pfd)
    finally:
        os.close(pfd)


def op_prepare(envs_root, team_root, team, name, visibility):
    """Create the name directory root:<team> with the chosen visibility (3770/3775); an existing name keeps its
    mode. The index link follows the actual mode."""
    need(TEAM_RE, team), need(NAME_RE, name)
    if visibility not in VIS_MODES:
        raise Stop("invalid")
    er, efd = envs_dir(envs_root)
    tfd = team_dir(efd, team, team_root)
    try:
        os.mkdir(name, 0o700, dir_fd=tfd)
        created = True
    except FileExistsError:
        created = False
    nfd = open_name_dir(tfd, name)
    if created:
        os.fchown(nfd, -1, os.fstat(tfd).st_gid)
        os.fchmod(nfd, VIS_MODES[visibility])
    current = visibility_of(nfd)
    public_link(efd, team, name, current == "all")
    print("@@VISIBILITY " + current)
    return "prepared"


def op_visibility(envs_root, team, name, visibility, actor):
    """Change the visibility of every version of a name. With an actor (a member, not a lead or an admin), the
    actor must own at least one version of the name."""
    need(TEAM_RE, team), need(NAME_RE, name)
    if visibility not in VIS_MODES:
        raise Stop("invalid")
    er, efd = envs_dir(envs_root)
    tfd = team_dir(efd, team, "-")
    nfd = open_name_dir(tfd, name)
    if actor != "-":
        need(USER_RE, actor)
        try:
            uid = pwd.getpwnam(actor).pw_uid
        except KeyError:
            raise Stop("not_owner")
        owners = [os.stat(v, dir_fd=nfd, follow_symlinks=False).st_uid for v in subdirs(nfd)]
        if uid not in owners:
            raise Stop("not_owner")
    old = visibility_of(nfd)
    os.fchmod(nfd, VIS_MODES[visibility])
    public_link(efd, team, name, visibility == "all")
    print("@@OLD " + old)
    print("@@VISIBILITY " + visibility)
    return "changed"


def op_delete(envs_root, team, name, version, owner):
    need(TEAM_RE, team), need(NAME_RE, name), need(VERSION_RE, version)
    er, efd = envs_dir(envs_root)
    tfd = team_dir(efd, team, "-")
    parent = open_name_dir(tfd, name)
    try:
        vfd = os.open(version, DIR_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        raise Stop("not_found")
    except OSError:
        raise Stop("denied")
    if owner != "-":
        need(USER_RE, owner)
        try:
            uid = pwd.getpwnam(owner).pw_uid
        except KeyError:
            raise Stop("not_owner")
        if os.fstat(vfd).st_uid != uid:
            raise Stop("not_owner")
    stop_running(read_json(vfd) or {}, os.fstat(vfd).st_uid)
    trash(os.path.dirname(er), parent, version)
    try:  # the last version of a name: the name directory and its index link go too
        os.rmdir(name, dir_fd=tfd)
    except OSError:
        return "deleted"
    public_link(efd, team, name, False)
    return "deleted"


OPS = {"list": (2, list_envs), "prepare": (5, op_prepare), "visibility": (5, op_visibility),
       "delete": (5, op_delete)}


def main(argv):
    if not argv or argv[0] not in OPS or len(argv) - 1 != OPS[argv[0]][0]:
        raise Stop("invalid")
    return OPS[argv[0]][1](*argv[1:])


if __name__ == "__main__":
    sys.exit(run(main, sys.argv[1:]))
'''

# The member program runs through bash (sudo -u <member> /bin/bash -c <script>); the program itself is stdin.
MEMBER_ENV_SCRIPT = (
    "set -euo pipefail\n"
    "export PATH=/opt/slurm/bin:/usr/local/bin:/usr/bin:/bin\n"
    "export LC_ALL=C\n"
    "umask 022\n"
    "exec python3 - \"$@\" <<'PCUI_PY'\n" + MEMBER_ENV_PY + "PCUI_PY\n"
)

RESULT_ERRORS = {
    "invalid": (400, "环境请求无效"),
    "denied": (403, "环境路径不在团队的环境目录内，或没有权限"),
    "not_found": (404, "环境不存在"),
    "not_owner": (403, "只有环境的创建者、团队负责人或管理员可以执行此操作"),
    "exists": (409, "这个环境版本已存在，请换一个版本号"),
    "state": (409, "只有构建失败的版本可以重试"),
    "not_ready": (409, "环境尚未构建成功"),
    "cap": (409, "团队的环境存储空间已达上限，请删除旧版本，或请管理员提高上限"),
    "unavailable": (503, "集群上还没有安装环境构建工具"),
}


class EnvError(MemberCommandError):
    def __init__(self, result, message=None):
        status, default = RESULT_ERRORS.get(result, (502, "环境操作返回了意外的结果"))
        super().__init__(status, message or default)
        self.result = result


def codebuild_client(region):
    return boto3.client("codebuild", region_name=region)


# --- validation -------------------------------------------------------------------------------------------------


def parse_image_ref(ref, config, team):
    """Validate an image reference and return how to build it.

    Allowed: ``docker.io`` (also without a registry), ``nvcr.io``, ``public.ecr.aws``, ``ghcr.io`` (Slurm job,
    ``enroot import``) and the team's own private ECR repository ``<prefix>-<team>-envs`` (CodeBuild). Returns
    ``{registry, path, tag, digest, private, enroot, image_ref}``.
    """
    if not isinstance(ref, str) or not ref or len(ref) > IMAGE_REF_MAX:
        raise ValueError(f"镜像地址长度应为 1 到 {IMAGE_REF_MAX} 个字符")
    registry, rest = None, ref
    first, sep, remainder = ref.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        registry, rest = first, remainder
    ecr_host = f"{config['account_id']}.dkr.ecr.{config['region']}.amazonaws.com"
    private = registry == ecr_host
    if registry is not None and registry not in PUBLIC_REGISTRIES and not private:
        if ECR_HOST_RE.fullmatch(registry):
            raise ValueError(f"私有 ECR 只能使用本团队的仓库 {ecr_host}/{lifecycle_store.ecr_repo_name(config, team)}")
        raise ValueError(f"不允许使用镜像仓库 {registry}。请使用 docker.io、nvcr.io、public.ecr.aws、ghcr.io "
                         f"或本团队的仓库 {ecr_host}/{lifecycle_store.ecr_repo_name(config, team)}")
    tag = digest = None
    path = rest
    if "@" in rest:
        path, _, digest = rest.partition("@")
        if DIGEST_RE.fullmatch(digest) is None:
            raise ValueError("镜像摘要格式应为 sha256:<64 位十六进制>")
    elif ":" in rest:
        path, _, tag = rest.rpartition(":")
        if TAG_RE.fullmatch(tag) is None:
            raise ValueError("镜像标签无效")
    if IMAGE_PATH_RE.fullmatch(path) is None:
        raise ValueError("镜像名称无效：只能使用小写字母、数字和 . _ - / 分隔符")
    if private:
        repo = lifecycle_store.ecr_repo_name(config, team)
        if path != repo:
            prefix = f"{config['ecr_repo_prefix']}-"
            if path.startswith(prefix) and path.endswith("-envs"):
                raise MemberCommandError(403, f"这个 ECR 仓库属于其他团队，请使用 {ecr_host}/{repo}")
            raise ValueError(f"本账号中只能使用本团队的仓库 {ecr_host}/{repo}")
        if tag is None and digest is None:
            tag = "latest"
    suffix = f"@{digest}" if digest else (f":{tag}" if tag else "")
    if registry in (None, "docker.io"):
        enroot = f"docker://{path}{suffix}"
    else:
        enroot = f"docker://{registry}#{path}{suffix}"
    return {"registry": registry or "docker.io", "path": path, "tag": tag, "digest": digest, "private": private,
            "enroot": enroot, "image_ref": f"{registry}/{path}{suffix}" if private else None}


LABELS = {"specification": "环境说明文件", "Dockerfile": "Dockerfile"}


def _token(text, label):
    """gzip+base64 argv token of an untrusted text (reuses the job-script encoder and its limits)."""
    try:
        token, digest, size = encode_script(text)
    except MemberCommandError:
        raise MemberCommandError(413, f"{LABELS.get(label, label)}过大") from None
    if len(token) > ENV_TOKEN_MAX:
        raise MemberCommandError(413, f"{LABELS.get(label, label)}过大")
    return token, digest, size


def dockerfile_b64(content):
    value = base64.b64encode(content.encode("utf-8")).decode("ascii")
    if len(value) > DOCKERFILE_B64_MAX:
        raise MemberCommandError(413, f"Dockerfile 最多 {DOCKERFILE_B64_MAX * 3 // 4} 字节")
    return value


def plan_create(body, config, team, user, created_at):
    """(meta, spec_name, spec_text, builder, image) for a validated ``EnvCreate`` body."""
    source = body["source"]
    image = None
    if source == "image":
        image = parse_image_ref(body["image"], config, team)
        kind, builder, spec_name, spec_text = "container", "codebuild" if image["private"] else "slurm", None, None
    elif source == "dockerfile":
        dockerfile_b64(body["dockerfile"])
        kind, builder, spec_name, spec_text = "container", "codebuild", "Dockerfile", body["dockerfile"]
    else:
        kind, builder = "conda", "slurm"
        if body.get("environment_yml"):
            spec_name, spec_text = "environment.yml", body["environment_yml"]
        else:
            spec_name, spec_text = "requirements.txt", body["requirements_txt"]
    meta = {
        "team": team, "name": body["name"], "version": body["version"], "kind": kind, "source": source,
        "builder": builder, "image": body.get("image") if source == "image" else None,
        "ref": image["enroot"] if image else None, "python": body.get("python"), "status": "queued",
        "failure": None, "job_id": None, "build_id": None, "owner": user, "created_at": created_at,
        "queued_at": created_at, "started_at": None, "finished_at": None, "size_bytes": None,
        "description": body.get("description") or "", "software": body.get("software") or "", "detected": None,
        "attempt": 1,
    }
    return meta, spec_name, spec_text, builder, image


def _root(path):
    validate_member_path(path)
    if ".." in path.split("/"):
        raise ValueError("Invalid root")
    return path


def _names(team, name, version):
    return validate_team_name(team), validate_env_name(name), validate_env_version(version)


# --- argv builders ----------------------------------------------------------------------------------------------


def list_argv(envs_root, team, op="env-list"):
    return [op, _root(envs_root), validate_team_name(team)]


def create_argv(envs_root, shared_root, team, name, version, meta, spec_name, spec_text, cap_bytes, builder):
    _names(team, name, version)
    if builder not in ("slurm", "codebuild") or (spec_name is not None and spec_name not in SPEC_NAMES):
        raise ValueError("Invalid build plan")
    meta_token = base64.b64encode(gzip.compress(json.dumps(meta).encode("utf-8"), mtime=0)).decode("ascii")
    spec_token = _token(spec_text, "specification")[0] if spec_name else "-"
    return ["env-create", _root(envs_root), _root(shared_root), team, name, version, meta_token, spec_name or "-",
            spec_token, str(int(cap_bytes)), builder]


def retry_argv(envs_root, shared_root, team, name, version, cap_bytes):
    _names(team, name, version)
    return ["env-retry", _root(envs_root), _root(shared_root), team, name, version, str(int(cap_bytes))]


def mark_argv(envs_root, team, name, version, build_id=None, failure=None):
    _names(team, name, version)
    if build_id is not None and BUILD_ID_RE.fullmatch(build_id) is None:
        raise ValueError("Invalid build id")
    if failure is not None and FAILURE_RE.fullmatch(failure) is None:
        raise ValueError("Invalid failure text")
    return ["env-mark", _root(envs_root), team, name, version, build_id or "-", failure or "-"]


def _visibility(value):
    if value not in VISIBILITIES:
        raise ValueError("Invalid visibility")
    return value


def _user(value):
    if value is not None and (not isinstance(value, str) or USER_PREFIX_RE.fullmatch(value) is None):
        raise ValueError("Invalid user")
    return value or "-"


def root_argv(op, *args):
    """``list <envs root> <team>``, ``prepare <envs root> <team root> <team> <name> <team|all>``,
    ``visibility <envs root> <team> <name> <team|all> <member|None>``, ``delete <envs root> <team> <name>
    <version> <owner|None>`` (None: a lead or an administrator, no owner check)."""
    if op == "list":
        return list_argv(*args, op="list")
    if op == "prepare":
        envs_root, team_root, team, name, visibility = args
        validate_team_name(team), validate_env_name(name)
        return ["prepare", _root(envs_root), _root(team_root), team, name, _visibility(visibility)]
    if op == "visibility":
        envs_root, team, name, visibility, actor = args
        validate_team_name(team), validate_env_name(name)
        return ["visibility", _root(envs_root), team, name, _visibility(visibility), _user(actor)]
    if op == "delete":
        envs_root, team, name, version, owner = args
        _names(team, name, version)
        return ["delete", _root(envs_root), team, name, version, _user(owner)]
    raise ValueError("Unknown environment operation")


def root_command(argv):
    """Head-node command (root) running ``ENV_ROOT_PY`` with already validated argv."""
    return quote_argv(["python3", "-c", ENV_ROOT_PY, *argv])


# --- runners and parsers ----------------------------------------------------------------------------------------


def markers(text):
    values = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("@@") and " " in line:
            key, value = line[2:].split(" ", 1)
            values.setdefault(key, []).append(value.strip())
    return values


def result_of(values, *expected):
    result = (values.get("RESULT") or [None])[-1]
    if result not in expected:
        raise EnvError(result or "missing")
    return result


def first(values, key):
    return (values.get(key) or [None])[0]


def run_member(ctx, argv, user=None):
    code, stdout, stderr = run_as_member(ctx.config, ctx.cluster_name, ctx.region, user or ctx.user,
                                         MEMBER_ENV_SCRIPT, argv)
    return markers(check_result(code, stdout, stderr))


def run_root(ctx, argv):
    try:
        return markers(run_command(ctx.cluster_name, ctx.region, root_command(argv)))
    except SlurmCommandError as err:
        logger.error(f"Environment operation {argv[0]} failed on the head node: {err.message[:300]}")
        raise MemberCommandError(502, "头节点上的环境操作失败") from None


def parse_list(values):
    data = first(values, "LIST")
    if data is None:
        raise MemberCommandError(502, "无法读取环境列表")
    try:
        parsed = json.loads(gzip.decompress(base64.b64decode(data, validate=True)).decode("utf-8"))
    except (binascii.Error, OSError, EOFError, ValueError):
        raise MemberCommandError(502, "无法读取环境列表") from None
    if not isinstance(parsed, dict):
        raise MemberCommandError(502, "无法读取环境列表")
    return {"envs": list(parsed.get("envs") or []), "truncated": bool(parsed.get("truncated"))}


# --- CodeBuild --------------------------------------------------------------------------------------------------


def efs_subpath(team):
    """``/envs/<team>`` relative to the file system root (the cluster and the env-build project mount the root)."""
    return f"/envs/{validate_team_name(team)}"


def start_build(config, team, name, version, source, owner_uid, team_gid, image_ref=None, dockerfile=None):
    """Start the env-build project. StartBuild cannot override the project's file system locations, so the project
    mounts the EFS root and the buildspec opens only envs/TEAM/ENV_NAME/ENV_VERSION, without following symlinks.
    The version directory exists already (created by the member). Returns the build id."""
    _names(team, name, version)
    if not (str(owner_uid).isdigit() and str(team_gid).isdigit()):
        raise ValueError("Invalid owner or group id")
    variables = [("TEAM", team), ("ENV_NAME", name), ("ENV_VERSION", version), ("SOURCE", source),
                 ("OWNER_UID", str(owner_uid)), ("TEAM_GID", str(team_gid))]
    if source == "ecr":
        variables.append(("IMAGE_REF", image_ref))
    elif source == "dockerfile":
        variables.append(("DOCKERFILE_B64", dockerfile_b64(dockerfile)))
    else:
        raise ValueError("Invalid CodeBuild source")
    response = codebuild_client(config["region"]).start_build(
        projectName=config["env_build_project"],
        environmentVariablesOverride=[{"name": k, "value": v, "type": "PLAINTEXT"} for k, v in variables],
    )
    return response["build"]["id"]


def build_matches(build, team, name, version):
    """A build id read from env.json (editable by team members) only counts when the build really is that
    environment's build: its TEAM / ENV_NAME / ENV_VERSION variables must match."""
    variables = {v.get("name"): v.get("value") for v in (build.get("environment") or {}).get("environmentVariables")
                 or []}
    return (variables.get("TEAM"), variables.get("ENV_NAME"), variables.get("ENV_VERSION")) == (team, name, version)


def stop_build(config, build_id, team, name, version):
    """Stop the CodeBuild build of a deleted version (best effort, only that version's own build)."""
    try:
        client = codebuild_client(config["region"])
        builds = client.batch_get_builds(ids=[build_id]).get("builds", [])
        if builds and build_matches(builds[0], team, name, version):
            client.stop_build(id=build_id)
    except (ClientError, BotoCoreError) as err:
        logger.warning(f"Could not stop CodeBuild build: {type(err).__name__}")


def merge_build_status(config, entries):
    """Add the CodeBuild phase (``progress``) to queued/building entries and mark builds that ended without
    updating env.json as failed. Best effort."""
    active = {e["build_id"]: e for e in entries
              if e.get("build_id") and e.get("status") in ("queued", "building")}
    if not active:
        return entries
    try:
        builds = codebuild_client(config["region"]).batch_get_builds(ids=list(active)[:100]).get("builds", [])
    except (ClientError, BotoCoreError) as err:
        logger.warning(f"CodeBuild status unavailable: {type(err).__name__}")
        return entries
    for build in builds:
        entry = active.get(build.get("id"))
        if entry is None or not build_matches(build, entry.get("team"), entry.get("name"), entry.get("version")):
            continue
        phase, status = build.get("currentPhase"), build.get("buildStatus")
        entry["progress"] = phase
        if status in CODEBUILD_FAILED:
            entry["status"] = "failed"
            entry["failure"] = entry.get("failure") or (
                f"CodeBuild 构建{CODEBUILD_FAILED_ZH.get(status, status)}（阶段 {phase}）")
    return entries


# --- views ------------------------------------------------------------------------------------------------------

CODEBUILD_FAILED_ZH = {"FAILED": "失败", "FAULT": "出错", "STOPPED": "已停止", "TIMED_OUT": "超时"}
# env.json failure texts written by the portal and the cluster programs are ASCII (they pass through argv
# allowlists); the view shows them in Chinese. Other texts (e.g. from the build itself) are shown as they are.
FAILURE_ZH = (
    (re.compile(r"Could not start the CodeBuild build \((.*)\)"), "无法启动 CodeBuild 构建（{0}）"),
    (re.compile(r"Could not submit the build job: (.*)", re.S), "无法提交构建作业：{0}"),
)


def failure_text(text):
    """Chinese text for a known failure message; anything else unchanged."""
    if not isinstance(text, str):
        return text
    for pattern, template in FAILURE_ZH:
        match = pattern.fullmatch(text)
        if match:
            return template.format(*match.groups())
    return text


VIEW_FIELDS = ("kind", "source", "builder", "image", "ref", "status", "failure", "created_at", "queued_at",
               "started_at", "finished_at", "size_bytes", "description", "software", "python", "attempt", "job_id",
               "build_id", "progress")


def usage(team_entries, cap_gb):
    total = sum(e.get("size_bytes") or 0 for e in team_entries)
    cap_gb = cap_gb or 0
    view = {"total_bytes": total, "cap_gb": cap_gb or None, "percent": None, "level": None}
    if cap_gb:
        percent = round(total * 100 / (cap_gb * CAP_UNIT), 1)
        view["percent"] = percent
        view["level"] = "exceeded" if percent >= FULL_PERCENT else ("warning" if percent >= WARN_PERCENT else None)
    return view


def usage_warning(view):
    if view["level"] == "exceeded":
        return f"团队已用环境存储上限 {view['cap_gb']} GB 的 {view['percent']} %"
    if view["level"] == "warning":
        return f"团队已用环境存储上限 {view['cap_gb']} GB 的 {view['percent']} %，请尽快删除旧版本"
    return None


def owned_names(entries, team, user):
    """Names of the team of which ``user`` created at least one version (they may change its visibility)."""
    if not user:
        return set()
    return {e.get("name") for e in entries
            if e.get("team") == team and (e.get("dir_owner") or e.get("owner")) == user}


def entry_view(entry, access, owned=()):
    """Public entry: identifiers, visibility, status fields, the build log path and what the caller may do, with a
    reason (Chinese) for every action the caller may not take. ``owned`` are the names the calling member created
    a version of (``owned_names``)."""
    team, name = entry.get("team"), entry.get("name")
    owner = entry.get("dir_owner") or entry.get("owner")
    view = {"id": f"team/{team}/{name}/{entry.get('version')}", "scope": "team", "team": team, "name": name,
            "version": entry.get("version"), "path": entry.get("path"), "owner": owner,
            "visibility": entry.get("visibility") if entry.get("visibility") in VISIBILITIES else "team"}
    view.update({key: entry.get(key) for key in VIEW_FIELDS})
    view["status"] = view["status"] or "unknown"
    view["failure"] = failure_text(view["failure"])
    own_team = team == access.team
    # members read build logs below their own team's environments only (the file routes' readable roots)
    view["log_path"] = f"{entry['path']}/build.log" if own_team and entry.get("has_log") and entry.get("path") else None
    manager = access.admin or (access.lead and own_team)
    mine = own_team and access.member_user is not None and owner == access.member_user
    creator = own_team and access.member_user is not None and name in owned
    can = {"delete": bool(manager or mine), "retry": bool(mine and view["status"] == "failed")}
    view["can"] = can
    view["can_change_visibility"] = bool(manager or creator)
    reasons = {}
    if not can["delete"]:
        reasons["delete"] = OTHER_TEAM_REASON if not own_team else "只有这个版本的创建者、团队负责人或管理员可以删除"
    if not can["retry"]:
        if not own_team:
            reasons["retry"] = OTHER_TEAM_REASON
        elif not mine:
            reasons["retry"] = "只有创建者可以重新构建"
        else:
            reasons["retry"] = "只有构建失败的版本可以重新构建"
    if not view["can_change_visibility"]:
        reasons["visibility"] = (OTHER_TEAM_REASON if not own_team
                                 else "只有这个环境的创建者、团队负责人或管理员可以更改可见范围")
    view["disabled_reasons"] = reasons
    return view


def dropdown(entries, team=None):
    """Environments a job can run in (succeeded) for the submit form: the caller's team first, then the 所有人
    environments of other teams, grouped by team."""
    items = [{"scope": "team", "team": e["team"], "name": e["name"], "version": e["version"],
              "kind": e.get("kind"), "description": e.get("description"), "detected": e.get("detected"),
              "visibility": e.get("visibility") if e.get("visibility") in VISIBILITIES else "team"}
             for e in entries if e.get("status") == "succeeded"]
    return sorted(items, key=lambda e: (e["team"] != team, e["team"], e["name"], e["version"]))
