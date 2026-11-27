import json
import re
import shlex

import pytest

from api.teams.slurm import GEN_TYPE_QUOTA_SCRIPT

from api.teams import cluster, costs
from api.teams.costs import TeamCostExplorerClient
from . import slurm_outputs as out
from .conftest import PRICES, walk_keys

HEAD_NODE = "i-0123456789abcdef0"
Q = "cluster_name=hpc-poc&region=us-west-2"

SECTION_OUTPUTS = {
    "ASSOC": out.ASSOC,
    "QOS": out.QOS,
    "QUEUE": out.QUEUE,
    "SINFO": out.SINFO,
    "TEAMJOBS": out.TEAM_JOBS,
    "JOBS": out.SACCT_JOBS,
}
TL = {"g6e.xlarge": 2}

READ_URLS = [
    "/team-quota/clusters?region=us-west-2",
    f"/team-quota/teams?{Q}",
    f"/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01",
    f"/team-quota/trend?{Q}&months=2",
    f"/team-quota/catalog?{Q}",
    f"/team-quota/lead/jobs?{Q}&team=teama",
    f"/team-quota/lead/quota?{Q}&team=teama",
    f"/team-quota/capacity?{Q}",
]
WRITE_CALLS = [
    ("post", f"/team-quota/teams?{Q}", {"name": "qatest", "fairshare": 10, "type_limits": TL}),
    ("patch", f"/team-quota/teams/teamb?{Q}", {"type_limits": TL}),
    ("delete", f"/team-quota/teams/teamb?{Q}", None),
    ("post", f"/team-quota/teams/teamb/members?{Q}", {"user": "carol"}),
    ("delete", f"/team-quota/teams/teamb/members/bob?{Q}", None),
    ("post", f"/team-quota/teams/teamb/members?{Q}", {"email": "carol@example.com"}),
    ("post", f"/team-quota/lifecycle/teams?{Q}", {"name": "teamc", "fairshare": 10, "type_limits": TL}),
    ("post", f"/team-quota/lifecycle/teams/teamc/advance?{Q}", {}),
    ("post", f"/team-quota/teams/teamb/members/bob/offboard?{Q}", {"jobs": "cancel"}),
    ("post", f"/team-quota/teams/teamb/members/bob/reenable?{Q}", {}),
]
ADMIN_READ_URLS = [
    f"/team-quota/lifecycle/teams?{Q}",
    f"/team-quota/lifecycle/teams/teamc?{Q}",
    "/team-quota/audit",
    "/team-quota/audit?month=2026-11&team=teama",
]
LEAD_READ_URLS = READ_URLS


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeHeadNode:
    """Stands in for ssm_command: answers read sections from recorded outputs and records every command."""

    def __init__(self):
        self.commands = []
        self.outputs = dict(SECTION_OUTPUTS)
        self.fail_writes = None

    def __call__(self, region, instance_id, user, command):
        assert (region, instance_id, user) == ("us-west-2", HEAD_NODE, "root")
        self.commands.append(command)
        if " -i " in command:
            if self.fail_writes:
                raise Exception(self.fail_writes)
            return ""
        lines = []
        for name in re.findall(r"echo @@([A-Za-z0-9_-]+)", command):
            lines.append(f"@@{name}")
            key = "SREPORT" if name.startswith("MONTH_") else name
            lines.extend(self.outputs.get(key, "").splitlines())
        return "\n".join(lines)

    @property
    def writes(self):
        return [c for c in self.commands if " -i " in c]


@pytest.fixture(autouse=True)
def backend(mocker):
    cluster._head_node_cache.clear()
    cluster._config_cache.clear()
    costs._ce_cache.clear()
    mocker.patch("api.teams.cluster.get_base_url", return_value="https://abc.execute-api.us-west-2.amazonaws.com/prod")

    def _pc_api(method, host, path, params=None, **kwargs):
        if path == "/v3/clusters":
            return _Resp(200, {"clusters": [{"clusterName": "hpc-poc", "region": "us-west-2",
                                             "scheduler": {"type": "slurm"}}]})
        if path == "/v3/clusters/hpc-poc":
            return _Resp(200, {"scheduler": {"type": "slurm"}, "headNode": {"instanceId": HEAD_NODE}})
        return _Resp(404, {})

    pc_api = mocker.patch("api.teams.cluster.sigv4_request", side_effect=_pc_api)
    mocker.patch("api.teams.cluster.get_cluster_config_text", return_value=out.CLUSTER_CONFIG)
    fake = FakeHeadNode()
    mocker.patch("api.teams.slurm.ssm_command", side_effect=fake)

    ce = mocker.Mock()

    def _ce(**kwargs):
        amount = "10.0" if "And" in kwargs["Filter"] else "15.0"
        return {"ResultsByTime": [{"TimePeriod": {"Start": kwargs["TimePeriod"]["Start"][:8] + "01",
                                                  "End": kwargs["TimePeriod"]["End"]},
                                   "Total": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}}}]}

    ce.get_cost_and_usage.side_effect = _ce
    mocker.patch("api.teams.routes.cost_client", return_value=TeamCostExplorerClient(ce))
    audit_logger = mocker.patch("api.teams.audit.logger", new=mocker.Mock())
    return {"ssm": fake, "ce": ce, "audit": audit_logger, "pc_api": pc_api}


