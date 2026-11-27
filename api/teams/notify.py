# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Notifications: preferences, the bell list, budget banner state and the e-mail subscription.

* Each member has at most one e-mail subscription to the ``portal-notify`` topic, created by PCUI with the
  caller's verified token e-mail and the filter policy ``{"user": [<user>]}``. The scheduler publishes job-end
  and budget messages per user (message attribute ``user``) only for users whose preferences ask for them, so
  changing a preference never needs a new subscription (and no new confirmation e-mail).
* SNS sends the confirmation e-mail; the status shown is ``pending`` until the user confirms it.
"""
import json

import boto3
from botocore.exceptions import ClientError

from api.teams import datastore

BELL_LIMIT = 50
# email_alerts (GPU idle / long-running / no-capacity alerts) is on by default; it is sent only once the user has
# an e-mail subscription (created when any e-mail preference is saved on, confirmed by the user)
DEFAULT_PREFS = {"job_notify": False, "email_jobs": False, "email_budget": False, "email_alerts": True}
EMAIL_PREFS = ("email_jobs", "email_budget", "email_alerts")
ALERT_KINDS = ("gpu_idle", "long_run", "no_capacity")
WARNING_PERCENT, EXCEEDED_PERCENT = 80, 100
BUDGET_THRESHOLDS = (WARNING_PERCENT, EXCEEDED_PERCENT)


def sns_client(region):
    return boto3.client("sns", region_name=region)


def filter_policy(user):
    return json.dumps({"user": [user]}, separators=(",", ":"))


def get_prefs(store, user):
    item = store.get(datastore.user_pk(user), datastore.PREFS_SK) or {}
    prefs = {**DEFAULT_PREFS, **{k: bool(item[k]) for k in DEFAULT_PREFS if k in item}}
    prefs["subscription_arn"] = item.get("subscription_arn")
    prefs["notif_seen"] = item.get("notif_seen")
    return prefs


def _save_prefs(store, user, prefs):
    store.put({"pk": datastore.user_pk(user), "sk": datastore.PREFS_SK, **prefs})


def subscription_status(config, arn):
    """``none``, ``pending`` or ``confirmed``."""
    if not arn:
        return "none"
    try:
        attrs = sns_client(config["region"]).get_subscription_attributes(SubscriptionArn=arn)["Attributes"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "NotFound":
            return "none"
        raise
    return "pending" if attrs.get("PendingConfirmation") == "true" else "confirmed"


def public_prefs(config, prefs, email):
    return {
        "job_notify": prefs["job_notify"],
        "email_jobs": prefs["email_jobs"],
        "email_budget": prefs["email_budget"],
        "email_alerts": prefs["email_alerts"],
        "email": {"address": email, "status": subscription_status(config, prefs.get("subscription_arn"))},
    }


def update_preferences(config, store, user, email, changes):
    """Apply ``changes`` and create/remove the e-mail subscription. Returns the stored preferences."""
    prefs = get_prefs(store, user)
    prefs.update({k: bool(v) for k, v in changes.items() if k in DEFAULT_PREFS})
    wants_email = any(prefs[k] for k in EMAIL_PREFS)
    sns = sns_client(config["region"])
    if wants_email and not prefs.get("subscription_arn"):
        resp = sns.subscribe(TopicArn=config["portal_topic_arn"], Protocol="email", Endpoint=email,
                             Attributes={"FilterPolicy": filter_policy(user)}, ReturnSubscriptionArn=True)
        prefs["subscription_arn"] = resp.get("SubscriptionArn")
    elif not wants_email and prefs.get("subscription_arn"):
        try:
            sns.unsubscribe(SubscriptionArn=prefs["subscription_arn"])
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") not in ("NotFound", "InvalidParameter"):
                raise
        prefs["subscription_arn"] = None
    _save_prefs(store, user, prefs)
    return prefs


def list_notifications(store, user, limit=BELL_LIMIT):
    prefs = get_prefs(store, user)
    items = store.query(datastore.user_pk(user), prefix="NOTIF#", newest_first=True, limit=limit)
    seen = prefs.get("notif_seen") or ""
    result = []
    for item in items:
        result.append({
            "id": item["sk"][len("NOTIF#"):],
            "kind": item.get("kind", "job"),
            "job_id": item.get("job_id"),
            "name": item.get("name"),
            "state": item.get("state"),
            "exit_code": item.get("exit_code"),
            "partition": item.get("partition"),
            "instance_type": item.get("instance_type"),
            "env": item.get("env"),
            "end": item.get("end"),
            "team": item.get("team"),
            "hours": item.get("hours"),
            "minutes": item.get("minutes"),
            "alternatives": item.get("alternatives"),
            "text": item.get("text"),
            "unread": item["sk"] > f"NOTIF#{seen}" if seen else True,
        })
    return {"items": result, "unread": sum(1 for i in result if i["unread"])}


def mark_read(store, user, until):
    prefs = get_prefs(store, user)
    prefs["notif_seen"] = until
    _save_prefs(store, user, prefs)


def budget_level(percent):
    if percent is None:
        return None
    if percent >= EXCEEDED_PERCENT:
        return "exceeded"
    if percent >= WARNING_PERCENT:
        return "warning"
    return None


def budget_banner(store, team, month):
    """Month-to-date budget state of a team (written by the scheduler), or None without a budget."""
    budget = store.get(datastore.team_pk(team), datastore.BUDGET_SK)
    if not budget or not budget.get("budget_usd"):
        return None
    state = store.get(datastore.team_pk(team), datastore.budget_state_sk(month)) or {}
    percent = state.get("percent")
    return {
        "team": team,
        "month": month,
        "budget_usd": budget["budget_usd"],
        "spent_usd": state.get("spent_usd"),
        "percent": percent,
        "level": budget_level(percent),
        "updated_at": state.get("updated_at"),
    }


def publish(config, topic_arn, subject, message, attributes):
    """Publish one message (scheduler only; the PCUI Lambda has no sns:Publish)."""
    sns_client(config["region"]).publish(
        TopicArn=topic_arn, Subject=subject[:100], Message=message,
        MessageAttributes={k: {"DataType": "String", "StringValue": v} for k, v in attributes.items()},
    )
