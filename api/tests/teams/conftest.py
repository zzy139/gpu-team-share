import json

import pytest
from botocore.exceptions import ClientError

from api.teams import catalog, cluster, datastore, lifecycle_store, member_exec, member_identity, pricing


class FakeParameterStore:
    """In-memory SSM Parameter Store (get_parameter, put_parameter, get_parameters_by_path)."""

    def __init__(self):
        self.params = {}
        self.puts = []

    def get_parameter(self, Name):
        if Name not in self.params:
            raise ClientError({"Error": {"Code": "ParameterNotFound", "Message": "not found"}}, "GetParameter")
        return {"Parameter": {"Name": Name, "Value": self.params[Name]}}

    def put_parameter(self, Name, Value, Type, Tier, Overwrite):
        assert (Type, Tier, Overwrite) == ("String", "Standard", True)
        self.params[Name] = Value
        self.puts.append(Name)
        return {"Version": 1}

    def get_parameters_by_path(self, Path, Recursive=False, NextToken=None):
        items = [{"Name": k, "Value": v} for k, v in sorted(self.params.items()) if k.startswith(Path + "/")]
        return {"Parameters": items}

    def record(self, name):
        return json.loads(self.params[name])


class FakeDynamoDB:
    """In-memory DynamoDB table (put/get/delete/query/batch_get with the low-level client API)."""

    def __init__(self, table="hpc-poc-pcui-data"):
        self.table = table
        self.items = {}
        self.calls = []
        self.fail = None

    def _check(self, name, kwargs):
        self.calls.append(name)
        if self.fail:
            raise self.fail
        table = kwargs.get("TableName")
        if table is not None:
            assert table == self.table, table

    @staticmethod
    def _key(key):
        return key["pk"]["S"], key["sk"]["S"]

    def put_item(self, TableName, Item, ConditionExpression=None):
        self._check("put_item", {"TableName": TableName})
        key = (Item["pk"]["S"], Item["sk"]["S"])
        if ConditionExpression == "attribute_not_exists(pk)" and key in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem")
        self.items[key] = Item
        return {}

    def get_item(self, TableName, Key, ConsistentRead=False):
        self._check("get_item", {"TableName": TableName})
        item = self.items.get(self._key(Key))
        return {"Item": item} if item else {}

    def delete_item(self, TableName, Key):
        self._check("delete_item", {"TableName": TableName})
        self.items.pop(self._key(Key), None)
        return {}

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues, ScanIndexForward=True, Limit=None,
              ExclusiveStartKey=None):
        self._check("query", {"TableName": TableName})
        pk = ExpressionAttributeValues[":pk"]["S"]
        keys = sorted(k for k in self.items if k[0] == pk)
        if ":prefix" in ExpressionAttributeValues:
            prefix = ExpressionAttributeValues[":prefix"]["S"]
            keys = [k for k in keys if k[1].startswith(prefix)]
        if ":low" in ExpressionAttributeValues:
            low, high = ExpressionAttributeValues[":low"]["S"], ExpressionAttributeValues[":high"]["S"]
            keys = [k for k in keys if low <= k[1] <= high]
        if not ScanIndexForward:
            keys.reverse()
        if Limit:
            keys = keys[:Limit]
        return {"Items": [self.items[k] for k in keys]}

    def batch_get_item(self, RequestItems):
        self._check("batch_get_item", {})
        (table, request), = RequestItems.items()
        assert table == self.table
        found = [self.items[self._key(k)] for k in request["Keys"] if self._key(k) in self.items]
        return {"Responses": {table: found}}

    def item(self, pk, sk):
        item = self.items.get((pk, sk))
        return datastore.deserialize(item) if item else None

    def with_prefix(self, pk, prefix=""):
        return [datastore.deserialize(v) for k, v in sorted(self.items.items()) if k[0] == pk
                and k[1].startswith(prefix)]


CONFIG = {
    "version": 2,
    "region": "us-west-2",
    "account_id": "123456789012",
    "project": "hpc-poc",
    "clusters": ["hpc-poc"],
    "directory_id": "d-0123456789",
    "ad_domain": "corp.example.internal",
    "ad_domain_controllers": ["10.0.0.10", "10.0.0.11"],
    "ad_admin_user": "Administrator",
    "ad_admin_secret_arn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:hpc-poc/ad-admin-password-AbCdEf",
    "efs_id": "fs-0123456789abcdef0",
    "storage_mount": "/efs",
    "efs_dns": "fs-0123456789abcdef0.efs.us-west-2.amazonaws.com",
    "ecr_repo_prefix": "hpc-poc",
    "secret_prefix": "hpc-poc/user-",
    "record_prefix": "/pcui-teams",
    "tags": {"Project": "hpc-poc", "CostCenter": "hpc-poc"},
    "email_domains": ["example.com"],
    "member_exec_role_arn": "arn:aws:iam::123456789012:role/pcui-member-exec",
    "data_table": "hpc-poc-pcui-data",
    "portal_topic_arn": "arn:aws:sns:us-west-2:123456789012:hpc-poc-portal-notify",
    "admin_topic_arn": "arn:aws:sns:us-west-2:123456789012:hpc-poc-admin-alerts",
    "env_build_project": "hpc-poc-env-build",
    "codebuild_log_group": "/aws/codebuild/hpc-poc-env-build",
}