@pytest.fixture
def login(mocker, client):
    def _login(groups, username="user-1"):
        mocker.patch("api.PclusterApiHandler.jwt_decode",
                     return_value={"cognito:groups": groups, "username": username, "email": f"{username}@example.com"})
        client.set_cookie("accessToken", "access-token")
        return client

    return _login


def _call(client, method, url, body=None):
    kwargs = {"json": body} if body is not None else {}
    return getattr(client, method)(url, **kwargs)


# --- authentication and authorization ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", READ_URLS + ADMIN_READ_URLS)
def test_anonymous_read_is_rejected(client, backend, url):
    assert client.get(url).status_code == 401
    assert backend["ssm"].commands == []


@pytest.mark.parametrize("method,url,body", WRITE_CALLS)
def test_anonymous_write_is_rejected(client, backend, method, url, body):
    assert _call(client, method, url, body).status_code == 401
    assert backend["ssm"].commands == []


@pytest.mark.parametrize("method,url,body", WRITE_CALLS)
def test_team_lead_cannot_write(login, backend, method, url, body):
    client = login(["team-teama"])
    assert _call(client, method, url, body).status_code == 403
    assert backend["ssm"].commands == []


@pytest.mark.parametrize("method,url,body", WRITE_CALLS)
def test_team_lead_cannot_write_even_without_csrf(login, backend, mock_csrf_needed, method, url, body):
    client = login(["team-teama"])
    assert _call(client, method, url, body).status_code == 403
    assert backend["ssm"].writes == []


@pytest.mark.parametrize("url", READ_URLS + ADMIN_READ_URLS)
def test_user_without_admin_or_team_group_is_rejected(login, backend, url):
    client = login(["viewer"])
    assert client.get(url).status_code == 403


@pytest.mark.parametrize("url", ADMIN_READ_URLS)
def test_team_lead_cannot_read_lifecycle(login, backend, url):
    assert login(["team-teama"]).get(url).status_code == 403
    assert backend["ssm"].commands == []


MEMBER_GROUPS = ["member-teama"]


@pytest.mark.parametrize("url", [u for u in READ_URLS + ADMIN_READ_URLS if "/clusters" not in u])
def test_member_cannot_read_admin_or_lead_routes(login, backend, url):
    assert login(MEMBER_GROUPS).get(url).status_code == 403
    assert backend["ssm"].commands == []


@pytest.mark.parametrize("method,url,body", WRITE_CALLS)
def test_member_cannot_write_admin_routes(login, backend, mock_csrf_needed, method, url, body):
    assert _call(login(MEMBER_GROUPS), method, url, body).status_code == 403
    assert backend["ssm"].commands == []


@pytest.mark.parametrize("url", [
    f"/team-quota/teams?{Q}&team=teamb",
    f"/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01&team=teamb",
    f"/team-quota/trend?{Q}&team=teamb",
    f"/team-quota/lead/jobs?{Q}&team=teamb",
    f"/team-quota/lead/quota?{Q}&team=teamb",
    f"/team-quota/capacity?{Q}&team=teamb",
])
def test_team_lead_cannot_read_other_team(login, backend, url):
    client = login(["team-teama"])
    assert client.get(url).status_code == 403
    assert backend["ssm"].commands == []


@pytest.mark.parametrize("url", ["/api?path=/v3/clusters", "/manager/list_users", "/logs"])
def test_team_lead_cannot_use_upstream_admin_routes(login, url):
    client = login(["team-teama"])
    response = client.post(url, json={"logs": []}) if url == "/logs" else client.get(url)
    assert response.status_code == 403


# --- read routes ------------------------------------------------------------------------------------------------


def test_lead_lists_clusters(login, backend):
    response = login(["team-teama"]).get("/team-quota/clusters?region=us-west-2")
    assert response.status_code == 200
    assert response.json == {"clusters": [{"name": "hpc-poc", "region": "us-west-2"}]}


def test_clusters_managed_by_the_team_lifecycle_come_first(login, backend, configured, mocker):
    # The page preselects the first entry; another cluster in the region must not be preselected.
    mocker.patch("api.teams.cluster.list_slurm_clusters", return_value=[
        {"name": "aaa-other", "region": "us-west-2"}, {"name": "hpc-poc", "region": "us-west-2"}])
    response = login(["admin"]).get("/team-quota/clusters?region=us-west-2")
    assert [c["name"] for c in response.json["clusters"]] == ["hpc-poc", "aaa-other"]


