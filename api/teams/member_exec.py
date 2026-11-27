# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Run member commands on the cluster LOGIN node as the member's own Linux user.

* The login node is discovered at call time by its ParallelCluster tags; there is never a fallback to the head
  node (this module does not import the head-node resolver).
* Commands are sent with credentials of the member execution role, which IAM allows to reach login nodes only.
* ``sudo -n -H -u <user>`` drops from root to the member, so Linux permissions and Slurm associations apply
  exactly as they do on the command line.
"""
import re
import shlex
import time

import boto3
from botocore.exceptions import ClientError

from api.pcm_globals import logger
from api.teams.cache import TTLCache
from api.teams.validation import RESERVED_USERS, USER_PREFIX_RE

SESSION_TTL = 50 * 60
LOGIN_NODE_TTL = 30
POLL_INTERVAL = 0.5
COMMAND_TIMEOUT = 20  # API Gateway stops waiting after 29 s
SSM_TIMEOUT_SECONDS = 60
INSTANCE_ID_RE = re.compile(r"i-[0-9a-f]{8,17}")

EXIT_PATH_DENIED = 4
EXIT_NOT_FOUND = 5
EXIT_NOT_OWNER = 6
EXIT_TOO_LARGE = 7
EXIT_NO_PERMISSION = 8
EXIT_ENV_NOT_READY = 9
EXIT_MESSAGES = {
    EXIT_PATH_DENIED: (403, "路径不在允许的目录内（只能访问团队目录和个人主目录）"),
    EXIT_NOT_FOUND: (404, "文件或目录不存在"),
    EXIT_NOT_OWNER: (403, "作业不存在或不属于您"),
    EXIT_TOO_LARGE: (413, "文件过大"),
    EXIT_NO_PERMISSION: (403, "没有读取权限"),
    EXIT_ENV_NOT_READY: (403, "该环境仅对其所属团队可见，或尚未构建成功"),
}
SLURM_ERROR_PREFIX_RE = re.compile(r"^(?:sbatch|srun|salloc|scancel): error: ")
GENERIC_SUBMIT_FAILURE_RE = re.compile(r"^Batch job submission failed: ")
UNKNOWN_USER_MESSAGE = "集群账号尚未生效，请联系管理员"
LOGIN_NODE_UNAVAILABLE = "登录节点暂不可用"

_session_cache = TTLCache(SESSION_TTL)
_login_node_cache = TTLCache(LOGIN_NODE_TTL)


class MemberCommandError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class LoginNodeUnavailable(MemberCommandError):
    def __init__(self):
        super().__init__(503, LOGIN_NODE_UNAVAILABLE)


def validate_member_user(user):
    if not isinstance(user, str) or USER_PREFIX_RE.fullmatch(user) is None or user in RESERVED_USERS:
        raise ValueError("成员用户名无效")
    return user


def _sts_client(region):
    return boto3.client("sts", region_name=region)


def member_session(config, user):
    """boto3 session of the member execution role (session name ``pcui-member-<user>`` for CloudTrail)."""
    validate_member_user(user)
    role_arn = config["member_exec_role_arn"]

    def _assume():
        session_name = re.sub(r"[^\w+=,.@-]", "-", f"pcui-member-{user}")[:64]
        creds = _sts_client(config["region"]).assume_role(
            RoleArn=role_arn, RoleSessionName=session_name, DurationSeconds=3600,
        )["Credentials"]
        return boto3.session.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )

    return _session_cache.get_or_compute((role_arn, user), _assume)


def login_node_filters(cluster_name):
    return [
        {"Name": "tag:parallelcluster:cluster-name", "Values": [cluster_name]},
        {"Name": "tag:parallelcluster:node-type", "Values": ["LoginNode"]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ]


def _discover_login_node(session, cluster_name, region):
    ec2 = session.client("ec2", region_name=region)
    ids, kwargs = [], {"Filters": login_node_filters(cluster_name)}
    for _ in range(10):
        resp = ec2.describe_instances(**kwargs)
        for reservation in resp.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_id = instance.get("InstanceId", "")
                if INSTANCE_ID_RE.fullmatch(instance_id):
                    ids.append(instance_id)
        if not resp.get("NextToken"):
            break
        kwargs["NextToken"] = resp["NextToken"]
    if not ids:
        raise LoginNodeUnavailable()
    return sorted(ids)[0]


def login_node(session, cluster_name, region):
    return _login_node_cache.get_or_compute(
        (cluster_name, region), lambda: _discover_login_node(session, cluster_name, region))


def build_command(user, script, argv):
    """``sudo -n -H -u <user> -- /bin/bash --noprofile --norc -c <script> pcui-member <argv...>``, all quoted."""
    validate_member_user(user)
    tokens = ["sudo", "-n", "-H", "-u", user, "--", "/bin/bash", "--noprofile", "--norc", "-c", script,
              "pcui-member", *[str(a) for a in argv]]
    return " ".join(shlex.quote(token) for token in tokens)


def _send(ssm, instance_id, command, op):
    return ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment=f"pcui-member {op}",
        TimeoutSeconds=SSM_TIMEOUT_SECONDS,
        Parameters={"commands": [command]},
    )["Command"]["CommandId"]


def _wait(ssm, command_id, instance_id):
    deadline = time.monotonic() + COMMAND_TIMEOUT
    while True:
        time.sleep(POLL_INTERVAL)
        try:
            status = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "InvocationDoesNotExist":
                raise
            status = {"Status": "Pending"}
        if status.get("Status") not in ("Pending", "InProgress", "Delayed"):
            return status
        if time.monotonic() > deadline:
            raise MemberCommandError(504, "命令执行超时，请稍后重试")


def run_as_member(config, cluster_name, region, user, script, argv):
    """Run ``script`` with ``argv`` on the login node as ``user``. Returns ``(exit_code, stdout, stderr)``."""
    command = build_command(user, script, argv)
    op = str(argv[0]) if argv else "-"
    session = member_session(config, user)
    ssm = session.client("ssm", region_name=region)
    for attempt in (1, 2):
        instance_id = login_node(session, cluster_name, region)
        try:
            command_id = _send(ssm, instance_id, command, op)
            break
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") == "InvalidInstanceId" and attempt == 1:
                _login_node_cache.clear()  # the login node was replaced: discover it again
                continue
            if err.response.get("Error", {}).get("Code") == "InvalidInstanceId":
                raise LoginNodeUnavailable() from None
            raise
    logger.info(f"Submitted member SSM command {command_id} ({op})")
    status = _wait(ssm, command_id, instance_id)
    return status.get("ResponseCode", -1), status.get("StandardOutputContent", ""), status.get("StandardErrorContent", "")


def check_result(code, stdout, stderr):
    """Map the script exit code to an HTTP error, or return stdout."""
    if code == 0:
        return stdout
    if code in EXIT_MESSAGES:
        raise MemberCommandError(*EXIT_MESSAGES[code])
    if "unknown user" in (stderr or "") or "unknown user" in (stdout or ""):
        raise MemberCommandError(403, UNKNOWN_USER_MESSAGE)
    message = slurm_error_message(stderr)[:500] or f"命令执行失败（退出码 {code}）"
    raise MemberCommandError(400, message)


def slurm_error_message(stderr):
    """The rejection sentence of a Slurm client error without the ``sbatch: error: `` prefix.

    A job_submit plugin rejection prints its own sentence followed by sbatch's generic "Batch job submission
    failed: ..." line; the generic line is only kept when it is the only one.
    """
    lines = [SLURM_ERROR_PREFIX_RE.sub("", line).strip() for line in (stderr or "").splitlines()]
    lines = [line for line in lines if line]
    specific = [line for line in lines if not GENERIC_SUBMIT_FAILURE_RE.match(line)]
    return " ".join(specific or lines)
