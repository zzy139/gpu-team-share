# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Identity of a business user ("member") of the self-service portal.

* PCUI login is the email. The cluster (AD / Linux / Slurm) user is the lowercased local part of the email.
* The user is derived ONLY from the verified ID token: ``email_verified`` must be true, ``token_use`` must be
  ``id`` and its ``sub`` must equal the access token's ``sub``. Request parameters and bodies are never used.
* Member teams come from ``member-<team>`` Cognito groups (not ``team-<team>-member``: every ``team-*`` group
  grants team-lead scope, so a distinct prefix keeps members out of lead routes) and need an ``active`` record.
"""
from flask import request

from api.PclusterApiHandler import AUDIENCE, jwt_decode
from api.pcm_globals import auth_cookies
from api.teams import lifecycle_store
from api.teams.cache import TTLCache
from api.teams.member_exec import MemberCommandError
from api.teams.scope import caller_claims, caller_roles
from api.teams.validation import RESERVED_NAMES, RESERVED_USERS, USER_PREFIX_RE, is_valid_name, normalize_email
from api.utils import disable_auth

MEMBER_GROUP_PREFIX = lifecycle_store.MEMBER_GROUP_PREFIX
RECORD_TTL = 60
DEV_EMAIL = "user@domain.com"

_record_cache = TTLCache(RECORD_TTL)


def _forbidden(message="无权访问"):
    return MemberCommandError(403, message)


def _id_token_claims():
    # After a refresh in authenticate() the new tokens are in auth_cookies; the request cookies are stale.
    id_token = auth_cookies.get("idToken") or request.cookies.get("idToken")
    access_token = auth_cookies.get("accessToken") or request.cookies.get("accessToken")
    if not id_token:
        raise _forbidden("缺少身份令牌，请重新登录")
    try:
        return jwt_decode(id_token, audience=AUDIENCE, access_token=access_token)
    except Exception:
        raise _forbidden("身份令牌无效，请重新登录") from None


def _verified(value):
    return value is True or value == "true"


def member_teams_from_roles():
    teams = set()
    for role in caller_roles():
        if role.startswith(MEMBER_GROUP_PREFIX):
            team = role[len(MEMBER_GROUP_PREFIX):]
            if is_valid_name(team) and team not in RESERVED_NAMES:
                teams.add(team)
    return sorted(teams)


def _active_record(config, team):
    def _load():
        record = lifecycle_store.get_record(config, team)
        return record if record and record["state"] == "active" else None

    return _record_cache.get_or_compute((config["record_prefix"], team), _load)


def member_identity(config):
    """Return ``{email, user, teams, records}`` for the caller or raise a 403 ``MemberCommandError``."""
    if disable_auth():
        email, user = DEV_EMAIL, DEV_EMAIL.split("@", 1)[0]
        return {"email": email, "user": user, "teams": [], "records": {}}
    access = caller_claims()
    claims = _id_token_claims()
    if claims.get("token_use") != "id":
        raise _forbidden("身份令牌无效，请重新登录")
    if not access.get("sub") or claims.get("sub") != access.get("sub"):
        raise _forbidden("身份令牌与会话不匹配，请重新登录")
    if not _verified(claims.get("email_verified")):
        raise _forbidden("邮箱尚未验证")
    try:
        email, user = normalize_email(claims.get("email"), config["email_domains"])
    except ValueError:
        raise _forbidden("该邮箱不能用于集群账号（邮箱域名或用户名格式不符合要求）") from None
    if USER_PREFIX_RE.fullmatch(user) is None or user in RESERVED_USERS:
        raise _forbidden("该邮箱不能用于集群账号")
    records = {}
    for team in member_teams_from_roles():
        record = _active_record(config, team)
        if record is not None and record.get("team_path"):
            records[team] = record
    if not records:
        raise _forbidden("您还不是任何团队的成员")
    return {"email": email, "user": user, "teams": sorted(records), "records": records}


def resolve_member_team(identity, requested=None):
    teams = identity["teams"]
    if requested:
        if requested not in teams:
            raise _forbidden("无权访问该团队")
        return requested
    if len(teams) == 1:
        return teams[0]
    if not teams:
        raise _forbidden("您还不是任何团队的成员")
    raise MemberCommandError(400, "请选择团队")
