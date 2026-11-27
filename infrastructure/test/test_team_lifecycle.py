"""Least-privilege tests for team-lifecycle/pcui-team-lifecycle.yaml (side stack of the custom PCUI)."""
import json

import pytest
from cloud_radar.cf.unit import Template

from constants import INFRASTRUCTURE_DIR
from helpers import policy_statements

TEMPLATE = INFRASTRUCTURE_DIR / "team-lifecycle" / "pcui-team-lifecycle.yaml"
PARAMS = {
    "PcuiRoleName": "ParallelClusterUIUserRole-ABC",
    "UserPoolId": "us-west-2_AbCdEf123",
    "ClusterNames": "hpc-poc,hpc-dev",
    "DirectoryId": "d-0123456789",
    "AdDomainName": "corp.example.internal",
    "AdDomainControllers": "10.0.0.10,10.0.0.11",
    "AdAdminSecretArn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:hpc-poc/ad-admin-password-AbCdEf",
    "FsxFileSystemId": "fs-0123456789abcdef0",
    "EmailDomains": "example.com",
}
ACCOUNT = "555555555555"  # cloud-radar default account id
# Actions that have no resource-level permissions.
STAR_ALLOWED = {"fsx:DescribeDataRepositoryAssociations", "ec2:DescribeInstances", "ssm:GetCommandInvocation"}


@pytest.fixture(scope="module")
def stack():
    template = Template.from_yaml(TEMPLATE.resolve(), {})
    return template.create_stack(PARAMS, region="us-west-2")


def _statements(stack, name):
    resource = stack.get_resource(name)
    return policy_statements(resource.get_property_value("PolicyDocument"))


def _member_statements(stack):
    policies = stack.get_resource("PcuiMemberExecRole").get_property_value("Policies")
    return [s for p in policies for s in policy_statements(p["PolicyDocument"])]


def _actions(statement):
    actions = statement["Action"]
    return actions if isinstance(actions, list) else [actions]


def _resources(statement):
    resources = statement["Resource"]
    return resources if isinstance(resources, list) else [resources]


def _by_sid(statements, sid):
    return next(s for s in statements if s.get("Sid") == sid)


def test_template_is_small_enough_for_template_body():
    assert TEMPLATE.stat().st_size < 51200


def test_lambda_ssm_is_denied_outside_listed_cluster_head_nodes(stack):
    statements = _statements(stack, "PcuiSsmScopePolicy")
    assert stack.get_resource("PcuiSsmScopePolicy").get_property_value("Roles") == ["ParallelClusterUIUserRole-ABC"]
    assert all(s["Effect"] == "Deny" and s["Action"] == "ssm:SendCommand" for s in statements)
    cluster = _by_sid(statements, "DenySendCommandOutsideListedClusters")
    assert cluster["Condition"] == {"StringNotEquals": {"ssm:resourceTag/parallelcluster:cluster-name": ["hpc-poc", "hpc-dev"]}}
    head = _by_sid(statements, "DenySendCommandToNonHeadNodes")
    assert head["Condition"] == {"StringNotEquals": {"ssm:resourceTag/parallelcluster:node-type": "HeadNode"}}
    assert cluster["Resource"] == head["Resource"] == f"arn:aws:ec2:*:{ACCOUNT}:instance/*"
    assert _by_sid(statements, "DenySendCommandToManagedInstances")["Resource"] == f"arn:aws:ssm:*:{ACCOUNT}:managed-instance/*"


def test_member_role_reaches_login_nodes_only(stack):
    role = stack.get_resource("PcuiMemberExecRole")
    trust = policy_statements(role.get_property_value("AssumeRolePolicyDocument"))
    assert trust == [{"Effect": "Allow", "Action": "sts:AssumeRole",
                      "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:role/ParallelClusterUIUserRole-ABC"}}]
    assert role.get_property_value("MaxSessionDuration") == 3600
    statements = _member_statements(stack)
    allow = _by_sid(statements, "SendCommandToLoginNodes")
    assert allow["Effect"] == "Allow"
    assert allow["Condition"] == {"StringEquals": {
        "ssm:resourceTag/parallelcluster:node-type": "LoginNode",
        "ssm:resourceTag/parallelcluster:cluster-name": ["hpc-poc", "hpc-dev"],
    }}
    deny = _by_sid(statements, "DenySendCommandToNonLoginNodes")
    assert deny["Effect"] == "Deny"
    assert deny["Condition"] == {"StringNotEquals": {"ssm:resourceTag/parallelcluster:node-type": "LoginNode"}}
    document = _by_sid(statements, "RunShellScriptDocument")
    assert document["Resource"] == "arn:aws:ssm:us-west-2::document/AWS-RunShellScript"
    sendable = [s for s in statements if s["Effect"] == "Allow" and "ssm:SendCommand" in _actions(s)]
    for statement in sendable:
        assert statement is document or "Condition" in statement


