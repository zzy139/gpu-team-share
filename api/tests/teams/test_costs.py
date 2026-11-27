from datetime import date, datetime

import pytest
from botocore.exceptions import ClientError

from api.costmonitoring.costexplorer_client import CostExplorerNotActiveException
from api.teams import costs
from api.teams.costs import TeamCostExplorerClient, allocate, ce_end, compute_filter, estimate, project_filter
from api.teams.slurm import parse_sacct_jobs
from . import slurm_outputs as out

from .conftest import PRICES

TYPES = {"g6e-xlarge": "g6e.xlarge", "g6e-2xlarge": "g6e.2xlarge", "g6e-12xlarge": "g6e.12xlarge"}


def type_of(job):
    return TYPES.get(job.get("partition"))


def _result(start, end, amount):
    return {"TimePeriod": {"Start": start, "End": end}, "Total": {"UnblendedCost": {"Amount": str(amount), "Unit": "USD"}}}


@pytest.fixture
def ce(mocker):
    return mocker.Mock()


def test_filters():
    assert project_filter("hpc-poc") == {"Tags": {"Key": "Project", "Values": ["hpc-poc"], "MatchOptions": ["EQUALS"]}}
    assert compute_filter("hpc-poc") == {"And": [
        {"Tags": {"Key": "Project", "Values": ["hpc-poc"], "MatchOptions": ["EQUALS"]}},
        {"Tags": {"Key": "parallelcluster:node-type", "Values": ["Compute"], "MatchOptions": ["EQUALS"]}},
    ]}


def test_get_cost_for_filter_request_shape_and_pagination(ce):
    ce.get_cost_and_usage.side_effect = [
        {"ResultsByTime": [_result("2026-11-01", "2026-12-01", 3.5)], "NextPageToken": "tok"},
        {"ResultsByTime": [_result("2026-10-01", "2026-11-01", 1.25)]},
    ]
    client = TeamCostExplorerClient(ce)
    result = client.get_cost_for_filter(compute_filter("hpc-poc"), "2026-10-01", "2026-11-03", "MONTHLY")
    first, second = ce.get_cost_and_usage.call_args_list
    assert first.kwargs == {
        "TimePeriod": {"Start": "2026-10-01", "End": "2026-11-03"},
        "Granularity": "MONTHLY",
        "Filter": compute_filter("hpc-poc"),
        "Metrics": ["UnblendedCost"],
    }
    assert second.kwargs["NextPageToken"] == "tok"
    assert [c["amount"] for c in result] == [1.25, 3.5]
    assert result[0]["period"] == {"start": "2026-10-01", "end": "2026-11-01"}


def test_get_cost_for_filter_not_active(ce):
    ce.get_cost_and_usage.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "User not enabled for cost explorer access"}},
        "GetCostAndUsage")
    with pytest.raises(CostExplorerNotActiveException):
        TeamCostExplorerClient(ce).get_cost_for_filter(project_filter("hpc-poc"), "2026-11-01", "2026-11-02")


def test_cached_cost_hits_cache(ce):
    costs._ce_cache.clear()
    ce.get_cost_and_usage.return_value = {"ResultsByTime": [_result("2026-11-01", "2026-11-02", 2)]}
    client = TeamCostExplorerClient(ce)
    costs.cached_cost(client, project_filter("x"), "2026-11-01", "2026-11-02", "MONTHLY")
    costs.cached_cost(client, project_filter("x"), "2026-11-01", "2026-11-02", "MONTHLY")
    assert ce.get_cost_and_usage.call_count == 1
    costs._ce_cache.clear()


def test_ce_end_is_clamped_to_tomorrow():
    assert ce_end("2026-12-01", today=date(2026, 11, 2)) == "2026-11-03"
    assert ce_end("2026-10-01", today=date(2026, 11, 2)) == "2026-10-01"


def test_allocate_by_estimated_cost_share():
    result = allocate({"teama": 6.0, "teamb": 4.0}, compute=10.0, shared=5.0)
    assert result["teams"]["teama"] == {"cost_share": 0.6, "compute_cost": 6.0, "shared_cost": 3.0}
    assert result["teams"]["teamb"] == {"cost_share": 0.4, "compute_cost": 4.0, "shared_cost": 2.0}
    assert result["unallocated"] == {"compute_cost": 0.0, "shared_cost": 0.0}


