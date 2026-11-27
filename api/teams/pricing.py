# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance
# with the License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.
"""On-demand instance prices (AWS Pricing API) and instance sizes (DescribeInstanceTypes), cached for 24 hours."""
import json

import boto3
import botocore

from api.pcm_globals import logger
from api.teams.cache import TTLCache
from api.teams.validation import validate_instance_type

PRICE_CACHE_TTL = 24 * 60 * 60
PRICING_REGION = "us-east-1"  # the Pricing API endpoint is only available there

_price_cache = TTLCache(PRICE_CACHE_TTL)
_info_cache = TTLCache(PRICE_CACHE_TTL)


def pricing_filters(instance_type, region):
    # Same filters as PclusterApiHandler._price_estimate
    return [
        {"Field": "tenancy", "Value": "shared", "Type": "TERM_MATCH"},
        {"Field": "instanceType", "Value": instance_type, "Type": "TERM_MATCH"},
        {"Field": "operatingSystem", "Value": "Linux", "Type": "TERM_MATCH"},
        {"Field": "regionCode", "Value": region, "Type": "TERM_MATCH"},
        {"Field": "preInstalledSw", "Value": "NA", "Type": "TERM_MATCH"},
        {"Field": "capacityStatus", "Value": "Used", "Type": "TERM_MATCH"},
    ]


def parse_price_list(price_list):
    for product in map(json.loads, price_list or []):
        for term in (product.get("terms", {}).get("OnDemand") or {}).values():
            for dimension in (term.get("priceDimensions") or {}).values():
                try:
                    price = float(dimension["pricePerUnit"]["USD"])
                except (KeyError, TypeError, ValueError):
                    continue
                if price == price and price > 0:  # skip NaN and zero-priced dimensions
                    return price
    return None


def _fetch_price(instance_type, region):
    client = boto3.client("pricing", region_name=PRICING_REGION)
    response = client.get_products(ServiceCode="AmazonEC2", Filters=pricing_filters(instance_type, region))
    return parse_price_list(response.get("PriceList"))


def ondemand_price(instance_type, region):
    validate_instance_type(instance_type)
    return _price_cache.get_or_compute((instance_type, region), lambda: _fetch_price(instance_type, region))


def _gpu_model(gpu):
    """``NVIDIA L40S`` from the manufacturer and name of a ``GpuInfo.Gpus`` entry, or None."""
    name = (gpu.get("Name") or "").strip()
    maker = (gpu.get("Manufacturer") or "").strip()
    if not name:
        return None
    return name if not maker or name.lower().startswith(maker.lower()) else f"{maker} {name}"


def _info(item):
    gpu_list = (item.get("GpuInfo") or {}).get("Gpus", [])
    gpus = sum(gpu.get("Count", 0) for gpu in gpu_list)
    first = gpu_list[0] if gpu_list else {}
    gpu_mib = (first.get("MemoryInfo") or {}).get("SizeInMiB") or 0
    return {
        "gpus": gpus,
        "vcpus": (item.get("VCpuInfo") or {}).get("DefaultVCpus"),
        "memory_gib": round((item.get("MemoryInfo") or {}).get("SizeInMiB", 0) / 1024, 1) or None,
        "gpu_model": _gpu_model(first) if first else None,
        # memory per GPU: GiB as reported (L40S 44.7) and decimal GB as on the vendor data sheet (L40S 48)
        "gpu_memory_gib": round(gpu_mib / 1024, 1) if gpu_mib else None,
        "gpu_memory_gb": round(gpu_mib * 1024 * 1024 / 1e9) if gpu_mib else None,
    }


def instance_info(instance_types, region):
    """Return {instance_type: {gpus, vcpus, memory_gib, gpu_model, gpu_memory_gib, gpu_memory_gb}} using one
    DescribeInstanceTypes
    call for the uncached types."""
    result, missing = {}, []
    for instance_type in sorted(set(instance_types)):
        validate_instance_type(instance_type)
        cached = _info_cache.get((instance_type, region))
        if cached is None:
            missing.append(instance_type)
        else:
            result[instance_type] = cached
    if missing:
        ec2 = boto3.client("ec2", config=botocore.config.Config(region_name=region)) if region else boto3.client("ec2")
        response = ec2.describe_instance_types(InstanceTypes=missing)
        for item in response.get("InstanceTypes", []):
            result[item["InstanceType"]] = _info_cache.set((item["InstanceType"], region), _info(item))
    return result


def gpu_count(instance_types, region):
    """Return {instance_type: number of GPUs}."""
    return {name: info["gpus"] for name, info in instance_info(instance_types, region).items()}


def instance_prices(instance_types, region):
    """Return {instance_type: on-demand USD per instance-hour}. Types without a price are omitted."""
    prices = {}
    for instance_type in sorted(set(instance_types)):
        try:
            price = ondemand_price(instance_type, region)
        except Exception as err:
            logger.warning(f"Unable to get on-demand price for {instance_type}: {err}")
            continue
        if price:
            prices[instance_type] = round(price, 6)
    return prices