def test_admin_lists_all_teams(login, backend):
    response = login(["admin"]).get(f"/team-quota/teams?{Q}")
    assert response.status_code == 200
    body = response.json
    assert [t["name"] for t in body["teams"]] == ["teama", "teamb"]
    assert body["editable"] is True
    assert [t["instance_type"] for t in body["catalog"]] == ["g6e.xlarge", "g6e.2xlarge", "g6e.12xlarge"]
    assert {q["queue"]: q["reason"] for q in body["unmanaged_queues"]} == {"build": "no_gpu", "mixed": "multi_type"}
    teama, teamb = body["teams"]
    assert teamb["fairshare"] == 40 and teamb["fairshare_share"] == 0.4
    assert teama["qos"] == "teama-std"
    assert teama["type_limits"] == {"g6e.xlarge": 2, "g6e.2xlarge": 1, "g6e.12xlarge": 0}
    assert teama["running_by_type"]["g6e.xlarge"] == {"partition": "g6e-xlarge", "running": 2, "pending": 1,
                                                      "borrowed": 0, "users": {"alice": {"running": 2, "pending": 1}}}
    assert teama["running_by_type"]["g6e.2xlarge"]["borrowed"] == 1
    assert teama["running"] == {"jobs": 4, "machines": 3, "gpus": 3}
    assert teamb["running"] == {"jobs": 1, "machines": 1, "gpus": 4}
    assert teama["members"] == ["alice"]
    command = backend["ssm"].commands[0]
    assert "echo @@QUEUE ; /opt/slurm/bin/squeue -a -h -t R,CF,PD" in command
    backend["pc_api"].assert_any_call("GET", "https://abc.execute-api.us-west-2.amazonaws.com/prod",
                                      "/v3/clusters/hpc-poc", params={"region": "us-west-2"})


def test_catalog_route(login, backend):
    body = login(["team-teama"]).get(f"/team-quota/catalog?{Q}").json
    assert body["types"][0] == {"instance_type": "g6e.xlarge", "partition": "g6e-xlarge", "gpus": 1, "vcpus": 4,
                                "memory_gib": 32.0, "gpu_model": "NVIDIA L40S", "gpu_memory_gib": 44.7,
                                "gpu_memory_gb": 48, "price_per_hour": 1.861}
    assert {"queue": "mixed", "reason": "multi_type", "instance_types": ["g6.xlarge", "g6e.xlarge"]} in \
        body["unmanaged_queues"]
    assert backend["ssm"].commands == []  # the catalog comes from the cluster configuration only


def test_lead_lists_only_own_team(login, backend):
    response = login(["team-teama"]).get(f"/team-quota/teams?{Q}")
    assert response.status_code == 200
    assert [t["name"] for t in response.json["teams"]] == ["teama"]
    assert response.json["editable"] is False
    assert login(["team-teama"]).get(f"/team-quota/teams?{Q}&team=teama").status_code == 200


def test_client_supplied_instance_id_is_ignored(login, backend):
    response = login(["admin"]).get(f"/team-quota/teams?{Q}&instance_id=i-0000000000000dead")
    assert response.status_code == 200  # FakeHeadNode asserts the resolved head node is used


def test_unknown_cluster_is_404(login, backend):
    response = login(["admin"]).get("/team-quota/teams?cluster_name=other&region=us-west-2")
    assert response.status_code == 404
    assert response.json["code"] == 404


@pytest.mark.parametrize("query", ["cluster_name=hpc;id", "cluster_name=hpc-poc&region=mars-1",
                                   "cluster_name=hpc-poc&team=TeamA", "region=us-west-2"])
def test_invalid_read_params_are_400(login, backend, query):
    assert login(["admin"]).get(f"/team-quota/teams?{query}").status_code == 400
    assert backend["ssm"].commands == []


TEAMA_COST = 1.861 + 2.24208          # 1 x g6e.xlarge for 1 h + 2 x g6e.2xlarge for 0.5 h
TEAMB_COST = 2 * 10.49264              # 1 x g6e.12xlarge for 2 h


