"""Regression guard: upstream job, log and shell routes stay admin-only for team leads and members."""
import pytest

UPSTREAM = [
    ("get", "/manager/queue_status?cluster_name=hpc-poc&region=us-west-2"),
    ("post", "/manager/sacct?cluster_name=hpc-poc&region=us-west-2"),
    ("get", "/manager/scontrol_job?cluster_name=hpc-poc&region=us-west-2&job_id=1"),
    ("get", "/manager/cancel_job?cluster_name=hpc-poc&region=us-west-2&job_id=1"),
    ("post", "/logs"),
    ("get", "/manager/get_dcv_session?cluster_name=hpc-poc&region=us-west-2&instance_id=i-0123456789abcdef0&user=ec2-user"),
    ("post", "/manager/ec2_action?region=us-west-2&instance_id=i-0123456789abcdef0&action=stop_instances"),
    ("get", "/api?path=/v3/clusters"),
    ("get", "/manager/list_users"),
]


def _call(client, method, url):
    return getattr(client, method)(url, json={"logs": []}) if method == "post" else client.get(url)


@pytest.fixture
def login(mocker, client, mock_csrf_needed):
    def _login(groups):
        mocker.patch("api.PclusterApiHandler.jwt_decode",
                     return_value={"cognito:groups": groups, "username": "u", "sub": "s"})
        client.set_cookie("accessToken", "access-token")
        return client

    return _login


@pytest.mark.parametrize("method,url", UPSTREAM)
def test_anonymous_is_401(client, method, url):
    assert _call(client, method, url).status_code == 401


@pytest.mark.parametrize("groups", [["team-teama"], ["member-teama"], ["team-teama", "member-teama"]])
@pytest.mark.parametrize("method,url", UPSTREAM)
def test_leads_and_members_are_403(login, groups, method, url):
    assert _call(login(groups), method, url).status_code == 403


@pytest.mark.parametrize("groups", [["team-teama"], ["member-teama"]])
def test_leads_and_members_can_load_their_identity(login, mocker, groups):
    mocker.patch("app.get_identity", return_value={"user_roles": groups, "username": "u"})
    assert login(groups).get("/manager/get_identity").status_code == 200


def test_identity_needs_a_known_group(login):
    assert login(["viewer"]).get("/manager/get_identity").status_code == 403