def test_allocate_without_usage_leaves_cost_unallocated():
    result = allocate({"teama": 0, "teamb": 0}, compute=10.0, shared=5.0)
    assert result["teams"]["teama"] == {"cost_share": 0.0, "compute_cost": None, "shared_cost": None}
    assert result["unallocated"] == {"compute_cost": 10.0, "shared_cost": 5.0}


def test_allocate_without_billed_cost():
    result = allocate({"teama": 5, "teamb": 4}, compute=None, shared=None)
    assert result["teams"]["teama"]["cost_share"] == pytest.approx(5 / 9, abs=1e-6)
    assert result["teams"]["teama"]["compute_cost"] is None


def test_estimate_is_machines_times_hours_times_price():
    jobs = parse_sacct_jobs(out.SACCT_JOBS.splitlines())
    result = estimate(jobs, type_of, PRICES, date(2026, 11, 1), date(2026, 12, 1))
    teama, teamb = result["teams"]["teama"], result["teams"]["teamb"]
    # alice: 1 x g6e.xlarge for 1 h; carol: 2 x g6e.2xlarge for 0.5 h
    assert teama["instance_hours"] == pytest.approx(2.0)
    assert teama["gpu_hours"] == pytest.approx(2.0)
    assert teama["estimate_cost"] == pytest.approx(1.861 + 2.24208, abs=1e-4)
    assert [t["instance_type"] for t in teama["by_type"]] == ["g6e.2xlarge", "g6e.xlarge"]
    assert teama["by_type"][0]["instance_hours"] == pytest.approx(1.0)
    assert teamb["estimate_cost"] == pytest.approx(2 * 10.49264, abs=1e-4)
    assert teamb["gpu_hours"] == pytest.approx(8.0)
    members = result["members"]
    assert members[("teama", "carol")]["estimate_cost"] == pytest.approx(2.24208, abs=1e-4)
    assert members[("teama", "alice")]["by_type"][0]["instance_type"] == "g6e.xlarge"
    assert "billing" not in repr(result)


def test_estimate_clips_to_window_and_handles_running_jobs():
    jobs = [
        {"account": "teama", "user": "a", "partition": "g6e-12xlarge", "start": "2026-10-31T23:00:00",
         "end": "2026-11-01T01:00:00", "alloc_tres": {"gres/gpu": 4, "node": 1}},
        {"account": "teamb", "user": "b", "partition": "g6e-xlarge", "start": "2026-11-01T10:00:00",
         "end": "Unknown", "alloc_tres": {"gres/gpu": 1, "node": 1}},
    ]
    result = estimate(jobs, type_of, PRICES, date(2026, 11, 1), date(2026, 11, 2), now=datetime(2026, 11, 1, 12, 0))
    assert result["teams"]["teama"]["instance_hours"] == pytest.approx(1.0)
    assert result["teams"]["teama"]["estimate_cost"] == pytest.approx(10.49264, abs=1e-4)
    assert result["teams"]["teamb"]["instance_hours"] == pytest.approx(2.0)


def test_estimate_counts_unknown_instance_types_as_unpriced():
    jobs = [{"account": "teama", "user": "a", "partition": "mixed", "start": "2026-11-01T00:00:00",
             "end": "2026-11-01T01:00:00", "alloc_tres": {"gres/gpu": 1, "node": 1}},
            {"account": "root", "user": "root", "partition": "g6e-xlarge", "start": "2026-11-01T00:00:00",
             "end": "2026-11-01T01:00:00", "alloc_tres": {"node": 1}}]
    result = estimate(jobs, type_of, PRICES, date(2026, 11, 1), date(2026, 11, 2))
    entry = result["teams"]["teama"]
    assert entry["unpriced_instance_hours"] == 1.0 and entry["estimate_cost"] == 0.0
    assert entry["by_type"][0]["instance_type"] == "unknown"
    assert "root" not in result["teams"]