def test_admin_usage(login, backend):
    response = login(["admin"]).get(f"/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01")
    assert response.status_code == 200
    body = response.json
    teams = {t["name"]: t for t in body["teams"]}
    assert teams["teama"]["instance_hours"] == pytest.approx(2.0)
    assert teams["teama"]["gpu_hours"] == pytest.approx(2.0)
    assert teams["teama"]["estimate_cost"] == pytest.approx(TEAMA_COST, abs=1e-3)
    assert teams["teamb"]["estimate_cost"] == pytest.approx(TEAMB_COST, abs=1e-3)
    share = TEAMA_COST / (TEAMA_COST + TEAMB_COST)
    assert teams["teama"]["cost_share"] == pytest.approx(share, abs=1e-5)
    assert teams["teama"]["compute_cost"] == pytest.approx(10 * share, abs=1e-3)
    assert teams["teamb"]["shared_cost"] == pytest.approx(5 * (1 - share), abs=1e-3)
    assert [t["instance_type"] for t in teams["teama"]["by_type"]] == ["g6e.2xlarge", "g6e.xlarge"]
    members = {(m["team"], m["user"]): m for m in body["by_member"]}
    assert set(members) == {("teama", "alice"), ("teama", "carol"), ("teamb", "bob")}
    assert members[("teama", "carol")]["estimate_cost"] == pytest.approx(2.24208, abs=1e-4)
    assert body["totals"]["compute_cost"] == 10.0
    assert body["totals"]["shared_cost"] == 5.0
    assert body["totals"]["total_cost"] == 15.0
    assert body["totals"]["estimate_cost"] == pytest.approx(TEAMA_COST + TEAMB_COST, abs=1e-3)
    assert body["sources"] == {"billed": "cost-explorer", "estimate": "pricing-api"}
    assert body["billed_available"] is True
    assert body["prices"]["g6e.xlarge"] == PRICES["g6e.xlarge"]
    command = backend["ssm"].commands[0]
    assert "sreport" not in command and "/opt/slurm/bin/sacct -a -X -n -P -S 2026-11-01 -E 2026-12-01" in command
    ce_call = backend["ce"].get_cost_and_usage.call_args_list[0].kwargs
    assert ce_call["Granularity"] == "MONTHLY"
    assert ce_call["Filter"]["And"][0]["Tags"]["Values"] == ["hpc-poc"]


def test_lead_usage_has_only_own_team_and_no_totals(login, backend):
    response = login(["team-teama"]).get(f"/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01")
    assert response.status_code == 200
    body = response.json
    assert [t["name"] for t in body["teams"]] == ["teama"]
    assert {m["team"] for m in body["by_member"]} == {"teama"}
    assert "totals" not in body


@pytest.mark.parametrize("path", [
    "/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01&project=other",
    "/team-quota/trend?{Q}&months=2&project=other",
])
def test_lead_cannot_query_other_project(login, backend, path):
    response = login(["team-teama"]).get(path.format(Q=Q))
    assert response.status_code == 403
    backend["ce"].get_cost_and_usage.assert_not_called()
    assert backend["ssm"].commands == []


def test_lead_may_pass_own_cluster_project(login, backend):
    response = login(["team-teama"]).get(f"/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01&project=hpc-poc")
    assert response.status_code == 200
    for call in backend["ce"].get_cost_and_usage.call_args_list:
        assert "other" not in str(call.kwargs["Filter"])


def test_admin_may_query_other_project(login, backend):
    response = login(["admin"]).get(f"/team-quota/trend?{Q}&months=1&project=other")
    assert response.status_code == 200
    assert "other" in str(backend["ce"].get_cost_and_usage.call_args_list[0].kwargs["Filter"])


def test_usage_survives_cost_explorer_failure(login, backend):
    backend["ce"].get_cost_and_usage.side_effect = Exception("boom")
    response = login(["admin"]).get(f"/team-quota/usage?{Q}&start=2026-11-01&end=2026-12-01")
    assert response.status_code == 200
    body = response.json
    assert body["billed_available"] is False
    assert "billed-unavailable" in body["notes"]
    teama = next(t for t in body["teams"] if t["name"] == "teama")
    assert teama["compute_cost"] is None
    assert teama["estimate_cost"] > 0


def test_usage_rejects_bad_period(login, backend):
    client = login(["admin"])
    assert client.get(f"/team-quota/usage?{Q}&start=2026-12-01&end=2026-11-01").status_code == 400
    assert client.get(f"/team-quota/usage?{Q}&start=2024-01-01&end=2026-11-01").status_code == 400
    assert backend["ssm"].commands == []


def test_trend(login, backend):
    response = login(["admin"]).get(f"/team-quota/trend?{Q}&months=3")
    assert response.status_code == 200
    months = response.json["months"]
    assert len(months) == 3
    assert [t["name"] for t in months[-1]["teams"]] == ["teama", "teamb"]
    assert sum(m["totals"]["compute_cost"] for m in months) == 10.0
    assert months[0]["totals"]["shared_cost"] == 5.0
    command = backend["ssm"].commands[0]
    assert len(re.findall(r"echo @@MONTH_\d{4}-\d{2}", command)) == 3
    assert "sreport" not in command
    assert len(backend["ssm"].commands) == 1
    assert backend["ce"].get_cost_and_usage.call_count == 2


