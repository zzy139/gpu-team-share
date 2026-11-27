# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Audit log for team quota, member, environment and portal changes.

Entries go through the application logger, which writes to the Lambda's CloudWatch log group (filter pattern
``team-quota-audit``), and, when the portal data table is configured, to DynamoDB items
``AUDIT#<yyyy-mm> | <iso>#<uuid>`` (ttl 400 days) for the admin Audit log page. The DynamoDB write is best
effort and never fails the request.
"""
import json
import uuid
from datetime import datetime, timezone

from flask import request

from api.pcm_globals import logger
from api.teams.scope import caller_claims

AUDIT_MESSAGE = "team-quota-audit"
PARAMS_MAX_CHARS = 4000


def _actor_email(claims):
    if claims.get("email"):
        return claims.get("email")
    # Cognito access tokens carry no email claim; the id token does.
    try:
        from api.PclusterApiHandler import AUDIENCE, jwt_decode

        id_token = request.cookies.get("idToken")
        if not id_token:
            return None
        decoded = jwt_decode(id_token, audience=AUDIENCE, access_token=request.cookies.get("accessToken"))
        return decoded.get("email")
    except Exception:  # the audit entry must never fail the request
        return None


def _store_entry(entry, now):
    try:
        from api.teams import datastore, lifecycle_store

        config = lifecycle_store.load_config()
        if not config:
            return
        params = json.dumps(entry.get("params"), default=str, sort_keys=True)
        if len(params) > PARAMS_MAX_CHARS:
            params = params[:PARAMS_MAX_CHARS] + "...(truncated)"
        datastore.store(config).put({
            "pk": datastore.audit_pk(now.strftime("%Y-%m")),
            # microseconds keep entries of the same second in order (newest first on the Audit log page)
            "sk": f"{now.isoformat(timespec='microseconds')}#{uuid.uuid4().hex}",
            "actor": entry.get("actor"),
            "actor_email": entry.get("actor_email"),
            "action": entry.get("action"),
            "cluster": entry.get("cluster"),
            "team": entry.get("team"),
            "params": params,
            "result": entry.get("result"),
            "error": (entry.get("error") or None) and str(entry["error"])[:1000],
            "time": entry["time"],
            "ttl": datastore.ttl_after(datastore.AUDIT_TTL_DAYS, now.timestamp()),
        })
    except Exception as err:  # best effort: the log entry above is the record of last resort
        try:
            logger.warning(f"Audit entry not stored in the data table: {type(err).__name__}")
        except Exception:
            pass


def audit(action, cluster, team, params, result, error=None):
    claims = caller_claims()
    now = datetime.now(timezone.utc)
    entry = {
        "actor": claims.get("username"),
        "actor_email": _actor_email(claims),
        "action": action,
        "cluster": cluster,
        "team": team,
        "params": params,
        "result": result,
        "error": error,
        "time": now.isoformat(timespec="seconds"),
    }
    logger.info(AUDIT_MESSAGE, extra=entry)
    _store_entry(entry, now)
    return entry
