# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Team cost allocation.

* estimate: per job, machines x hours (sacct) x the on-demand price of the queue's instance type. GPU queues
  allocate whole machines, so this is exact for compute; it is grouped by team, instance type and member.
* billed: Cost Explorer cost of the project (tag ``Project``), split into the
  compute part (tag ``parallelcluster:node-type=Compute``) and the shared rest
  (head/login node, EFS, RDS, Simple AD, NAT ...). Both are allocated to teams
  by their share of the estimated cost. Cost Explorer lags about 24 hours.
"""
from datetime import datetime, timedelta, timezone

from api.costmonitoring.costexplorer_client import (
    COST_DATA_FILTER_METRIC,
    CostExplorerClient,
    clienterror_handled,
)
from api.teams.cache import TTLCache
from api.teams.slurm import NON_TEAM_ACCOUNTS, gpus_in_tres

CE_REGION = "us-east-1"
CE_CACHE_TTL = 60 * 60
PROJECT_TAG = "Project"
NODE_TYPE_TAG = "parallelcluster:node-type"
COMPUTE_NODE_TYPE = "Compute"
TEAM_COST_TAGS = [PROJECT_TAG, NODE_TYPE_TAG]
MAX_PAGES = 20

_ce_cache = TTLCache(CE_CACHE_TTL)


def project_filter(project):
    return {"Tags": {"Key": PROJECT_TAG, "Values": [project], "MatchOptions": ["EQUALS"]}}


def compute_filter(project):
    return {"And": [
        project_filter(project),
        {"Tags": {"Key": NODE_TYPE_TAG, "Values": [COMPUTE_NODE_TYPE], "MatchOptions": ["EQUALS"]}},
    ]}


class TeamCostExplorerClient(CostExplorerClient):
    def __init__(self, client):
        super().__init__(client, cost_allocation_tags=TEAM_COST_TAGS)

    @clienterror_handled
    def _fetch_page(self, cost_filter, start, end, granularity, metric, next_token=None):
        kwargs = dict(
            TimePeriod={"Start": start, "End": end},
            Granularity=granularity,
            Filter=cost_filter,
            Metrics=[metric],
        )
        if next_token:
            kwargs["NextPageToken"] = next_token
        response = self.client.get_cost_and_usage(**kwargs)
        return response.get("ResultsByTime", []), response.get("NextPageToken")

    def get_cost_for_filter(self, cost_filter, start, end, granularity="MONTHLY", metric=COST_DATA_FILTER_METRIC):
        """Return [{period: {start, end}, amount, unit}] sorted by period. ``end`` is exclusive."""
        results, token = self._fetch_page(cost_filter, start, end, granularity, metric)
        pages = 1
        while token and pages < MAX_PAGES:
            more, token = self._fetch_page(cost_filter, start, end, granularity, metric, token)
            results.extend(more)
            pages += 1
        results = [r for r in results if metric in (r.get("Total") or {})]
        return sorted(CostExplorerClient.map_cost_values(results, metric), key=lambda c: c["period"]["start"])


def cached_cost(client, cost_filter, start, end, granularity):
    key = (repr(cost_filter), start, end, granularity)
    return _ce_cache.get_or_compute(key, lambda: client.get_cost_for_filter(cost_filter, start, end, granularity))


def sum_amounts(costs):
    return round(sum(c["amount"] for c in costs), 6)


def ce_end(end, today=None):
    """Cost Explorer rejects end dates far in the future; clamp to tomorrow (end is exclusive)."""
    tomorrow = ((today or datetime.now(timezone.utc).replace(tzinfo=None).date()) + timedelta(days=1)).isoformat()
    return min(end, tomorrow)


def allocate(team_estimates, compute, shared):
    """Split compute and shared cost by each team's share of the estimated cost.

    ``team_estimates`` is {team: estimated USD}. Returns {"teams": {team: {cost_share, compute_cost, shared_cost}},
    "unallocated": {compute_cost, shared_cost}}. Costs are None when the billed figure is unknown. With no estimated
    usage the whole cost stays unallocated.
    """
    total = sum(max(v or 0, 0) for v in team_estimates.values())
    teams = {}
    for team, value in team_estimates.items():
        share = (max(value or 0, 0) / total) if total > 0 else 0.0
        teams[team] = {
            "cost_share": round(share, 6),
            "compute_cost": None if compute is None or total <= 0 else round(compute * share, 4),
            "shared_cost": None if shared is None or total <= 0 else round(shared * share, 4),
        }
    unallocated = {
        "compute_cost": compute if total <= 0 else 0.0,
        "shared_cost": shared if total <= 0 else 0.0,
    }
    return {"teams": teams, "unallocated": unallocated}


def parse_slurm_time(value):
    if not value or value in ("Unknown", "None"):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _bucket():
    return {"instance_hours": 0.0, "gpu_hours": 0.0, "estimate_cost": 0.0, "unpriced_instance_hours": 0.0}


def _add(bucket, hours, gpu_hours, price):
    bucket["instance_hours"] += hours
    bucket["gpu_hours"] += gpu_hours
    if price is None:
        bucket["unpriced_instance_hours"] += hours
    else:
        bucket["estimate_cost"] += hours * price


def _round(bucket):
    for key in ("instance_hours", "gpu_hours", "unpriced_instance_hours"):
        bucket[key] = round(bucket[key], 4)
    bucket["estimate_cost"] = round(bucket["estimate_cost"], 4)
    return bucket


def _finish(entry):
    _round(entry)
    entry["by_type"] = [{"instance_type": itype, **_round(values)} for itype, values in sorted(entry["by_type"].items())]
    return entry


def estimate(jobs, type_of, prices, start, end, now=None):
    """Estimated cost of jobs clipped to [start, end): machines x hours x on-demand price of the instance type.

    GPU queues allocate whole machines (JobExclusiveAllocation), so this is the real compute use. ``type_of(job)``
    returns the instance type of a job or None, ``prices`` is {instance_type: USD per instance-hour}. Slurm times
    are the head node's local time (UTC on ParallelCluster).

    Returns ``{"teams": {team: entry}, "members": {(team, user): entry}}`` where ``entry`` is
    ``{instance_hours, gpu_hours, estimate_cost, unpriced_instance_hours, by_type: [{instance_type, ...}]}``.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    window_start = datetime.combine(start, datetime.min.time())
    window_end = datetime.combine(end, datetime.min.time())
    teams, members = {}, {}
    for job in jobs:
        account = job.get("account")
        job_start = parse_slurm_time(job.get("start"))
        if not account or account in NON_TEAM_ACCOUNTS or job_start is None:
            continue
        job_end = parse_slurm_time(job.get("end")) or now
        seconds = (min(job_end, window_end) - max(job_start, window_start)).total_seconds()
        if seconds <= 0:
            continue
        tres = job.get("alloc_tres") or {}
        nodes = tres.get("node") if isinstance(tres.get("node"), int) and tres.get("node") > 0 else 1
        hours = nodes * seconds / 3600
        gpu_hours = gpus_in_tres(tres) * seconds / 3600
        itype = type_of(job)
        price = prices.get(itype) if itype else None
        for key, store in ((account, teams), ((account, job.get("user") or ""), members)):
            entry = store.setdefault(key, {**_bucket(), "by_type": {}})
            _add(entry, hours, gpu_hours, price)
            _add(entry["by_type"].setdefault(itype or "unknown", _bucket()), hours, gpu_hours, price)
    return {"teams": {k: _finish(v) for k, v in teams.items()},
            "members": {k: _finish(v) for k, v in members.items()}}


def empty_estimate():
    return {**_bucket(), "by_type": []}
