import json

import pytest

from api.teams import pricing


def _price_list(usd):
    return [json.dumps({
        "product": {"attributes": {"instanceType": "x"}},
        "terms": {"OnDemand": {"ABC.JRTCKXETXF": {"priceDimensions": {
            "ABC.JRTCKXETXF.6YS6EN2CT7": {"unit": "Hrs", "pricePerUnit": {"USD": usd}}}}}},
    })]


REAL_INSTANCE_INFO, REAL_INSTANCE_PRICES = pricing.instance_info, pricing.instance_prices


@pytest.fixture(autouse=True)
def clear_caches(mocker):
    # conftest replaces these for every other test; here the real functions are under test
    mocker.patch.object(pricing, "instance_info", REAL_INSTANCE_INFO)
    mocker.patch.object(pricing, "instance_prices", REAL_INSTANCE_PRICES)
    pricing._price_cache.clear()
    pricing._info_cache.clear()
    yield
    pricing._price_cache.clear()
    pricing._info_cache.clear()


@pytest.fixture
def clients(mocker):
    price_client, ec2_client = mocker.Mock(), mocker.Mock()

    def _client(service, **kwargs):
        return {"pricing": price_client, "ec2": ec2_client}[service]

    mocker.patch("api.teams.pricing.boto3.client", side_effect=_client)
    return price_client, ec2_client


def test_parse_price_list():
    assert pricing.parse_price_list(_price_list("4.6016000000")) == 4.6016
    assert pricing.parse_price_list(_price_list("0.0000000000")) is None
    assert pricing.parse_price_list([]) is None


def test_ondemand_price_uses_upstream_filters_and_caches(clients):
    price_client, _ = clients
    price_client.get_products.return_value = {"PriceList": _price_list("0.8048000000")}
    assert pricing.ondemand_price("g6.xlarge", "us-west-2") == 0.8048
    assert pricing.ondemand_price("g6.xlarge", "us-west-2") == 0.8048
    price_client.get_products.assert_called_once()
    kwargs = price_client.get_products.call_args.kwargs
    assert kwargs["ServiceCode"] == "AmazonEC2"
    fields = {f["Field"]: f["Value"] for f in kwargs["Filters"]}
    assert fields == {"tenancy": "shared", "instanceType": "g6.xlarge", "operatingSystem": "Linux",
                      "regionCode": "us-west-2", "preInstalledSw": "NA", "capacityStatus": "Used"}


def test_ondemand_price_rejects_bad_instance_type(clients):
    with pytest.raises(ValueError):
        pricing.ondemand_price("g6.xlarge;id", "us-west-2")


def test_instance_info_and_prices(clients):
    price_client, ec2_client = clients
    ec2_client.describe_instance_types.return_value = {"InstanceTypes": [
        {"InstanceType": "g6e.12xlarge", "GpuInfo": {"Gpus": [{"Count": 4, "Name": "L40S", "Manufacturer": "NVIDIA",
                                                               "MemoryInfo": {"SizeInMiB": 45776}}]},
         "VCpuInfo": {"DefaultVCpus": 48}, "MemoryInfo": {"SizeInMiB": 393216}},
        {"InstanceType": "m7i.xlarge", "VCpuInfo": {"DefaultVCpus": 4}, "MemoryInfo": {"SizeInMiB": 16384}},
    ]}
    info = pricing.instance_info({"g6e.12xlarge", "m7i.xlarge"}, "us-west-2")
    assert info == {"g6e.12xlarge": {"gpus": 4, "vcpus": 48, "memory_gib": 384.0, "gpu_model": "NVIDIA L40S",
                                     "gpu_memory_gib": 44.7, "gpu_memory_gb": 48},
                    "m7i.xlarge": {"gpus": 0, "vcpus": 4, "memory_gib": 16.0, "gpu_model": None,
                                   "gpu_memory_gib": None, "gpu_memory_gb": None}}
    assert pricing.gpu_count(["g6e.12xlarge"], "us-west-2") == {"g6e.12xlarge": 4}
    ec2_client.describe_instance_types.assert_called_once_with(InstanceTypes=["g6e.12xlarge", "m7i.xlarge"])
    prices = {"g6e.12xlarge": "10.4926400000", "m7i.xlarge": "0.2016"}
    price_client.get_products.side_effect = lambda **kw: {
        "PriceList": _price_list(prices[next(f["Value"] for f in kw["Filters"] if f["Field"] == "instanceType")])}
    assert pricing.instance_prices({"g6e.12xlarge", "m7i.xlarge"}, "us-west-2") == {"g6e.12xlarge": 10.49264,
                                                                                   "m7i.xlarge": 0.2016}


def test_instance_prices_skip_failures(clients, mocker):
    mocker.patch("api.teams.pricing.logger", new=mocker.Mock())
    price_client, _ = clients
    price_client.get_products.side_effect = Exception("throttled")
    assert pricing.instance_prices({"g6e.xlarge"}, "us-west-2") == {}


def test_instance_prices_empty(clients):
    assert pricing.instance_prices(set(), "us-west-2") == {}


def test_gpu_model_and_memory_from_describe_instance_types():
    item = {"GpuInfo": {"Gpus": [{"Count": 1, "Name": "L4", "Manufacturer": "NVIDIA",
                                  "MemoryInfo": {"SizeInMiB": 22888}}]},
            "VCpuInfo": {"DefaultVCpus": 4}, "MemoryInfo": {"SizeInMiB": 16384}}
    assert pricing._info(item) == {"gpus": 1, "vcpus": 4, "memory_gib": 16.0, "gpu_model": "NVIDIA L4",
                                   "gpu_memory_gib": 22.4, "gpu_memory_gb": 24}
    # a name that already carries the manufacturer is not repeated; no memory info -> None
    named = {"GpuInfo": {"Gpus": [{"Count": 8, "Name": "NVIDIA H100", "Manufacturer": "NVIDIA"}]}}
    assert pricing._info(named)["gpu_model"] == "NVIDIA H100" and pricing._info(named)["gpu_memory_gib"] is None
