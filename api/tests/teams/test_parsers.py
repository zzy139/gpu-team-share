import pytest

from api.teams import cluster
from api.teams.cluster import (
    compute_resource_for_node,
    instance_type_for_job,
    parse_compute_resources,
    split_nodelist,
)
from api.teams.slurm import (
    parse_assoc,
    parse_grp_tres,
    parse_qos,
    parse_queue,
    parse_sacct_jobs,
    parse_sections,
    parse_sinfo,
    parse_sinfo_ice,
    parse_sinfo_nodes,
    parse_team_jobs,
    parse_type_quota_conf,
    queue_usage,
    type_qos_limits,
)
from . import slurm_outputs as out


def lines(text):
    return text.splitlines()


def test_parse_assoc():
    teams = parse_assoc(lines(out.ASSOC))
    assert sorted(teams) == ["teama", "teamb"]
    assert teams["teama"]["fairshare"] == 60
    assert teams["teama"]["members"][0]["user"] == "alice"
    assert "teama-g6e-xlarge" in teams["teama"]["members"][0]["qos"]
    with_cluster = parse_assoc(lines(out.ASSOC_WITH_CLUSTER))
    assert with_cluster["teamb"]["members"] == [{"user": "bob", "qos": ["borrow", "teamb-std"],
                                                 "default_qos": "teamb-std"}]


def test_parse_qos_and_type_limits():
    qos = parse_qos(lines(out.QOS))
    assert qos["teama-std"]["grp_tres"] == {"node": 0}
    assert qos["teama-g6e-xlarge"] == {"name": "teama-g6e-xlarge", "priority": 100, "grp_tres": {"node": 2},
                                       "max_wall": None}
    assert qos["borrow"]["max_wall"] == "04:00:00"
    assert type_qos_limits("teama", qos, ["g6e-xlarge", "g6e-2xlarge", "g6e-12xlarge"]) == \
        {"g6e-xlarge": 2, "g6e-2xlarge": 1}
    assert type_qos_limits("teamb", qos, ["g6e-xlarge", "g6e-12xlarge"]) == {"g6e-12xlarge": 1}


def test_parse_grp_tres():
    assert parse_grp_tres("node=8,gres/gpu=2") == {"node": 8, "gres/gpu": 2}
    assert parse_grp_tres("") == {}
    assert parse_grp_tres("mem=15564M") == {"mem": "15564M"}


def test_queue_usage_counts_machines_per_type_and_user():
    usage = queue_usage(parse_queue(lines(out.QUEUE)))
    teama = usage["teama"]
    assert teama["g6e-xlarge"]["running"] == 2      # RUNNING + CONFIGURING
    assert teama["g6e-xlarge"]["pending"] == 1
    assert teama["g6e-xlarge"]["users"] == {"alice": {"running": 2, "pending": 1}}
    assert teama["g6e-2xlarge"]["running"] == 0 and teama["g6e-2xlarge"]["borrowed"] == 1
    assert usage["teamb"]["g6e-12xlarge"]["running"] == 1
    assert queue_usage([]) == {}


def test_parse_sinfo_and_team_jobs():
    nodes = parse_sinfo(lines(out.SINFO))
    assert nodes["g6e-2xlarge"] == {"down~": 8}
    assert nodes["g6e-xlarge"] == {"allocated": 2, "idle~": 6}
    jobs = parse_team_jobs(lines(out.TEAM_JOBS))
    assert [j["job_id"] for j in jobs] == ["21", "22", "23"]
    assert jobs[1]["name"] == "a|b" and jobs[1]["reason"] == "QOSGrpNodeLimit" and jobs[1]["start"] is None
    assert jobs[0]["reason"] is None and jobs[0]["nodes"] == 1


