# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Team lifecycle configuration and per-team records in SSM Parameter Store.

* ``<record_prefix>/config`` (written by the ``pcui-team-lifecycle`` stack, version 2) holds directory, EFS, ECR,
  data table, topic, secret and cluster settings. It never contains secret values.
* ``<record_prefix>/teams/<team>`` holds the resource names of one team (so teama can map to the AD group
  ``teamA`` and ``/efs/teamA``) plus the provisioning progress.

Every value read back is re-validated before it is used to build a command.
"""
import json
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from api.teams.cache import TTLCache
from api.teams.validation import (
    AD_GROUP_RE,
    DOMAIN_RE,
    EMAIL_RE,
    ENV_CAP_MAX_GB,
    INSTANCE_TYPE_RE,
    MAX_TYPE_LIMITS,
    TYPE_LIMIT_MAX,
    TYPE_LIMIT_MIN,
    USER_PREFIX_RE,
    is_valid_name,
)
from api.validation.validators import is_alphanumeric_with_hyphen

CONFIG_PARAMETER = os.getenv("PCUI_TEAMS_CONFIG_PARAMETER", "/pcui-teams/config")
CONFIG_TTL = 300
RECORD_MAX_BYTES = 4000
CONFIG_VERSION = 2
RECORD_VERSION = 2
STATES = ("provisioning", "active", "deleted")
MEMBER_GROUP_PREFIX = "member-"
TEAM_GROUP_PREFIX = "team-"

REGION_RE = re.compile(r"[a-z]{2}(-gov)?-[a-z]+-\d")
ACCOUNT_RE = re.compile(r"\d{12}")
PROJECT_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
DIRECTORY_RE = re.compile(r"d-[0-9a-f]{10}")
DC_RE = re.compile(r"(\d{1,3}(\.\d{1,3}){3})|([a-z0-9-]+(\.[a-z0-9-]+)+)")
AD_ADMIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
SECRET_ARN_RE = re.compile(r"arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]{1,512}")
EFS_ID_RE = re.compile(r"fs-[0-9a-f]{8,17}")
EFS_DNS_RE = re.compile(r"fs-[0-9a-f]{8,17}\.efs\.[a-z0-9-]+\.amazonaws\.com(\.cn)?")
MOUNT_RE = re.compile(r"/[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*")
TEAM_SEGMENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
ECR_REPO_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
TABLE_RE = re.compile(r"[A-Za-z0-9_.-]{3,255}")
TOPIC_ARN_RE = re.compile(r"arn:aws[a-z-]*:sns:[a-z0-9-]+:\d{12}:[A-Za-z0-9_-]{1,256}")
CODEBUILD_PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,254}")
LOG_GROUP_RE = re.compile(r"[A-Za-z0-9_/#.-]{1,512}")
SECRET_PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.-]{0,63}")
RECORD_PREFIX_RE = re.compile(r"/[A-Za-z0-9_-]+")
TAG_VALUE_RE = re.compile(r"[A-Za-z0-9 _.:/=+@-]{1,128}")
ROLE_ARN_RE = re.compile(r"arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]{1,128}")
TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00")

_config_cache = TTLCache(CONFIG_TTL)


class LifecycleConfigError(ValueError):
    """The lifecycle configuration or a team record is invalid."""


def ssm_client():
    return boto3.client("ssm")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check(condition, field):
    if not condition:
        raise LifecycleConfigError(f"Invalid team lifecycle configuration: {field}")


def _match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _list_of(pattern, value):
    return isinstance(value, list) and len(value) > 0 and all(_match(pattern, item) for item in value)


def validate_config(config):
    _check(isinstance(config, dict) and config.get("version") == CONFIG_VERSION, "version")
    _check(_match(REGION_RE, config.get("region")), "region")
    _check(_match(ACCOUNT_RE, config.get("account_id")), "account_id")
    _check(_match(PROJECT_RE, config.get("project")), "project")
    clusters = config.get("clusters")
    _check(isinstance(clusters, list) and clusters and all(
        isinstance(c, str) and len(c) <= 60 and is_alphanumeric_with_hyphen(c) for c in clusters), "clusters")
    _check(_match(DIRECTORY_RE, config.get("directory_id")), "directory_id")
    _check(_match(DOMAIN_RE, config.get("ad_domain")), "ad_domain")
    _check(_list_of(DC_RE, config.get("ad_domain_controllers")), "ad_domain_controllers")
    _check(_match(AD_ADMIN_RE, config.get("ad_admin_user")), "ad_admin_user")
    _check(_match(SECRET_ARN_RE, config.get("ad_admin_secret_arn")), "ad_admin_secret_arn")
    _check(_match(EFS_ID_RE, config.get("efs_id")), "efs_id")
    _check(_match(MOUNT_RE, config.get("storage_mount")), "storage_mount")
    _check(_match(EFS_DNS_RE, config.get("efs_dns")) and config["efs_dns"].startswith(config["efs_id"] + "."),
           "efs_dns")
    _check(_match(PROJECT_RE, config.get("ecr_repo_prefix")), "ecr_repo_prefix")
    _check(_match(SECRET_PREFIX_RE, config.get("secret_prefix")), "secret_prefix")
    _check(_match(RECORD_PREFIX_RE, config.get("record_prefix")), "record_prefix")
    tags = config.get("tags")
    _check(isinstance(tags, dict) and set(tags) == {"Project", "CostCenter"}
           and all(_match(TAG_VALUE_RE, v) for v in tags.values()), "tags")
    _check(_list_of(DOMAIN_RE, config.get("email_domains")), "email_domains")
    _check(_match(ROLE_ARN_RE, config.get("member_exec_role_arn")), "member_exec_role_arn")
    _check(_match(TABLE_RE, config.get("data_table")), "data_table")
    _check(_match(TOPIC_ARN_RE, config.get("portal_topic_arn")), "portal_topic_arn")
    _check(_match(TOPIC_ARN_RE, config.get("admin_topic_arn")), "admin_topic_arn")
    _check(_match(CODEBUILD_PROJECT_RE, config.get("env_build_project")), "env_build_project")
    _check(_match(LOG_GROUP_RE, config.get("codebuild_log_group")), "codebuild_log_group")
    return config


def shared_root(config):
    """``/efs/shared``: environment tools (builder, activate) and shared data sets."""
    return f"{config['storage_mount']}/shared"


def envs_root(config):
    """``/efs/envs``: runtime environments of every team, ``<envs root>/<team>/<name>/<version>``."""
    return f"{config['storage_mount']}/envs"


def ecr_repo_name(config, team):
    return f"{config['ecr_repo_prefix']}-{team}-envs"


def _load_config():
    try:
        value = ssm_client().get_parameter(Name=CONFIG_PARAMETER)["Parameter"]["Value"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise
    try:
        config = json.loads(value)
    except ValueError:
        raise LifecycleConfigError("Invalid team lifecycle configuration: not JSON") from None
    return validate_config(config)


def load_config():
    """Return the validated lifecycle configuration, or None when the side stack is not deployed."""
    return _config_cache.get_or_compute(CONFIG_PARAMETER, _load_config)


# --- records ----------------------------------------------------------------------------------------------------


def record_name(config, team):
    if not is_valid_name(team):
        raise ValueError("Invalid team name")
    return f"{config['record_prefix']}/teams/{team}"


def default_names(config, team):
    return {
        "ad_group": team,
        "team_path": f"{config['storage_mount']}/{team}",
        "ecr_repo": ecr_repo_name(config, team),
        "cognito_group": f"{TEAM_GROUP_PREFIX}{team}",
        "member_group": f"{MEMBER_GROUP_PREFIX}{team}",
        "qos": f"{team}-std",
    }


def new_record(config, team, spec, previous=None):
    """A ``provisioning`` record. A ``deleted`` record that is re-created keeps its resource names and history."""
    now = _now()
    record = {
        "version": RECORD_VERSION,
        "team": team,
        "state": "provisioning",
        **default_names(config, team),
        "ecr_ready": False,
        "env_storage_cap_gb": 0,
        "ad_users_created": [],
        "ad_secrets": [],
        "password_set": [],
        "cognito_users_created": [],
        "pcui_secrets": [],
        "spec": spec,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    if previous:
        for key in ("ad_group", "team_path", "ecr_ready", "env_storage_cap_gb", "ad_users_created", "ad_secrets",
                    "password_set", "cognito_users_created", "pcui_secrets", "created_at"):
            if previous.get(key) is not None:
                record[key] = previous[key]
    validate_record(record, config)
    return record


def _check_record(condition, field):
    if not condition:
        raise LifecycleConfigError(f"Invalid team record: {field}")


def validate_record(record, config):
    _check_record(isinstance(record, dict) and record.get("version") == RECORD_VERSION, "version")
    team = record.get("team")
    _check_record(is_valid_name(team), "team")
    _check_record(record.get("state") in STATES, "state")
    _check_record(_match(AD_GROUP_RE, record.get("ad_group")), "ad_group")
    team_path = record.get("team_path")
    mount = config["storage_mount"]
    _check_record(isinstance(team_path, str) and team_path.startswith(mount + "/")
                  and _match(TEAM_SEGMENT_RE, team_path[len(mount) + 1:])
                  and team_path != shared_root(config), "team_path")
    _check_record(record.get("ecr_repo") == ecr_repo_name(config, team) and _match(ECR_REPO_RE, record["ecr_repo"])
                  and len(record["ecr_repo"]) <= 256, "ecr_repo")
    _check_record(isinstance(record.get("ecr_ready"), bool), "ecr_ready")
    cap = record.get("env_storage_cap_gb")
    _check_record(isinstance(cap, int) and not isinstance(cap, bool) and 0 <= cap <= ENV_CAP_MAX_GB,
                  "env_storage_cap_gb")
    _check_record(record.get("cognito_group") == f"{TEAM_GROUP_PREFIX}{team}", "cognito_group")
    _check_record(record.get("member_group") == f"{MEMBER_GROUP_PREFIX}{team}", "member_group")
    _check_record(record.get("qos") == f"{team}-std", "qos")
    for key in ("ad_users_created", "password_set"):
        _check_record(isinstance(record.get(key), list) and all(_match(USER_PREFIX_RE, u) for u in record[key]), key)
    _check_record(isinstance(record.get("cognito_users_created"), list)
                  and all(_match(EMAIL_RE, e) for e in record["cognito_users_created"]), "cognito_users_created")
    for key in ("ad_secrets", "pcui_secrets"):
        _check_record(isinstance(record.get(key), list)
                      and all(isinstance(s, str) and s.startswith(config["secret_prefix"]) for s in record[key]), key)
    spec = record.get("spec")
    if spec is not None:
        _check_record(isinstance(spec, dict) and isinstance(spec.get("members"), list), "spec")
        for member in spec["members"]:
            _check_record(isinstance(member, dict) and _match(EMAIL_RE, member.get("email"))
                          and _match(USER_PREFIX_RE, member.get("user"))
                          and member["email"].split("@", 1)[0] == member["user"]
                          and isinstance(member.get("link_existing"), bool), "spec.members")
        _check_record(spec.get("lead") is None or _match(EMAIL_RE, spec.get("lead")), "spec.lead")
        limits = spec.get("type_limits")
        _check_record(isinstance(limits, dict) and len(limits) <= MAX_TYPE_LIMITS and all(
            _match(INSTANCE_TYPE_RE, k) and isinstance(v, int) and not isinstance(v, bool)
            and TYPE_LIMIT_MIN <= v <= TYPE_LIMIT_MAX for k, v in limits.items()), "spec.type_limits")
    for key in ("created_at", "updated_at"):
        _check_record(_match(TIME_RE, record.get(key)), key)
    _check_record(record.get("deleted_at") is None or _match(TIME_RE, record.get("deleted_at")), "deleted_at")
    return record


def get_record(config, team):
    try:
        value = ssm_client().get_parameter(Name=record_name(config, team))["Parameter"]["Value"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        raise
    try:
        record = json.loads(value)
    except ValueError:
        raise LifecycleConfigError(f"Invalid team record for '{team}': not JSON") from None
    validate_record(record, config)
    if record["team"] != team:
        raise LifecycleConfigError(f"Invalid team record for '{team}': team mismatch")
    return record


def put_record(config, record):
    record["updated_at"] = _now()
    validate_record(record, config)
    value = json.dumps(record, separators=(",", ":"), sort_keys=True)
    if len(value.encode("utf-8")) > RECORD_MAX_BYTES:
        raise LifecycleConfigError(f"Team record for '{record['team']}' exceeds {RECORD_MAX_BYTES} bytes")
    ssm_client().put_parameter(Name=record_name(config, record["team"]), Value=value, Type="String",
                               Tier="Standard", Overwrite=True)
    return record


def list_records(config):
    client = ssm_client()
    records, kwargs = [], {"Path": f"{config['record_prefix']}/teams", "Recursive": False}
    for _ in range(50):
        resp = client.get_parameters_by_path(**kwargs)
        for param in resp.get("Parameters", []):
            try:
                record = validate_record(json.loads(param["Value"]), config)
            except (ValueError, LifecycleConfigError):
                continue  # a malformed record is ignored rather than breaking the list
            records.append(record)
        if not resp.get("NextToken"):
            break
        kwargs["NextToken"] = resp["NextToken"]
    return sorted(records, key=lambda r: r["team"])


def public_view(record):
    """Record fields that are safe to show to administrators (no lead email, no member emails)."""
    keys = ("team", "state", "ad_group", "team_path", "ecr_repo", "cognito_group", "member_group", "qos",
            "env_storage_cap_gb", "created_at", "updated_at", "deleted_at")
    return {key: record.get(key) for key in keys}