def test_lead_trend_is_scoped(login, backend):
    response = login(["team-teamb"]).get(f"/team-quota/trend?{Q}&months=1")
    assert response.status_code == 200
    assert [t["name"] for t in response.json["months"][0]["teams"]] == ["teamb"]
    assert "totals" not in response.json["months"][0]


def test_slurm_error_message_hidden_from_leads(login, backend, mocker):
    mocker.patch("api.teams.slurm.ssm_command", side_effect=Exception("sacctmgr: secret detail"))
    response = login(["team-teama"]).get(f"/team-quota/teams?{Q}")
    assert response.status_code == 400
    assert "secret detail" not in response.json["message"]


# --- write routes -----------------------------------------------------------------------------------------------


def test_admin_updates_teamb_type_limits(login, backend, mock_csrf_needed):
    response = login(["admin"], username="admin-user").patch(
        f"/team-quota/teams/teamb?{Q}", json={"type_limits": {"g6e.xlarge": 3, "g6e.12xlarge": 0}})
    assert response.status_code == 200, response.json
    assert backend["ssm"].writes == [
        "/opt/slurm/bin/sacctmgr -i add qos teamb-g6e-xlarge Priority=100 GrpTRES=node=3 && "
        "/opt/slurm/bin/sacctmgr -i modify account where name=teamb set "
        "QOS=teamb-std,borrow,envbuild,teamb-g6e-xlarge DefaultQOS=teamb-std && "
        "/opt/slurm/bin/sacctmgr -i modify user where account=teamb set "
        "QOS=teamb-std,borrow,envbuild,teamb-g6e-xlarge DefaultQOS=teamb-std && "
        "/opt/slurm/bin/sacctmgr -i delete qos where name=teamb-g6e-12xlarge && "
        "bash -c " + shlex.quote(GEN_TYPE_QUOTA_SCRIPT) + " pcui-gen-type-quota /opt/slurm/etc/gen-type-quota.sh"
    ]
    entry = backend["audit"].info.call_args
    assert entry.args[0] == "team-quota-audit"
    extra = entry.kwargs["extra"]
    assert extra["actor"] == "admin-user"
    assert extra["actor_email"] == "admin-user@example.com"
    assert (extra["action"], extra["cluster"], extra["team"], extra["result"]) == ("update_team", "hpc-poc", "teamb", "success")
    assert extra["params"] == {"type_limits": {"g6e.xlarge": 3, "g6e.12xlarge": 0}}


def test_admin_modifies_an_existing_type_and_fairshare(login, backend, mock_csrf_needed):
    response = login(["admin"]).patch(f"/team-quota/teams/teama?{Q}",
                                      json={"fairshare": 55, "type_limits": {"g6e.xlarge": 4}})
    assert response.status_code == 200
    (write,) = backend["ssm"].writes
    assert write.startswith("/opt/slurm/bin/sacctmgr -i modify qos where name=teama-g6e-xlarge set GrpTRES=node=4 && ")
    assert "QOS=teama-std,borrow,envbuild,teama-g6e-2xlarge,teama-g6e-xlarge" in write
    assert write.endswith("/opt/slurm/bin/sacctmgr -i modify account where name=teama set Fairshare=55")


@pytest.mark.parametrize("type_limits", [{"p5.48xlarge": 1}, {"g6.xlarge": 1}, {"m7i.xlarge": 1}])
def test_unknown_or_unmanaged_instance_type_is_400(login, backend, mock_csrf_needed, type_limits):
    response = login(["admin"]).patch(f"/team-quota/teams/teama?{Q}", json={"type_limits": type_limits})
    assert response.status_code == 400
    assert "只含这一种机型的队列" in response.json["message"]
    assert backend["ssm"].writes == []


@pytest.mark.parametrize("method,url,body", [
    ("patch", f"/team-quota/teams/teamb?{Q}", {"type_limits": {"g6e.xlarge": "6; reboot"}}),
    ("patch", f"/team-quota/teams/teamb?{Q}", {"type_limits": {"g6e.xlarge;id": 1}}),
    ("patch", f"/team-quota/teams/teamb?{Q}", {"type_limits": {"g6e.xlarge": 1001}}),
    ("patch", f"/team-quota/teams/teamb?{Q}", {}),
    ("patch", f"/team-quota/teams/teamb?{Q}", {"billing_limit": 6}),
    ("patch", f"/team-quota/teams/teamb?{Q}", {"fairshare": 6, "qos": "normal"}),
    ("patch", f"/team-quota/teams/TeamB?{Q}", {"fairshare": 6}),
    ("patch", f"/team-quota/teams/borrow?{Q}", {"fairshare": 6}),
    ("patch", "/team-quota/teams/teamb?cluster_name=hpc;id", {"fairshare": 6}),
    ("post", f"/team-quota/teams?{Q}", {"name": "$(id)", "fairshare": 10, "type_limits": TL}),
    ("post", f"/team-quota/teams?{Q}", {"name": "root", "fairshare": 10, "type_limits": TL}),
    ("post", f"/team-quota/teams?{Q}", {"name": "qatest", "fairshare": 0, "type_limits": TL}),
    ("post", f"/team-quota/teams?{Q}", {"name": "qatest", "fairshare": 10, "billing_limit": 2}),
    ("post", f"/team-quota/teams/teamb/members?{Q}", {"user": "bob;id"}),
    ("delete", f"/team-quota/teams/teamb/members/root?{Q}", None),
    ("delete", f"/team-quota/teams/normal?{Q}", None),
])
def test_invalid_writes_are_400_without_ssm(login, backend, mock_csrf_needed, method, url, body):
    response = _call(login(["admin"]), method, url, body)
    assert response.status_code == 400
    assert backend["ssm"].commands == []


