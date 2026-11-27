# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Instance-type catalog of a cluster: the queues whose machines per-type team quotas can count.

A queue is *managed* when it has exactly one compute resource with exactly one GPU instance type: then the
queue (= Slurm partition) and the instance type are the same thing for quotas, submit and cost. Every other
queue is returned under ``unmanaged_queues`` with a reason:

* ``multi_type``: several compute resources or instance types (per-type quotas need one type per queue);
* ``no_gpu``: a CPU queue such as ``build`` (environment builds);
* ``duplicate_type``: the instance type already has a managed queue;
* ``unknown_type``: the instance type could not be described.

The cluster configuration is cached for 5 minutes (``cluster``), sizes and prices for 24 hours (``pricing``).
"""
from api.pcm_globals import logger
from api.teams import cluster, pricing
from api.teams.validation import INSTANCE_TYPE_RE, PARTITION_RE

MULTI_TYPE, NO_GPU, DUPLICATE_TYPE, UNKNOWN_TYPE, INVALID_NAME = (
    "multi_type", "no_gpu", "duplicate_type", "unknown_type", "invalid_name")


def _queues(mapping):
    queues = {}
    for (queue, resource), types in mapping.items():
        if not queue:
            continue
        entry = queues.setdefault(queue, {"resources": set(), "types": set()})
        entry["resources"].add(resource)
        entry["types"].update(types or [])
    return queues


def single_types(mapping):
    """Instance types of queues with one compute resource and one instance type."""
    result = set()
    for entry in _queues(mapping).values():
        if len(entry["resources"]) == 1 and len(entry["types"]) == 1:
            itype = next(iter(entry["types"]))
            if INSTANCE_TYPE_RE.fullmatch(itype or ""):
                result.add(itype)
    return result


def build_catalog(mapping, info, prices):
    """Pure catalog builder. ``mapping`` = {(queue, compute_resource): [types]}, ``info`` = {type: {gpus, vcpus,
    memory_gib, gpu_model, gpu_memory_gib, gpu_memory_gb}}, ``prices`` = {type: USD per hour}."""
    managed, unmanaged, seen = [], [], set()
    queues = _queues(mapping)
    for queue in sorted(queues):
        entry = queues[queue]
        types = sorted(entry["types"])
        if PARTITION_RE.fullmatch(queue) is None:
            unmanaged.append({"queue": queue, "reason": INVALID_NAME, "instance_types": types})
            continue
        if len(entry["resources"]) != 1 or len(types) != 1:
            unmanaged.append({"queue": queue, "reason": MULTI_TYPE, "instance_types": types})
            continue
        itype = types[0]
        details = info.get(itype) if INSTANCE_TYPE_RE.fullmatch(itype or "") else None
        if details is None:
            unmanaged.append({"queue": queue, "reason": UNKNOWN_TYPE, "instance_types": types})
            continue
        if not details.get("gpus"):
            unmanaged.append({"queue": queue, "reason": NO_GPU, "instance_types": types})
            continue
        if itype in seen:
            unmanaged.append({"queue": queue, "reason": DUPLICATE_TYPE, "instance_types": types})
            continue
        seen.add(itype)
        managed.append({
            "instance_type": itype,
            "partition": queue,
            "gpus": details["gpus"],
            "vcpus": details.get("vcpus"),
            "memory_gib": details.get("memory_gib"),
            "gpu_model": details.get("gpu_model"),
            "gpu_memory_gib": details.get("gpu_memory_gib"),
            "gpu_memory_gb": details.get("gpu_memory_gb"),
            "price_per_hour": prices.get(itype),
        })
    managed.sort(key=lambda t: (t["instance_type"].split(".")[0], t["vcpus"] or 0, t["instance_type"]))
    return {"types": managed, "unmanaged_queues": unmanaged}


def instance_catalog(cluster_name, region):
    mapping = cluster.compute_resource_instance_types(cluster_name, region)
    types = single_types(mapping)
    info = pricing.instance_info(types, region) if types else {}
    gpu_types = [t for t, i in info.items() if i.get("gpus")]
    try:
        prices = pricing.instance_prices(gpu_types, region) if gpu_types else {}
    except Exception as err:  # prices are informative; the catalog works without them
        logger.warning(f"Instance prices unavailable: {err}")
        prices = {}
    return build_catalog(mapping, info, prices)


def by_type(catalog):
    return {entry["instance_type"]: entry for entry in catalog["types"]}


def by_partition(catalog):
    return {entry["partition"]: entry for entry in catalog["types"]}


def resolve_type_limits(catalog, type_limits):
    """{instance_type: n} -> {partition: n}. Unknown or unmanaged instance types raise ValueError (400)."""
    types = by_type(catalog)
    unknown = sorted(set(type_limits or {}) - set(types))
    if unknown:
        raise ValueError(
            f"机型 {', '.join(unknown)} 在此集群上没有只含这一种机型的队列，不能设置配额")
    return {types[itype]["partition"]: value for itype, value in (type_limits or {}).items()}
