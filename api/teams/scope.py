# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Server-side team scoping based on the caller's Cognito groups.

Members of the ``admin`` group see every team. Any other caller sees only the
teams named by their ``team-<team>`` groups. ``authenticate()`` stores the
decoded access token in ``flask.g.pcui_user_claims``; nothing here trusts a
value supplied by the client.
"""
from flask import abort, g

from api.PclusterApiHandler import USER_ROLES_CLAIM
from api.teams.validation import RESERVED_NAMES, is_valid_name
from api.utils import disable_auth

TEAM_GROUP_PREFIX = "team-"
ADMIN_GROUP = "admin"


def caller_claims() -> dict:
    claims = getattr(g, "pcui_user_claims", None)
    return claims if isinstance(claims, dict) else {}


def caller_roles() -> list:
    if disable_auth():
        # Mirrors the identity returned by get_identity() when auth is disabled (local dev / tests).
        return [ADMIN_GROUP]
    roles = caller_claims().get(USER_ROLES_CLAIM, [])
    if not isinstance(roles, (list, tuple)):
        return []
    return [role for role in roles if isinstance(role, str)]


def is_admin() -> bool:
    return ADMIN_GROUP in caller_roles()


def caller_teams() -> list:
    teams = set()
    for role in caller_roles():
        if role.startswith(TEAM_GROUP_PREFIX):
            team = role[len(TEAM_GROUP_PREFIX):]
            if is_valid_name(team) and team not in RESERVED_NAMES:
                teams.add(team)
    return sorted(teams)


def resolve_team_scope(requested_team=None):
    """Return the list of teams the caller may see, or None for "all teams" (admin without a filter).

    Aborts with 403 when a non-admin caller has no team group or asks for a team outside their groups.
    """
    if is_admin():
        return [requested_team] if requested_team else None
    teams = caller_teams()
    if not teams:
        abort(403)
    if requested_team:
        if requested_team not in teams:
            abort(403)
        return [requested_team]
    return teams


def in_scope(team, scope) -> bool:
    return scope is None or team in scope


def filter_teams(rows, scope, key="name"):
    return [row for row in rows if in_scope(row.get(key), scope)]