def test_unknown_team_is_404(login, backend, mock_csrf_needed):
    response = login(["admin"]).patch(f"/team-quota/teams/nosuch?{Q}", json={"fairshare": 6})
    assert response.status_code == 404
    assert backend["ssm"].writes == []
    assert backend["audit"].info.call_args.kwargs["extra"]["result"] == "rejected"


def test_create_team(login, backend, mock_csrf_needed):
    response = login(["admin"]).post(f"/team-quota/teams?{Q}", json={
        "name": "qatest", "fairshare": 10, "type_limits": {"g6e.xlarge": 2, "g6e.2xlarge": 0}})
    assert response.status_code == 201
    assert backend["ssm"].writes == [
        "/opt/slurm/bin/sacctmgr -i add account qatest Description=qatest Organization=hpc Fairshare=10 && "
        "/opt/slurm/bin/sacctmgr -i add qos qatest-std Priority=100 GrpTRES=node=0 && "
        "/opt/slurm/bin/sacctmgr -i add qos qatest-g6e-xlarge Priority=100 GrpTRES=node=2 && "
        "/opt/slurm/bin/sacctmgr -i modify account where name=qatest set "
        "QOS=qatest-std,borrow,envbuild,qatest-g6e-xlarge DefaultQOS=qatest-std && "
        "bash -c " + shlex.quote(GEN_TYPE_QUOTA_SCRIPT) + " pcui-gen-type-quota /opt/slurm/etc/gen-type-quota.sh"
    ]


def test_create_existing_team_is_409(login, backend, mock_csrf_needed):
    response = login(["admin"]).post(f"/team-quota/teams?{Q}", json={"name": "teama", "fairshare": 10, "type_limits": TL})
    assert response.status_code == 409
    assert backend["ssm"].writes == []


def test_delete_team_removes_members_without_lifecycle_config(login, backend, mock_csrf_needed):
    response = login(["admin"]).delete(f"/team-quota/teams/teamb?{Q}")
    assert response.status_code == 200
    assert response.json["removed"] == ["slurm-user:bob", "slurm-account", "slurm-qos",
                                        "slurm-qos:teamb-g6e-12xlarge"]
    assert response.json["retained"] == {}
    assert backend["ssm"].writes == [
        "/opt/slurm/bin/sacctmgr -i delete user where name=bob account=teamb && "
        "/opt/slurm/bin/sacctmgr -i delete account where name=teamb && "
        "/opt/slurm/bin/sacctmgr -i delete qos where name=teamb-std && "
        "/opt/slurm/bin/sacctmgr -i delete qos where name=teamb-g6e-12xlarge && "
        "bash -c " + shlex.quote(GEN_TYPE_QUOTA_SCRIPT) + " pcui-gen-type-quota /opt/slurm/etc/gen-type-quota.sh"
    ]


def test_lifecycle_routes_unconfigured(login, backend, mock_csrf_needed):
    client = login(["admin"])
    assert client.get(f"/team-quota/lifecycle/teams?{Q}").json == {"configured": False, "records": [],
                                                                   "email_domains": []}
    response = client.post(f"/team-quota/lifecycle/teams?{Q}", json={"name": "teamc", "fairshare": 10,
                                                                      "type_limits": TL})
    assert response.status_code == 409
    assert client.post(f"/team-quota/teams/teamb/members?{Q}", json={"email": "carol@example.com"}).status_code == 409
    assert backend["ssm"].writes == []


def test_delete_empty_team(login, backend, mock_csrf_needed):
    backend["ssm"].outputs["ASSOC"] = out.ASSOC + "\nqatest||10|qatest-std,borrow|qatest-std"
    backend["ssm"].outputs["QOS"] = out.QOS + "\nqatest-std|100|node=0|"
    response = login(["admin"]).delete(f"/team-quota/teams/qatest?{Q}")
    assert response.status_code == 200
    (write,) = backend["ssm"].writes
    assert write.startswith("/opt/slurm/bin/sacctmgr -i delete account where name=qatest && "
                            "/opt/slurm/bin/sacctmgr -i delete qos where name=qatest-std && bash -c ")


