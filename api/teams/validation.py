# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Allowlist validation for the team quota API.

Every value that ends up in a Slurm command on the head node passes through
one of the functions below. Patterns use ``re.fullmatch`` so a trailing newline
cannot slip through (``$`` in ``re.match`` would accept it).
"""
import math
import re
from datetime import date, timedelta

from marshmallow import INCLUDE, RAISE, Schema, ValidationError, fields, validate, validates_schema

from api.validation.validators import aws_region_validator, is_alphanumeric_with_hyphen

NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
# Linux / Slurm / AD sAMAccountName user: the lowercased local part of the user's email (sAMAccountName <= 20).
USER_PREFIX_RE = re.compile(r"[a-z][a-z0-9._-]{0,19}")
EMAIL_RE = re.compile(r"[a-z0-9._-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)+")
EMAIL_MAX_LENGTH = 128
DOMAIN_RE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
# Teams created by the lifecycle: lowercase, no underscore (they become AD groups and ECR repository names).
LIFECYCLE_TEAM_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
AD_GROUP_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
JOB_ID_RE = re.compile(r"\d{1,10}(_\d{1,6})?")
PARTITION_RE = re.compile(r"[a-z][a-z0-9-]{0,29}")
JOB_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_PATH_LENGTH = 1024
MAX_LIFECYCLE_MEMBERS = 10
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
MONTH_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
# Family may contain hyphens (p6-b200.48xlarge, p6e-gb200.36xlarge); always used with fullmatch.
INSTANCE_TYPE_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*(\.[a-z0-9-]+)+")
# Runtime environments (catalog): /efs/envs/<team>/<name>/<version>
ENV_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,31}")
ENV_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,31}")
ENV_SCOPES = ("team", "shared")  # "shared" (published copies, removed) is still accepted in old references
ENV_VISIBILITIES = ("team", "all")
UUID_HEX_RE = re.compile(r"[0-9a-f]{32}")
AUDIT_ACTION_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
TEMPLATE_NAME_RE = re.compile(r"[^\x00-\x1f\x7f]{1,64}")

# Slurm objects that must never be modified or deleted through the API ("shared" and "envs" are also directories
# of the storage mount: /efs/shared, /efs/envs).
RESERVED_NAMES = frozenset({"root", "pcdefault", "normal", "borrow", "envbuild", "shared", "envs"})
RESERVED_USERS = frozenset({"root", "slurm"})
QOS_SUFFIX = "-std"
BORROW_QOS = "borrow"
ENVBUILD_QOS = "envbuild"
BUILD_PARTITION = "build"
QOS_NAME_MAX = 63

FAIRSHARE_MIN, FAIRSHARE_MAX = 1, 10000
FAIRSHARE_DEFAULT = 50  # "高级选项" in the team forms; a team created without it gets 50
TYPE_LIMIT_MIN, TYPE_LIMIT_MAX = 0, 1000  # machines of one instance type a team may run at once; 0 = not allowed
MAX_TYPE_LIMITS = 64
BUDGET_MAX_USD = 10_000_000
ENV_CAP_MAX_GB = 100_000
MAX_RANGE_DAYS = 366
MONTHS_MIN, MONTHS_MAX = 1, 12


def is_valid_name(value) -> bool:
    return isinstance(value, str) and NAME_RE.fullmatch(value) is not None


def validate_team_name(value, allow_reserved=False) -> str:
    if not is_valid_name(value):
        raise ValueError("Invalid team name: must match ^[a-z][a-z0-9_-]{0,31}$")
    if not allow_reserved and value in RESERVED_NAMES:
        raise ValueError(f"Team name '{value}' is reserved")
    return value


def validate_user_name(value) -> str:
    if not isinstance(value, str) or USER_PREFIX_RE.fullmatch(value) is None:
        raise ValueError("Invalid user name: must match ^[a-z][a-z0-9._-]{0,19}$")
    if value in RESERVED_USERS:
        raise ValueError(f"User name '{value}' is reserved")
    return value


def validate_lifecycle_team_name(value) -> str:
    validate_team_name(value)
    if LIFECYCLE_TEAM_RE.fullmatch(value) is None:
        raise ValueError("Invalid team name: must match ^[a-z][a-z0-9-]{0,31}$ (lowercase, no '_')")
    return value


def normalize_email(value, domains=None):
    """Return ``(email, user)``: the lowercased email and its local part, which is the AD/Linux/Slurm user name.

    ``domains`` is the allowlist of customer email domains; None skips the domain check (schema-level validation).
    """
    if not isinstance(value, str) or len(value) > EMAIL_MAX_LENGTH:
        raise ValueError("Invalid email address")
    email = value.lower()
    if EMAIL_RE.fullmatch(email) is None:
        raise ValueError("Invalid email address: use lowercase letters, digits, '.', '_' or '-' before the '@'")
    local, domain = email.split("@", 1)
    if domains is not None and domain not in domains:
        raise ValueError(f"Email domain '{domain}' is not allowed")
    if USER_PREFIX_RE.fullmatch(local) is None:
        raise ValueError(
            "The part before '@' becomes the cluster user name and must match ^[a-z][a-z0-9._-]{0,19}$"
        )
    if local in RESERVED_USERS:
        raise ValueError(f"User name '{local}' is reserved")
    return email, local


def validate_member_path(value) -> str:
    if (not isinstance(value, str) or not value.startswith("/") or len(value) > MAX_PATH_LENGTH
            or CONTROL_CHARS_RE.search(value)):
        raise ValueError("Invalid path: must be absolute, at most 1024 characters, without control characters")
    return value


def validate_job_id(value) -> str:
    if not isinstance(value, str) or JOB_ID_RE.fullmatch(value) is None:
        raise ValueError("Invalid job id")
    return value


def validate_int(value, minimum, maximum, name="value") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def validate_instance_type(value) -> str:
    if not isinstance(value, str) or INSTANCE_TYPE_RE.fullmatch(value) is None:
        raise ValueError(f"Invalid instance type: {value!r}")
    return value


def validate_partition(value) -> str:
    if not isinstance(value, str) or PARTITION_RE.fullmatch(value) is None:
        raise ValueError("Invalid queue name")
    return value


def validate_env_name(value) -> str:
    if not isinstance(value, str) or ENV_NAME_RE.fullmatch(value) is None:
        raise ValueError("Invalid environment name: must match ^[a-z][a-z0-9-]{0,31}$")
    return value


def validate_env_version(value) -> str:
    if not isinstance(value, str) or ENV_VERSION_RE.fullmatch(value) is None:
        raise ValueError("Invalid environment version: must match ^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$")
    return value


def parse_date(value) -> date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ValueError("Dates must use the YYYY-MM-DD format")
    return date.fromisoformat(value)


def validate_month(value) -> str:
    if not isinstance(value, str) or MONTH_RE.fullmatch(value) is None:
        raise ValueError("Months must use the YYYY-MM format")
    return value


# --- marshmallow field validators -------------------------------------------------------------------------------


def _as_marshmallow(func):
    def _validator(value):
        try:
            func(value)
        except ValueError as err:
            raise ValidationError(str(err)) from None

    return _validator


team_name_validator = _as_marshmallow(validate_team_name)
any_team_name_validator = _as_marshmallow(lambda v: validate_team_name(v, allow_reserved=True))
user_name_validator = _as_marshmallow(validate_user_name)
instance_type_validator = _as_marshmallow(validate_instance_type)
date_validator = _as_marshmallow(parse_date)
month_validator = _as_marshmallow(validate_month)
env_name_validator = _as_marshmallow(validate_env_name)
env_version_validator = _as_marshmallow(validate_env_version)


def _project_validator(value):
    if PROJECT_RE.fullmatch(value or "") is None:
        raise ValidationError("Invalid project tag value")


def _cluster_name_validator(value):
    if not is_alphanumeric_with_hyphen(value) or len(value) > 60:
        raise ValidationError("Invalid cluster name")


class StrictInteger(fields.Integer):
    """Integer field that rejects booleans, floats and numeric strings in JSON bodies."""

    def __init__(self, **kwargs):
        super().__init__(strict=True, **kwargs)

    def _validated(self, value):
        if isinstance(value, bool):
            raise self.make_error("invalid")
        return super()._validated(value)


class StrictNumber(fields.Float):
    """JSON number (int or float) field that rejects booleans, strings, NaN and infinity."""

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise self.make_error("invalid")
        return float(value)


def _type_limits_field(**kwargs):
    """``{instance_type: machines}``: how many machines of each instance type the team may run at once."""
    return fields.Dict(
        keys=fields.String(validate=instance_type_validator),
        values=StrictInteger(validate=validate.Range(min=TYPE_LIMIT_MIN, max=TYPE_LIMIT_MAX)),
        validate=validate.Length(max=MAX_TYPE_LIMITS),
        **kwargs,
    )


def _budget_field():
    return StrictNumber(allow_none=True, validate=validate.Range(min=0, max=BUDGET_MAX_USD))


def _env_cap_field():
    return StrictInteger(allow_none=True, validate=validate.Range(min=0, max=ENV_CAP_MAX_GB))


# --- query parameter schemas ------------------------------------------------------------------------------------


class ClusterRegionSchema(Schema):
    region = fields.String(validate=aws_region_validator)


class ClusterTargetSchema(ClusterRegionSchema):
    cluster_name = fields.String(required=True, validate=_cluster_name_validator)


class TeamListSchema(ClusterTargetSchema):
    team = fields.String(validate=any_team_name_validator)


class UsageSchema(TeamListSchema):
    start = fields.String(required=True, validate=date_validator)
    end = fields.String(required=True, validate=date_validator)
    project = fields.String(validate=_project_validator)

    @validates_schema(skip_on_field_errors=True)
    def _check_range(self, data, **kwargs):
        start, end = parse_date(data["start"]), parse_date(data["end"])
        if start >= end:
            raise ValidationError("start must be before end", "start")
        if end - start > timedelta(days=MAX_RANGE_DAYS):
            raise ValidationError(f"range must not exceed {MAX_RANGE_DAYS} days", "end")


class TrendSchema(TeamListSchema):
    months = fields.Integer(validate=validate.Range(min=MONTHS_MIN, max=MONTHS_MAX))
    project = fields.String(validate=_project_validator)


AUDIT_MAX_ROWS = 500


class AuditQuerySchema(Schema):
    month = fields.String(validate=month_validator)
    actor = fields.String(validate=validate.And(validate.Length(min=1, max=128),
                                                 validate.Regexp(r"^[^\x00-\x1f\x7f]+$")))
    team = fields.String(validate=any_team_name_validator)
    action = fields.String(validate=validate.Regexp(AUDIT_ACTION_RE.pattern + "$"))
    start = fields.String(data_key="from", validate=date_validator)
    end = fields.String(data_key="to", validate=date_validator)


# --- body schemas -----------------------------------------------------------------------------------------------


def _check_some_type_allowed(type_limits):
    if not any(value > 0 for value in (type_limits or {}).values()):
        raise ValidationError("Allow at least one instance type (a machine count above 0)", "type_limits")


class CreateTeamSchema(Schema):
    name = fields.String(required=True, validate=team_name_validator)
    fairshare = StrictInteger(load_default=FAIRSHARE_DEFAULT,
                              validate=validate.Range(min=FAIRSHARE_MIN, max=FAIRSHARE_MAX))
    type_limits = _type_limits_field(required=True)
    budget_usd = _budget_field()

    @validates_schema(skip_on_field_errors=True)
    def _check(self, data, **kwargs):
        _check_some_type_allowed(data.get("type_limits"))


class UpdateTeamSchema(Schema):
    fairshare = StrictInteger(validate=validate.Range(min=FAIRSHARE_MIN, max=FAIRSHARE_MAX))
    type_limits = _type_limits_field()
    budget_usd = _budget_field()
    env_storage_cap_gb = _env_cap_field()

    @validates_schema(skip_on_field_errors=True)
    def _at_least_one(self, data, **kwargs):
        if data.get("fairshare") is None and not data.get("type_limits") and not (
                {"budget_usd", "env_storage_cap_gb"} & set(data)):
            raise ValidationError(
                "At least one of fairshare, type_limits, budget_usd or env_storage_cap_gb is required")


email_validator = _as_marshmallow(normalize_email)
lifecycle_team_name_validator = _as_marshmallow(validate_lifecycle_team_name)
member_path_validator = _as_marshmallow(validate_member_path)


class AddMemberSchema(Schema):
    """``{email, link_existing}`` for lifecycle-managed teams, or the legacy ``{user}`` (Slurm only)."""

    user = fields.String(validate=user_name_validator)
    email = fields.String(validate=email_validator)
    link_existing = fields.Boolean(load_default=False)

    @validates_schema(skip_on_field_errors=True)
    def _exactly_one(self, data, **kwargs):
        if bool(data.get("user")) == bool(data.get("email")):
            raise ValidationError("Give exactly one of email or user")
        if data.get("user") and data.get("link_existing"):
            raise ValidationError("link_existing needs an email", "link_existing")


class LifecycleMemberSchema(Schema):
    email = fields.String(required=True, validate=email_validator)
    link_existing = fields.Boolean(load_default=False)


class CreateLifecycleTeamSchema(Schema):
    name = fields.String(required=True, validate=lifecycle_team_name_validator)
    fairshare = StrictInteger(load_default=FAIRSHARE_DEFAULT,
                              validate=validate.Range(min=FAIRSHARE_MIN, max=FAIRSHARE_MAX))
    type_limits = _type_limits_field(required=True)
    budget_usd = _budget_field()
    env_storage_cap_gb = _env_cap_field()
    members = fields.List(fields.Nested(LifecycleMemberSchema(unknown=RAISE)),
                          load_default=list, validate=validate.Length(max=MAX_LIFECYCLE_MEMBERS))
    lead = fields.String(validate=email_validator, allow_none=True)

    @validates_schema(skip_on_field_errors=True)
    def _check(self, data, **kwargs):
        _check_some_type_allowed(data.get("type_limits"))
        users = [normalize_email(m["email"])[1] for m in data.get("members") or []]
        if len(users) != len(set(users)):
            raise ValidationError("Member emails (and their user names) must be unique", "members")


class AdvanceSchema(Schema):
    pass


class OffboardSchema(Schema):
    jobs = fields.String(required=True, validate=validate.OneOf(["cancel", "finish"]))


class ReenableSchema(Schema):
    pass


# --- member portal schemas --------------------------------------------------------------------------------------


MEMBER_HISTORY_MAX_DAYS = 31
MEMBER_READ_LIMIT_MAX = 16384
SCRIPT_MAX_BYTES = 65536
NODES_MAX, GPUS_PER_NODE_MAX, CPUS_MAX, MEM_GB_MAX = 64, 8, 192, 1536
TIME_MAX_MINUTES, BORROW_MAX_MINUTES = 10080, 240
TEMPLATES_MAX = 50


class MemberQuerySchema(ClusterTargetSchema):
    team = fields.String(validate=team_name_validator)


class MemberHistorySchema(MemberQuerySchema):
    start = fields.String(required=True, validate=date_validator)
    end = fields.String(required=True, validate=date_validator)

    @validates_schema(skip_on_field_errors=True)
    def _check_range(self, data, **kwargs):
        start, end = parse_date(data["start"]), parse_date(data["end"])
        if start >= end:
            raise ValidationError("start must be before end", "start")
        if end - start > timedelta(days=MEMBER_HISTORY_MAX_DAYS):
            raise ValidationError(f"range must not exceed {MEMBER_HISTORY_MAX_DAYS} days", "end")


class MemberUsageSchema(MemberQuerySchema):
    start = fields.String(required=True, validate=date_validator)
    end = fields.String(required=True, validate=date_validator)

    @validates_schema(skip_on_field_errors=True)
    def _check_range(self, data, **kwargs):
        start, end = parse_date(data["start"]), parse_date(data["end"])
        if start >= end:
            raise ValidationError("start must be before end", "start")
        if end - start > timedelta(days=MAX_RANGE_DAYS):
            raise ValidationError(f"range must not exceed {MAX_RANGE_DAYS} days", "end")


class MemberFileSchema(MemberQuerySchema):
    path = fields.String(required=True, validate=member_path_validator)
    mode = fields.String(load_default="tail", validate=validate.OneOf(["tail", "offset"]))
    offset = fields.Integer(load_default=0, validate=validate.Range(min=0, max=2 ** 53))
    limit = fields.Integer(load_default=MEMBER_READ_LIMIT_MAX, validate=validate.Range(min=1, max=MEMBER_READ_LIMIT_MAX))


class MemberDirSchema(MemberQuerySchema):
    path = fields.String(required=True, validate=member_path_validator)


def _job_name_validator(value):
    if JOB_NAME_RE.fullmatch(value or "") is None:
        raise ValidationError("Invalid job name: use letters, digits, '.', '_' or '-' (max 64)")


def _script_content_validator(value):
    data = value.encode("utf-8")
    if len(data) > SCRIPT_MAX_BYTES:
        raise ValidationError(f"Script must not exceed {SCRIPT_MAX_BYTES} bytes")
    if b"\x00" in data:
        raise ValidationError("Script must not contain NUL characters")
    if not value.strip():
        raise ValidationError("Script is empty")


class EnvRefSchema(Schema):
    """A runtime environment ``/efs/envs/<team>/<name>/<version>``. Scope ``shared`` (old published copies) is
    still accepted in old templates and resolves to the same directory."""

    scope = fields.String(required=True, validate=validate.OneOf(ENV_SCOPES))
    team = fields.String(required=True, validate=team_name_validator)
    name = fields.String(required=True, validate=env_name_validator)
    version = fields.String(required=True, validate=env_version_validator)


class SubmitJobSchema(Schema):
    team = fields.String(validate=team_name_validator)
    instance_type = fields.String(required=True, validate=instance_type_validator)
    nodes = StrictInteger(load_default=1, validate=validate.Range(min=1, max=NODES_MAX))
    gpus_per_node = StrictInteger(validate=validate.Range(min=0, max=GPUS_PER_NODE_MAX))
    cpus = StrictInteger(validate=validate.Range(min=1, max=CPUS_MAX))
    mem_gb = StrictInteger(validate=validate.Range(min=1, max=MEM_GB_MAX))
    # optional: own-quota jobs run without a time limit unless the member sets one (advanced option); borrowed
    # jobs are capped at BORROW_MAX_MINUTES by sbatch_args (and by job_submit.lua), never rejected
    time_minutes = StrictInteger(validate=validate.Range(min=1, max=TIME_MAX_MINUTES))
    borrow = fields.Boolean(load_default=False)
    job_name = fields.String(validate=_job_name_validator)
    env = fields.Nested(EnvRefSchema(unknown=RAISE), allow_none=True, load_default=None)
    workdir = fields.String(validate=member_path_validator)
    script_path = fields.String(validate=member_path_validator)
    script_content = fields.String(validate=_script_content_validator)

    @validates_schema(skip_on_field_errors=True)
    def _check(self, data, **kwargs):
        if bool(data.get("script_path")) == bool(data.get("script_content")):
            raise ValidationError("Give exactly one of script_path or script_content")


class CancelJobSchema(Schema):
    pass


# --- runtime environments ---------------------------------------------------------------------------------------

ENV_SOURCES = ("image", "dockerfile", "conda")
ENV_TEXT_MAX_BYTES = 65536
ENV_INFO_MAX = 256
IMAGE_REF_MAX = 256
PYTHON_VERSION_RE = re.compile(r"3\.\d{1,2}")


def _env_text_validator(value):
    data = value.encode("utf-8")
    if len(data) > ENV_TEXT_MAX_BYTES:
        raise ValidationError(f"Must not exceed {ENV_TEXT_MAX_BYTES} bytes")
    if b"\x00" in data:
        raise ValidationError("Must not contain NUL characters")
    if not value.strip():
        raise ValidationError("Must not be empty")


def _env_info_validator(value):
    if len(value) > ENV_INFO_MAX or CONTROL_CHARS_RE.search(value):
        raise ValidationError(f"At most {ENV_INFO_MAX} characters on one line")


def _image_validator(value):
    if not 1 <= len(value) <= IMAGE_REF_MAX or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise ValidationError(f"Image references have 1 to {IMAGE_REF_MAX} characters without spaces")


def _python_validator(value):
    if PYTHON_VERSION_RE.fullmatch(value) is None:
        raise ValidationError("Python versions look like 3.11")


ENV_SOURCE_FIELDS = {"image": ("image",), "dockerfile": ("dockerfile",),
                     "conda": ("environment_yml", "requirements_txt", "python")}


class EnvCreateSchema(Schema):
    """A new environment version: ``source`` image (registry import), dockerfile (CodeBuild) or conda
    (``environment_yml``, or ``requirements_txt`` with an optional ``python`` version)."""

    team = fields.String(validate=team_name_validator)
    name = fields.String(required=True, validate=env_name_validator)
    version = fields.String(required=True, validate=env_version_validator)
    source = fields.String(required=True, validate=validate.OneOf(ENV_SOURCES))
    image = fields.String(validate=_image_validator)
    dockerfile = fields.String(validate=_env_text_validator)
    environment_yml = fields.String(validate=_env_text_validator)
    requirements_txt = fields.String(validate=_env_text_validator)
    python = fields.String(validate=_python_validator)
    description = fields.String(validate=_env_info_validator)
    software = fields.String(validate=_env_info_validator)
    # 仅本团队 (team) or 所有人 (all), per environment name; an existing name keeps its visibility
    visibility = fields.String(load_default="team", validate=validate.OneOf(ENV_VISIBILITIES))

    @validates_schema(skip_on_field_errors=True)
    def _check(self, data, **kwargs):
        source = data["source"]
        allowed = set(ENV_SOURCE_FIELDS[source])
        for other in {f for fields_ in ENV_SOURCE_FIELDS.values() for f in fields_} - allowed:
            if other in data:
                raise ValidationError(f"{other} does not apply to source {source}", other)
        if source == "image" and not data.get("image"):
            raise ValidationError("image is required", "image")
        if source == "dockerfile" and not data.get("dockerfile"):
            raise ValidationError("dockerfile is required", "dockerfile")
        if source == "conda":
            if bool(data.get("environment_yml")) == bool(data.get("requirements_txt")):
                raise ValidationError("Give exactly one of environment_yml or requirements_txt")
            if data.get("python") and not data.get("requirements_txt"):
                raise ValidationError("python only applies to requirements_txt", "python")


class EnvActionSchema(Schema):
    pass


class EnvVisibilitySchema(Schema):
    visibility = fields.String(required=True, validate=validate.OneOf(ENV_VISIBILITIES))


def _template_name_validator(value):
    if not isinstance(value, str) or TEMPLATE_NAME_RE.fullmatch(value) is None or not value.strip():
        raise ValidationError("Template names have 1 to 64 characters without control characters")


class TemplateSchema(Schema):
    name = fields.String(required=True, validate=_template_name_validator)
    job = fields.Nested(SubmitJobSchema(unknown=RAISE), required=True)


class TemplatePatchSchema(Schema):
    name = fields.String(validate=_template_name_validator)
    job = fields.Nested(SubmitJobSchema(unknown=RAISE))

    @validates_schema(skip_on_field_errors=True)
    def _at_least_one(self, data, **kwargs):
        if not data:
            raise ValidationError("Give name or job")


class PreferencesSchema(Schema):
    job_notify = fields.Boolean()
    email_jobs = fields.Boolean()
    email_budget = fields.Boolean()
    email_alerts = fields.Boolean()  # GPU idle / long-running / no-capacity alerts by e-mail

    @validates_schema(skip_on_field_errors=True)
    def _at_least_one(self, data, **kwargs):
        if not data:
            raise ValidationError("Give at least one preference")


class NotificationsReadSchema(Schema):
    pass


ClusterRegion = ClusterRegionSchema(unknown=INCLUDE)
ClusterTarget = ClusterTargetSchema(unknown=INCLUDE)
TeamList = TeamListSchema(unknown=INCLUDE)
Usage = UsageSchema(unknown=INCLUDE)
Trend = TrendSchema(unknown=INCLUDE)
AuditQuery = AuditQuerySchema(unknown=INCLUDE)
CreateTeam = CreateTeamSchema(unknown=RAISE)
UpdateTeam = UpdateTeamSchema(unknown=RAISE)
AddMember = AddMemberSchema(unknown=RAISE)
CreateLifecycleTeam = CreateLifecycleTeamSchema(unknown=RAISE)
Advance = AdvanceSchema(unknown=RAISE)
Offboard = OffboardSchema(unknown=RAISE)
Reenable = ReenableSchema(unknown=RAISE)
MemberQuery = MemberQuerySchema(unknown=INCLUDE)
MemberHistory = MemberHistorySchema(unknown=INCLUDE)
MemberUsage = MemberUsageSchema(unknown=INCLUDE)
MemberFile = MemberFileSchema(unknown=INCLUDE)
MemberDir = MemberDirSchema(unknown=INCLUDE)
SubmitJob = SubmitJobSchema(unknown=RAISE)
CancelJob = CancelJobSchema(unknown=RAISE)
EnvCreate = EnvCreateSchema(unknown=RAISE)
EnvAction = EnvActionSchema(unknown=RAISE)
EnvVisibility = EnvVisibilitySchema(unknown=RAISE)
Template = TemplateSchema(unknown=RAISE)
TemplatePatch = TemplatePatchSchema(unknown=RAISE)
Preferences = PreferencesSchema(unknown=RAISE)
NotificationsRead = NotificationsReadSchema(unknown=RAISE)