def test_parse_sinfo_with_reasons_flags_insufficient_capacity():
    assert parse_sinfo_ice(lines(out.SINFO)) == {"g6e-xlarge": False, "g6e-2xlarge": True, "g6e-12xlarge": False,
                                                 "build": False}
    # the old 3-column format still parses; the ICE flag is unknown (absent) for it
    old = ["g6e-xlarge|allocated|2", "g6e-xlarge*|idle~|6", "g6e-2xlarge|down~|8"]
    assert parse_sinfo(old) == {"g6e-xlarge": {"allocated": 2, "idle~": 6}, "g6e-2xlarge": {"down~": 8}}
    assert parse_sinfo_ice(old) == {}
    # a '|' inside the reason stays in the reason; "_no_capacity" (ParallelCluster) also counts
    assert parse_sinfo_ice(["p|down~|1|a|b (Code:InsufficientInstanceCapacity)", "q|down~|1|x_no_capacity"]) == \
        {"p": True, "q": True}
    assert parse_sinfo(["p|down~|1|a|b"]) == {"p": {"down~": 1}}


def test_parse_sinfo_nodes():
    nodes = parse_sinfo_nodes(lines(out.SINFO_NODES))
    assert nodes["g6e-xlarge"] == [
        {"state": "allocated", "count": 2, "reason": None, "nodes": "g6e-xlarge-dy-g6e-xlarge-[1-2]"},
        {"state": "idle~", "count": 6, "reason": None, "nodes": "g6e-xlarge-dy-g6e-xlarge-[3-8]"}]
    assert nodes["g6e-2xlarge"][0]["reason"].startswith("(Code:InsufficientInstanceCapacity)")
    assert nodes["g6e-2xlarge"][0]["nodes"] == "g6e-2xlarge-dy-g6e-2xlarge-[1-8]"


def test_parse_type_quota_conf():
    conf = parse_type_quota_conf(lines(out.TYPE_QUOTA_CONF))
    assert conf["types"] == {"g6e-12xlarge": "g6e.12xlarge", "g6e-2xlarge": "g6e.2xlarge",
                             "g6e-xlarge": "g6e.xlarge"}
    assert conf["multi"] == ["mixed"]
    assert conf["quota"]["teama"] == {"g6e-2xlarge": 1, "g6e-xlarge": 2}


def test_parse_sacct_jobs():
    jobs = parse_sacct_jobs(lines(out.SACCT_JOBS))
    assert len(jobs) == 4
    assert jobs[0]["account"] == "teama" and jobs[0]["nodelist"] == "g6e-xlarge-dy-g6e-xlarge-1"
    assert jobs[0]["elapsed"] == 3600
    assert jobs[1]["alloc_tres"]["node"] == 2
    assert jobs[3]["alloc_tres"] == {}


def test_parse_sections_ignores_noise_and_strips_lines():
    text = "noise\n@@ASSOC\n teama||60|normal|\n\n@@QOS\n@@QUEUE\nteama|alice|g6e-xlarge|1|q|RUNNING"
    assert parse_sections(text) == {"ASSOC": ["teama||60|normal|"], "QOS": [],
                                    "QUEUE": ["teama|alice|g6e-xlarge|1|q|RUNNING"]}


def test_parse_compute_resources():
    mapping = parse_compute_resources(out.LEGACY_CLUSTER_CONFIG)
    assert mapping[("g6", "g6-1gpu")] == ["g6.xlarge"]
    assert mapping[("g6", "g6-4gpu")] == ["g6.12xlarge"]
    assert mapping[("g6e", "g6e-4gpu")] == ["g6e.12xlarge"]
    assert mapping[("mixed", "flex")] == ["g6.xlarge", "g6e.xlarge"]


def test_split_nodelist():
    assert split_nodelist("a-[1,3],b-2") == ["a-[1,3]", "b-2"]
    assert split_nodelist("") == []


@pytest.mark.parametrize("partition,nodelist,expected", [
    ("g6", "g6-dy-g6-1gpu-1", "g6.xlarge"),
    ("g6", "g6-st-g6-4gpu-2", "g6.12xlarge"),
    ("g6e", "g6e-dy-g6e-4gpu-[1-2]", "g6e.12xlarge"),
    ("g6e", "g6e-dy-g6e-1gpu-[1,3-4]", "g6e.xlarge"),
    ("g6", "g6-dy-g6-1gpu-1,g6-dy-g6-4gpu-1", None),
    ("mixed", "mixed-dy-flex-1", None),
    ("g6", "None assigned", None),
    ("g6", "g6e-dy-g6e-1gpu-1", None),
    ("g6,g6e", "g6-dy-g6-1gpu-1", None),
    ("", "", None),
])
def test_instance_type_for_job(partition, nodelist, expected):
    mapping = parse_compute_resources(out.LEGACY_CLUSTER_CONFIG)
    assert instance_type_for_job(partition, nodelist, mapping) == expected


