# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""Cluster lookups through the ParallelCluster API.

The head node instance id is always resolved server-side from the cluster
name; a client-supplied instance id is never used.
"""
import re
from urllib.parse import quote

import yaml
from flask import abort, request

from api.PclusterApiHandler import (
    _get_instance_types_for_compute_resource,
    get_base_url,
    get_cluster_config_text,
    sigv4_request,
)
from api.teams.cache import TTLCache
from api.validation.validators import is_alphanumeric_with_hyphen

CLUSTER_CACHE_TTL = 300
MAX_LIST_PAGES = 10

_head_node_cache = TTLCache(CLUSTER_CACHE_TTL)
_config_cache = TTLCache(CLUSTER_CACHE_TTL)

NODE_INDEX_RE = r"(?:\d+|\[[0-9,\-]+\])"


def _check_cluster_name(cluster_name):
    if not isinstance(cluster_name, str) or len(cluster_name) > 60 or not is_alphanumeric_with_hyphen(cluster_name):
        raise ValueError("Invalid cluster name")


def _describe_cluster(cluster_name, region):
    _check_cluster_name(cluster_name)
    params = {"region": region} if region else {}
    resp = sigv4_request("GET", get_base_url(request), f"/v3/clusters/{cluster_name}", params=params)
    if resp.status_code == 404:
        abort(404)
    if resp.status_code != 200:
        abort(resp.status_code if 400 <= resp.status_code < 600 else 502)
    return resp.json()


def _resolve_head_node(cluster_name, region):
    info = _describe_cluster(cluster_name, region)
    scheduler = (info.get("scheduler") or {}).get("type")
    if scheduler != "slurm":
        raise ValueError(f"Cluster '{cluster_name}' does not use the Slurm scheduler")
    instance_id = (info.get("headNode") or {}).get("instanceId")
    if not instance_id or not re.fullmatch(r"i-[0-9a-f]{8,17}", instance_id):
        raise ValueError(f"Cluster '{cluster_name}' has no running head node")
    return instance_id


def head_node(cluster_name, region):
    return _head_node_cache.get_or_compute((cluster_name, region), lambda: _resolve_head_node(cluster_name, region))


def parse_compute_resources(config_text):
    """Return {(queue, compute_resource): [instance types]} from a cluster configuration YAML."""
    config = yaml.safe_load(config_text) or {}
    mapping = {}
    for queue in (config.get("Scheduling") or {}).get("SlurmQueues") or []:
        for compute_resource in queue.get("ComputeResources") or []:
            try:
                instance_types = _get_instance_types_for_compute_resource(compute_resource)
            except Exception:
                instance_types = []
            mapping[(queue.get("Name"), compute_resource.get("Name"))] = instance_types
    return mapping


def compute_resource_instance_types(cluster_name, region):
    _check_cluster_name(cluster_name)
    return _config_cache.get_or_compute(
        (cluster_name, region), lambda: parse_compute_resources(get_cluster_config_text(cluster_name, region))
    )


def split_nodelist(nodelist):
    """Split a Slurm hostlist on top-level commas: ``a-[1,3],b-2`` -> ``['a-[1,3]', 'b-2']``."""
    parts, depth, current = [], 0, ""
    for char in nodelist or "":
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current:
        parts.append(current)
    return parts


def compute_resource_for_node(partition, node):
    match = re.fullmatch(rf"{re.escape(partition)}-(?:st|dy)-(.+)-{NODE_INDEX_RE}", node or "")
    return match.group(1) if match else None


def instance_type_for_job(partition, nodelist, mapping):
    """Map a job's partition and NodeList to a single instance type, or None when it is ambiguous or unknown."""
    if not partition or "," in partition:
        return None
    resources = {compute_resource_for_node(partition, node) for node in split_nodelist(nodelist)}
    if not resources or None in resources or len(resources) != 1:
        return None
    instance_types = mapping.get((partition, resources.pop())) or []
    return instance_types[0] if len(instance_types) == 1 else None


def list_slurm_clusters(region):
    clusters, params = [], {"region": region} if region else {}
    for _ in range(MAX_LIST_PAGES):
        resp = sigv4_request("GET", get_base_url(request), "/v3/clusters", params=params)
        if resp.status_code != 200:
            abort(resp.status_code if 400 <= resp.status_code < 600 else 502)
        body = resp.json()
        for item in body.get("clusters", []):
            if (item.get("scheduler") or {}).get("type") == "slurm":
                clusters.append({"name": item.get("clusterName"), "region": item.get("region") or region})
        token = body.get("nextToken")
        if not token:
            break
        params = {**params, "nextToken": quote(token, safe="")}
    return sorted(clusters, key=lambda c: c["name"] or "")
