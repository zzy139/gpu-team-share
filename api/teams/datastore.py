# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Portal data store: one DynamoDB table (``<project>-pcui-data``, keys ``pk``/``sk``, TTL attribute ``ttl``).

Items (pk | sk):

* ``TEAM#<t> | BUDGET``                   monthly budget of a team (USD)
* ``TEAM#<t> | BUDGETSTATE#<yyyy-mm>``    month-to-date estimate and percent (banner), written by the scheduler
* ``TEAM#<t> | BUDGETALERT#<yyyy-mm>#<p>`` marker: threshold p (80/100) already notified this month
* ``TEAM#<t> | ENVDEL#<name>#<version>``  tombstone of a deleted environment version {by, at} (ttl 400 days)
* ``USER#<u> | PREFS``                    notification preferences and the e-mail subscription
* ``USER#<u> | NOTIF#<iso>#<jobid>``      bell notification (ttl 30 days; alerts: ``NOTIF#<iso>#<jobid>-<kind>``)
* ``USER#<u> | ALERT#<jobid>``            job alert state (idle episode, long-run block, no-capacity) (ttl 30 days)
* ``USER#<u> | TPL#<id>``                 job template
* ``USER#<u> | OFFBOARD#<team>``          offboarding state; ``SYS | OFFBOARD#<team>#<u>`` indexes it
* ``AUDIT#<yyyy-mm> | <iso>#<uuid>``      audit entry (ttl 400 days)
* ``SYS | JOBWATERMARK``                  end time up to which job ends were processed

Only item-level calls are used (no Scan). Values are converted to and from DynamoDB types here.
"""
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

NOTIF_TTL_DAYS = 30
AUDIT_TTL_DAYS = 400
ENVDEL_TTL_DAYS = 400
MAX_QUERY_PAGES = 20
_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


class DataStoreUnavailable(Exception):
    """The portal data table is not configured."""


def dynamodb_client(region):
    return boto3.client("dynamodb", region_name=region)


def ttl_after(days, now=None):
    return int((now or time.time()) + days * 86400)


# --- keys -------------------------------------------------------------------------------------------------------


def team_pk(team):
    return f"TEAM#{team}"


def user_pk(user):
    return f"USER#{user}"


def audit_pk(month):
    return f"AUDIT#{month}"


SYS_PK = "SYS"
BUDGET_SK = "BUDGET"
PREFS_SK = "PREFS"
WATERMARK_SK = "JOBWATERMARK"


def budget_state_sk(month):
    return f"BUDGETSTATE#{month}"


def budget_alert_sk(month, threshold):
    return f"BUDGETALERT#{month}#{threshold}"


def env_deleted_sk(name, version):
    return f"ENVDEL#{name}#{version}"


def offboard_sk(team):
    return f"OFFBOARD#{team}"


def offboard_index_sk(team, user):
    return f"OFFBOARD#{team}#{user}"


# --- conversion -------------------------------------------------------------------------------------------------


def _to_dynamo(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, set, tuple)):
        return [_from_dynamo(v) for v in value]
    return value


def serialize(item):
    return {k: _serializer.serialize(_to_dynamo(v)) for k, v in item.items() if v is not None}


def deserialize(item):
    return {k: _from_dynamo(_deserializer.deserialize(v)) for k, v in (item or {}).items()}


def _key(pk, sk):
    return {"pk": {"S": pk}, "sk": {"S": sk}}


# --- operations -------------------------------------------------------------------------------------------------


class Store:
    """Thin wrapper around the table of one lifecycle configuration."""

    def __init__(self, config):
        if not config or not config.get("data_table"):
            raise DataStoreUnavailable("门户数据表尚未配置")
        self.table = config["data_table"]
        self.client = dynamodb_client(config["region"])

    def put(self, item, if_absent=False):
        """Write ``item`` (must contain pk and sk). With ``if_absent``, returns False when it already exists."""
        kwargs = {"TableName": self.table, "Item": serialize(item)}
        if if_absent:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
        try:
            self.client.put_item(**kwargs)
        except ClientError as err:
            if if_absent and err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def get(self, pk, sk):
        resp = self.client.get_item(TableName=self.table, Key=_key(pk, sk), ConsistentRead=True)
        return deserialize(resp["Item"]) if resp.get("Item") else None

    def delete(self, pk, sk):
        self.client.delete_item(TableName=self.table, Key=_key(pk, sk))

    def query(self, pk, prefix=None, between=None, newest_first=False, limit=None):
        """Items of ``pk`` whose sk starts with ``prefix`` or lies in ``between`` = (low, high)."""
        values = {":pk": {"S": pk}}
        condition = "pk = :pk"
        if prefix is not None:
            condition += " AND begins_with(sk, :prefix)"
            values[":prefix"] = {"S": prefix}
        elif between is not None:
            condition += " AND sk BETWEEN :low AND :high"
            values[":low"], values[":high"] = {"S": between[0]}, {"S": between[1]}
        kwargs = {"TableName": self.table, "KeyConditionExpression": condition,
                  "ExpressionAttributeValues": values, "ScanIndexForward": not newest_first}
        items = []
        for _ in range(MAX_QUERY_PAGES):
            if limit is not None:
                kwargs["Limit"] = max(limit - len(items), 1)
            resp = self.client.query(**kwargs)
            items.extend(deserialize(i) for i in resp.get("Items", []))
            if not resp.get("LastEvaluatedKey") or (limit is not None and len(items) >= limit):
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items[:limit] if limit is not None else items

    def batch_get(self, keys):
        """Items for [(pk, sk)] (PCUI only; the scheduler role has no BatchGetItem). Missing items are skipped."""
        result = []
        keys = list(dict.fromkeys(keys))
        for index in range(0, len(keys), 100):
            request = {self.table: {"Keys": [_key(pk, sk) for pk, sk in keys[index:index + 100]]}}
            for _ in range(5):
                resp = self.client.batch_get_item(RequestItems=request)
                result.extend(deserialize(i) for i in resp.get("Responses", {}).get(self.table, []))
                request = resp.get("UnprocessedKeys") or {}
                if not request:
                    break
        return result


def store(config):
    return Store(config)
