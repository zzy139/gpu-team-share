"""Instance-type catalog (R1/R2), the portal data store helpers and the notification helpers."""
import pytest

from api.teams import catalog, datastore, notify
from api.teams.cluster import parse_compute_resources

from . import slurm_outputs as out
from .conftest import INSTANCE_INFO, PRICES


def _catalog(config_text):
    return catalog.build_catalog(parse_compute_resources(config_text), INSTANCE_INFO, PRICES)


def test_one_type_queues_are_managed_and_carry_sizes():
    result = _catalog(out.CLUSTER_CONFIG)
    assert [t["instance_type"] for t in result["types"]] == ["g6e.xlarge", "g6e.2xlarge", "g6e.12xlarge"]
    assert result["types"][2] == {"instance_type": "g6e.12xlarge", "partition": "g6e-12xlarge", "gpus": 4,
                                  "vcpus": 48, "memory_gib": 384.0, "gpu_model": "NVIDIA L40S",
                                  "gpu_memory_gib": 44.7, "gpu_memory_gb": 48, "price_per_hour": 10.49264}


def test_multi_type_queue_is_unmanaged():
    result = _catalog(out.LEGACY_CLUSTER_CONFIG)
    assert result["types"] == []
    reasons = {q["queue"]: (q["reason"], q["instance_types"]) for q in result["unmanaged_queues"]}
    assert reasons == {"g6": ("multi_type", ["g6.12xlarge", "g6.xlarge"]),
                       "g6e": ("multi_type", ["g6e.12xlarge", "g6e.xlarge"]),
                       "mixed": ("multi_type", ["g6.xlarge", "g6e.xlarge"])}


def test_cpu_duplicate_and_unknown_queues_are_unmanaged():
    mapping = {("build", "build"): ["m7i.xlarge"], ("a", "a"): ["g6e.xlarge"], ("b", "b"): ["g6e.xlarge"],
               ("c", "c"): ["x9.metal"], ("Bad_Name", "x"): ["g6e.xlarge"]}
    result = catalog.build_catalog(mapping, INSTANCE_INFO, {})
    assert [(t["partition"], t["price_per_hour"]) for t in result["types"]] == [("a", None)]
    assert {q["queue"]: q["reason"] for q in result["unmanaged_queues"]} == {
        "build": "no_gpu", "b": "duplicate_type", "c": "unknown_type", "Bad_Name": "invalid_name"}


def test_resolve_type_limits():
    result = _catalog(out.CLUSTER_CONFIG)
    assert catalog.resolve_type_limits(result, {"g6e.xlarge": 2, "g6e.12xlarge": 0}) == \
        {"g6e-xlarge": 2, "g6e-12xlarge": 0}
    with pytest.raises(ValueError) as err:
        catalog.resolve_type_limits(result, {"g6.xlarge": 1, "p5.48xlarge": 1})
    assert "g6.xlarge, p5.48xlarge" in str(err.value)


def test_instance_catalog_survives_missing_prices(mocker):
    mocker.patch("api.teams.catalog.cluster.compute_resource_instance_types",
                 return_value=parse_compute_resources(out.CLUSTER_CONFIG))
    mocker.patch("api.teams.catalog.pricing.instance_prices", side_effect=Exception("throttled"))
    mocker.patch("api.teams.catalog.logger", new=mocker.Mock())
    result = catalog.instance_catalog("hpc-poc", "us-west-2")
    assert len(result["types"]) == 3 and all(t["price_per_hour"] is None for t in result["types"])


def test_datastore_round_trip_and_conditional_put(dynamo):
    store = datastore.Store({"data_table": "hpc-poc-pcui-data", "region": "us-west-2"})
    item = {"pk": "TEAM#teama", "sk": "BUDGET", "budget_usd": 12.5, "nested": {"a": [1, 2.5, True]}, "none": None}
    store.put(item)
    assert store.get("TEAM#teama", "BUDGET") == {"pk": "TEAM#teama", "sk": "BUDGET", "budget_usd": 12.5,
                                                 "nested": {"a": [1, 2.5, True]}}
    assert store.put({"pk": "TEAM#teama", "sk": "BUDGET"}, if_absent=True) is False
    assert store.put({"pk": "TEAM#teama", "sk": "OTHER"}, if_absent=True) is True
    assert [i["sk"] for i in store.query("TEAM#teama", prefix="B")] == ["BUDGET"]
    assert [i["sk"] for i in store.query("TEAM#teama", newest_first=True, limit=1)] == ["OTHER"]
    assert len(store.batch_get([("TEAM#teama", "BUDGET"), ("TEAM#teama", "BUDGET"), ("TEAM#x", "y")])) == 1
    store.delete("TEAM#teama", "BUDGET")
    assert store.get("TEAM#teama", "BUDGET") is None
    assert "scan" not in dynamo.calls


def test_datastore_requires_a_table():
    with pytest.raises(datastore.DataStoreUnavailable):
        datastore.Store({"region": "us-west-2"})


@pytest.mark.parametrize("percent,level", [(None, None), (79.9, None), (80, "warning"), (99.9, "warning"),
                                           (100, "exceeded"), (250, "exceeded")])
def test_budget_levels(percent, level):
    assert notify.budget_level(percent) == level


def test_filter_policy_is_per_user():
    assert notify.filter_policy("carol") == '{"user":["carol"]}'