def test_no_wildcard_resources_except_unscopable_actions(stack):
    statements = _statements(stack, "PcuiTeamLifecyclePolicy") + _member_statements(stack) + _statements(stack, "HeadNodeAdAdminPolicy")
    for statement in statements:
        if statement["Effect"] != "Allow":
            continue
        if "*" in _resources(statement):
            assert set(_actions(statement)) <= STAR_ALLOWED, statement
        for action in _actions(statement):
            assert action != "*" and not action.endswith(":*"), statement


def test_lifecycle_policy_scopes(stack):
    statements = _statements(stack, "PcuiTeamLifecyclePolicy")
    assert stack.get_resource("PcuiTeamLifecyclePolicy").get_property_value("Roles") == ["ParallelClusterUIUserRole-ABC"]
    actions = {a for s in statements for a in _actions(s)}
    assert "secretsmanager:GetSecretValue" not in actions
    create = _by_sid(statements, "CreateUserPasswordSecrets")
    assert create["Resource"] == f"arn:aws:secretsmanager:us-west-2:{ACCOUNT}:secret:hpc-poc/user-*"
    assert create["Condition"] == {"StringEquals": {"aws:RequestTag/Project": "hpc-poc"}}
    assert _by_sid(statements, "UpdateUserPasswordSecrets")["Resource"] == create["Resource"]
    assert _by_sid(statements, "TeamBuckets")["Resource"] == [
        f"arn:aws:s3:::hpc-poc-*-{ACCOUNT}-us-west-2", f"arn:aws:s3:::hpc-poc-*-{ACCOUNT}-us-west-2/*"]
    assert _by_sid(statements, "ResetInitialAdPassword")["Resource"] == f"arn:aws:ds:us-west-2:{ACCOUNT}:directory/d-0123456789"
    assert _by_sid(statements, "CognitoTeamGroupsAndUsers")["Resource"] == f"arn:aws:cognito-idp:us-west-2:{ACCOUNT}:userpool/us-west-2_AbCdEf123"
    fsx = _by_sid(statements, "CreateTeamDataRepositoryAssociation")["Resource"]
    assert all("fs-0123456789abcdef0" in arn for arn in fsx)
    assert _by_sid(statements, "FsxS3ServiceLinkedRole")["Condition"] == {
        "StringEquals": {"iam:AWSServiceName": "s3.data-source.lustre.fsx.amazonaws.com"}}
    records = _by_sid(statements, "TeamRecords")
    assert "ssm:DeleteParameter" not in _actions(records)
    assert all(r.startswith(f"arn:aws:ssm:us-west-2:{ACCOUNT}:parameter/pcui-teams/teams") for r in records["Resource"])


def test_head_node_policy_reads_only_the_ad_admin_secret(stack):
    resource = stack.get_resource("HeadNodeAdAdminPolicy")
    assert "Roles" not in resource.get("Properties", {})
    statements = _statements(stack, "HeadNodeAdAdminPolicy")
    assert statements == [{"Sid": "ReadAdAdminSecret", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
                           "Resource": PARAMS["AdAdminSecretArn"]}]


def test_config_parameter_has_no_secrets_and_the_member_role(stack):
    resource = stack.get_resource("TeamLifecycleConfig")
    assert resource.get_property_value("Name") == "/pcui-teams/config"
    config = json.loads(resource.get_property_value("Value"))
    assert config["clusters"] == ["hpc-poc", "hpc-dev"]
    assert config["ad_domain_controllers"] == ["10.0.0.10", "10.0.0.11"]
    assert config["email_domains"] == ["example.com"]
    assert config["member_exec_role_arn"]
    assert config["secret_prefix"] == "hpc-poc/user-"
    assert config["ad_admin_secret_arn"] == PARAMS["AdAdminSecretArn"]
    assert set(config) >= {"version", "region", "account_id", "project", "directory_id", "ad_domain", "ad_admin_user",
                           "fsx_id", "fsx_mount", "bucket_prefix", "record_prefix", "tags"}
    assert "password" not in json.dumps(config).replace("ad-admin-password", "")