def test_add_and_remove_member(login, backend, mock_csrf_needed):
    client = login(["admin"])
    assert client.post(f"/team-quota/teams/teamb/members?{Q}", json={"user": "carol"}).status_code == 201
    assert client.post(f"/team-quota/teams/teamb/members?{Q}", json={"user": "bob"}).status_code == 409
    assert client.delete(f"/team-quota/teams/teamb/members/bob?{Q}").status_code == 200
    assert client.delete(f"/team-quota/teams/teamb/members/carol?{Q}").status_code == 404
    assert backend["ssm"].writes == [
        "/opt/slurm/bin/sacctmgr -i add user carol Account=teamb QOS=teamb-std,borrow,envbuild,teamb-g6e-12xlarge "
        "DefaultQOS=teamb-std",
        "/opt/slurm/bin/sacctmgr -i delete user where name=bob account=teamb",
    ]


def test_slurm_write_failure_is_400_and_audited(login, backend, mock_csrf_needed):
    backend["ssm"].fail_writes = "sacctmgr: error: Job(s) active"
    response = login(["admin"]).delete(f"/team-quota/teams/teamb/members/bob?{Q}")
    assert response.status_code == 400
    assert "Job(s) active" in response.json["message"]
    extra = backend["audit"].info.call_args.kwargs["extra"]
    assert extra["result"] == "failure"
    assert "Job(s) active" in extra["error"]


def test_write_requires_csrf(login, backend):
    response = login(["admin"]).patch(f"/team-quota/teams/teamb?{Q}", json={"fairshare": 6})
    assert response.status_code in (400, 403)
    assert backend["ssm"].commands == []


# --- lead views -------------------------------------------------------------------------------------------------

FORBIDDEN_LEAD_KEYS = {"script", "script_path", "script_content", "command", "stdout", "stderr", "stdout_path",
                       "stderr_path", "content", "content_b64", "workdir"}


def test_lead_jobs_are_metadata_only_with_contextual_reasons(login, backend):
    response = login(["team-teama"]).get(f"/team-quota/lead/jobs?{Q}")
    assert response.status_code == 200
    body = response.json
    assert body["team"] == "teama"
    jobs = {j["job_id"]: j for j in body["jobs"]}
    assert set(jobs) == {"21", "22", "23"}
    assert not FORBIDDEN_LEAD_KEYS & set(walk_keys(body))
    assert jobs["22"]["name"] == "a|b" and jobs["22"]["instance_type"] == "g6e.xlarge"
    assert jobs["22"]["reason_text"] == "g6e.xlarge 团队配额已用满（2/2 台）。可改用 g6e.2xlarge（剩余 1 台），或开启借用"
    assert jobs["23"]["reason_text"].startswith("g6e.2xlarge 暂无容量：AWS 目前没有这种机器可用，系统会自动重试")
    assert jobs["21"]["reason_text"] == ""
    command = backend["ssm"].commands[0]
    assert "/opt/slurm/bin/squeue -a -h -A teama -o" in command
    assert all(" -i " not in c for c in backend["ssm"].commands)


def test_lead_quota_holders(login, backend):
    body = login(["team-teama"]).get(f"/team-quota/lead/quota?{Q}").json
    types = {t["instance_type"]: t for t in body["types"]}
    assert types["g6e.xlarge"] == {"instance_type": "g6e.xlarge", "partition": "g6e-xlarge", "gpus": 1, "limit": 2,
                                   "running": 2, "pending": 1, "borrowed": 0,
                                   "holders": [{"user": "alice", "running": 2, "pending": 1}]}
    assert types["g6e.12xlarge"]["limit"] == 0
    assert {q["queue"] for q in body["unmanaged_queues"]} == {"build", "mixed"}


def test_admin_needs_a_team_for_lead_views(login, backend):
    assert login(["admin"]).get(f"/team-quota/lead/jobs?{Q}").status_code == 400
    assert login(["admin"]).get(f"/team-quota/lead/jobs?{Q}&team=teamb").status_code == 200


# --- audit page -------------------------------------------------------------------------------------------------