def test_compute_resource_for_node():
    assert compute_resource_for_node("g6", "g6-dy-g6-1gpu-12") == "g6-1gpu"
    assert compute_resource_for_node("g6", "login-1") is None


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def clear_caches():
    cluster._head_node_cache.clear()
    cluster._config_cache.clear()


def test_head_node_resolved_from_pc_api_and_cached(mocker, app):
    sig = mocker.patch("api.teams.cluster.sigv4_request", return_value=_Resp(200, {
        "scheduler": {"type": "slurm"}, "headNode": {"instanceId": "i-0123456789abcdef0"}}))
    mocker.patch("api.teams.cluster.get_base_url", return_value="https://x.execute-api.us-west-2.amazonaws.com/prod")
    with app.test_request_context():
        assert cluster.head_node("hpc-poc", "us-west-2") == "i-0123456789abcdef0"
        assert cluster.head_node("hpc-poc", "us-west-2") == "i-0123456789abcdef0"
    sig.assert_called_once()
    assert sig.call_args.args[2] == "/v3/clusters/hpc-poc"
    assert sig.call_args.kwargs["params"] == {"region": "us-west-2"}


@pytest.mark.parametrize("body", [
    {"scheduler": {"type": "awsbatch"}, "headNode": {"instanceId": "i-0123456789abcdef0"}},
    {"scheduler": {"type": "slurm"}},
    {"scheduler": {"type": "slurm"}, "headNode": {"instanceId": "i-x; id"}},
])
def test_head_node_rejects_non_slurm_or_missing_head(mocker, app, body):
    mocker.patch("api.teams.cluster.sigv4_request", return_value=_Resp(200, body))
    mocker.patch("api.teams.cluster.get_base_url", return_value="https://x.execute-api.us-west-2.amazonaws.com/prod")
    with app.test_request_context(), pytest.raises(ValueError):
        cluster.head_node("hpc-poc", "us-west-2")


def test_head_node_missing_cluster_404(mocker, app):
    from werkzeug.exceptions import NotFound
    mocker.patch("api.teams.cluster.sigv4_request", return_value=_Resp(404, {}))
    mocker.patch("api.teams.cluster.get_base_url", return_value="https://x.execute-api.us-west-2.amazonaws.com/prod")
    with app.test_request_context(), pytest.raises(NotFound):
        cluster.head_node("missing", "us-west-2")


def test_head_node_rejects_bad_cluster_name(mocker, app):
    sig = mocker.patch("api.teams.cluster.sigv4_request")
    with app.test_request_context(), pytest.raises(ValueError):
        cluster.head_node("../v3/x", "us-west-2")
    sig.assert_not_called()


def test_list_slurm_clusters(mocker, app):
    mocker.patch("api.teams.cluster.get_base_url", return_value="https://x.execute-api.us-west-2.amazonaws.com/prod")
    mocker.patch("api.teams.cluster.sigv4_request", side_effect=[
        _Resp(200, {"clusters": [{"clusterName": "hpc-poc", "region": "us-west-2", "scheduler": {"type": "slurm"}},
                                 {"clusterName": "batch", "region": "us-west-2", "scheduler": {"type": "awsbatch"}}],
                    "nextToken": "abc/="}),
        _Resp(200, {"clusters": [{"clusterName": "aaa", "region": "us-west-2", "scheduler": {"type": "slurm"}}]}),
    ])
    with app.test_request_context():
        assert cluster.list_slurm_clusters("us-west-2") == [{"name": "aaa", "region": "us-west-2"},
                                                             {"name": "hpc-poc", "region": "us-west-2"}]