# DescribeInstanceTypes figures of the instance types used by the fixtures
# DescribeInstanceTypes reports 22888 MiB (L4) and 45776 MiB (L40S) per GPU
L4 = {"gpu_model": "NVIDIA L4", "gpu_memory_gib": 22.4, "gpu_memory_gb": 24}
L40S = {"gpu_model": "NVIDIA L40S", "gpu_memory_gib": 44.7, "gpu_memory_gb": 48}
INSTANCE_INFO = {
    "g6.xlarge": {"gpus": 1, "vcpus": 4, "memory_gib": 16.0, **L4},
    "g6.12xlarge": {"gpus": 4, "vcpus": 48, "memory_gib": 192.0, **L4},
    "g6e.xlarge": {"gpus": 1, "vcpus": 4, "memory_gib": 32.0, **L40S},
    "g6e.2xlarge": {"gpus": 1, "vcpus": 8, "memory_gib": 64.0, **L40S},
    "g6e.12xlarge": {"gpus": 4, "vcpus": 48, "memory_gib": 384.0, **L40S},
    "m7i.xlarge": {"gpus": 0, "vcpus": 4, "memory_gib": 16.0, "gpu_model": None, "gpu_memory_gib": None,
                   "gpu_memory_gb": None},
}
# On-demand USD per instance-hour
PRICES = {"g6.xlarge": 0.8048, "g6.12xlarge": 4.6016, "g6e.xlarge": 1.861, "g6e.2xlarge": 2.24208,
          "g6e.12xlarge": 10.49264, "m7i.xlarge": 0.2016}


@pytest.fixture(autouse=True)
def parameter_store(mocker):
    """No test ever reaches the real Parameter Store; the lifecycle is unconfigured unless a test configures it."""
    store = FakeParameterStore()
    lifecycle_store._config_cache.clear()
    member_identity._record_cache.clear()
    member_exec._session_cache.clear()
    member_exec._login_node_cache.clear()
    cluster._config_cache.clear()
    mocker.patch("api.teams.lifecycle_store.ssm_client", return_value=store)
    mocker.patch("api.teams.lifecycle.clients", side_effect=AssertionError("lifecycle AWS clients not mocked"))
    mocker.patch("api.teams.member_exec._sts_client", side_effect=AssertionError("STS not mocked"))
    mocker.patch("api.teams.notify.sns_client", side_effect=AssertionError("SNS not mocked"))
    mocker.patch("api.teams.envs.codebuild_client", side_effect=AssertionError("CodeBuild not mocked"))
    return store


@pytest.fixture(autouse=True)
def dynamo(mocker):
    """Every test gets an empty in-memory portal data table."""
    table = FakeDynamoDB()
    mocker.patch("api.teams.datastore.dynamodb_client", return_value=table)
    return table


@pytest.fixture(autouse=True)
def instance_info(mocker):
    """DescribeInstanceTypes and the Pricing API are never called for real."""
    def _info(instance_types, region):
        return {t: dict(INSTANCE_INFO[t]) for t in instance_types if t in INSTANCE_INFO}

    def _prices(instance_types, region):
        return {t: PRICES[t] for t in instance_types if t in PRICES}

    mocker.patch.object(pricing, "instance_info", side_effect=_info)
    mocker.patch.object(pricing, "instance_prices", side_effect=_prices)
    return catalog


@pytest.fixture
def configured(parameter_store):
    parameter_store.params[lifecycle_store.CONFIG_PARAMETER] = json.dumps(CONFIG)
    return CONFIG


def seed_record(store, team, **overrides):
    record = {
        "version": 2, "team": team, "state": "active",
        "ad_group": team, "team_path": f"/efs/{team}", "ecr_repo": f"hpc-poc-{team}-envs", "ecr_ready": True,
        "env_storage_cap_gb": 0,
        "cognito_group": f"team-{team}", "member_group": f"member-{team}", "qos": f"{team}-std",
        "ad_users_created": [], "ad_secrets": [], "password_set": [],
        "cognito_users_created": [], "pcui_secrets": [], "spec": None,
        "created_at": "2026-11-01T00:00:00+00:00", "updated_at": "2026-11-01T00:00:00+00:00", "deleted_at": None,
    }
    record.update(overrides)
    store.params[f"/pcui-teams/teams/{team}"] = json.dumps(record)
    return record


def walk_keys(value):
    """Every dict key in a JSON value (for the 'no billing anywhere' checks)."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item)