def test_audit_page_lists_and_filters_stored_entries(login, backend, mock_csrf_needed, configured, dynamo):
    client = login(["admin"], username="admin-user")
    assert client.patch(f"/team-quota/teams/teamb?{Q}", json={"fairshare": 30}).status_code == 200
    assert client.patch(f"/team-quota/teams/teama?{Q}", json={"type_limits": {"g6e.xlarge": 1}}).status_code == 200
    assert client.patch(f"/team-quota/teams/nosuch?{Q}", json={"fairshare": 3}).status_code == 404
    body = client.get("/team-quota/audit").json
    assert [e["team"] for e in body["entries"]] == ["nosuch", "teama", "teamb"]  # newest first
    assert body["entries"][0]["result"] == "rejected"
    assert body["entries"][2]["actor_email"] == "admin-user@example.com"
    assert body["truncated"] is False
    only_a = client.get("/team-quota/audit?team=teama").json["entries"]
    assert len(only_a) == 1 and '"g6e.xlarge": 1' in only_a[0]["params"]
    assert client.get("/team-quota/audit?action=create_team").json["entries"] == []
    assert len(client.get("/team-quota/audit?actor=ADMIN-USER").json["entries"]) == 3
    assert client.get("/team-quota/audit?actor=someone").json["entries"] == []
    stored = dynamo.with_prefix(next(k[0] for k in dynamo.items if k[0].startswith("AUDIT#")))
    assert all(item["ttl"] > 0 for item in stored)


@pytest.mark.parametrize("query", ["month=2026-13", "from=2026-11-05&to=2026-11-01", "from=2024-01-01&to=2026-11-01",
                                   "team=Team;id", "action=x-y"])
def test_audit_page_rejects_bad_filters(login, backend, configured, query):
    assert login(["admin"]).get(f"/team-quota/audit?{query}").status_code == 400


def test_audit_store_failure_never_fails_the_request(login, backend, mock_csrf_needed, configured, dynamo):
    from botocore.exceptions import ClientError
    dynamo.fail = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "PutItem")
    assert login(["admin"]).patch(f"/team-quota/teams/teamb?{Q}", json={"fairshare": 30}).status_code == 200
    assert backend["audit"].info.call_args.kwargs["extra"]["result"] == "success"


# --- no billing anywhere ----------------------------------------------------------------------------------------


def test_no_billing_key_in_any_read_response(login, backend):
    client = login(["admin"])
    for url in READ_URLS:
        response = client.get(url)
        assert response.status_code == 200, url
        assert not [k for k in walk_keys(response.json) if "billing" in k.lower()], url


# --- 机型与节点 (capacity) ----------------------------------------------------------------------------------------


def test_capacity_for_admins_shows_every_team_and_the_node_states(login, backend):
    backend["ssm"].outputs["SINFO"] = out.SINFO_NODES
    response = login(["admin"]).get(f"/team-quota/capacity?{Q}")
    assert response.status_code == 200, response.json
    body = response.json
    types = {t["instance_type"]: t for t in body["types"]}
    small = types["g6e.xlarge"]
    assert (small["gpu_model"], small["gpu_memory_gb"], small["vcpus"], small["memory_gib"]) == \
        ("NVIDIA L40S", 48, 4, 32.0)
    assert small["status"] == "startable" and small["node_total"] == 8 and small["no_capacity"] is False
    assert small["nodes"] == [
        {"state": "allocated", "count": 2, "reason": None, "nodes": "g6e-xlarge-dy-g6e-xlarge-[1-2]"},
        {"state": "idle~", "count": 6, "reason": None, "nodes": "g6e-xlarge-dy-g6e-xlarge-[3-8]"}]
    assert small["teams"] == [{"team": "teama", "limit": 2, "running": 2, "pending": 1, "borrowed": 0},
                              {"team": "teamb", "limit": 0, "running": 0, "pending": 0, "borrowed": 0}]
    assert types["g6e.2xlarge"]["status"] == "no_capacity" and types["g6e.2xlarge"]["no_capacity"] is True
    assert types["g6e.2xlarge"]["nodes"][0]["reason"].startswith("(Code:InsufficientInstanceCapacity)")
    assert types["g6e.12xlarge"]["status"] == "available"
    assert body["teams"] == ["teama", "teamb"]
    assert body["partitions"] == [{"partition": "build", "nodes": [
        {"state": "idle~", "count": 2, "reason": None, "nodes": "build-dy-build-[1-2]"}]}]
    command = backend["ssm"].commands[0]
    assert "/opt/slurm/bin/sinfo -a -h -o '%R|%T|%D|%E|%N'" in command
    assert all(" -i " not in c for c in backend["ssm"].commands)  # read-only


def test_capacity_for_leads_shows_their_team_only(login, backend):
    backend["ssm"].outputs["SINFO"] = out.SINFO_NODES
    body = login(["team-teamb"]).get(f"/team-quota/capacity?{Q}").json
    assert body["teams"] == ["teamb"] and "partitions" not in body
    big = next(t for t in body["types"] if t["instance_type"] == "g6e.12xlarge")
    assert big["teams"] == [{"team": "teamb", "limit": 1, "running": 1, "pending": 0, "borrowed": 0}]
    assert "teama" not in json.dumps(body)
