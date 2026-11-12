import logging
import math
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _utc(value):
    """Chuẩn hóa datetime hoặc ISO 8601 có múi giờ sang UTC."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Thời gian phải có múi giờ")
    return value.astimezone(timezone.utc)


def find_idle_instances(ec2, cloudwatch, now, cpu_threshold, days):
    
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("days phải là số nguyên từ 1 đến 365")
    if (isinstance(cpu_threshold, bool) or not isinstance(cpu_threshold, (int, float))
            or not math.isfinite(cpu_threshold) or not 0 < cpu_threshold < 100):
        raise ValueError("cpu_threshold phải nằm trong khoảng (0, 100)")
    now = _utc(now)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    expected = {start + timedelta(days=i) for i in range(days)}

    # Tự quét inventory để giữ đúng chữ ký đã chốt trong tài liệu Giai đoạn 2.
    eligible = {}
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
    for page in pages:
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                if instance.get("State", {}).get("Name") != "running":
                    continue
                resource = instance["InstanceId"]
                try:
                    launch = _utc(instance["LaunchTime"])
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("Không kiểm tra CPU %s: LaunchTime lỗi (%s)", resource, exc)
                    continue
                if launch <= start:
                    eligible[resource] = instance

    candidates = []
    records = list(eligible.items())
    for offset in range(0, len(records), 500):
        batch = records[offset:offset + 500]
        queries = [{"Id": f"cpu{i}", "MetricStat": {
            "Metric": {"Namespace": "AWS/EC2", "MetricName": "CPUUtilization",
                       "Dimensions": [{"Name": "InstanceId", "Value": resource}]},
            "Period": 86400, "Stat": "Average", "Unit": "Percent"}, "ReturnData": True}
            for i, (resource, _) in enumerate(batch)]
        request = {"MetricDataQueries": queries, "StartTime": start,
                   "EndTime": end, "ScanBy": "TimestampAscending"}
        samples = {q["Id"]: {} for q in queries}
        statuses, invalid, tokens = {}, set(), set()
        while True:
            response = cloudwatch.get_metric_data(**request)
            for metric in response.get("MetricDataResults", []):
                key = metric.get("Id")
                if key not in samples:
                    continue
                statuses[key] = metric.get("StatusCode")
                if statuses[key] in {"Forbidden", "InternalError"}:
                    raise RuntimeError(f"CloudWatch {key}: {statuses[key]}")
                stamps, values = metric.get("Timestamps", []), metric.get("Values", [])
                if len(stamps) != len(values):
                    invalid.add(key)
                for stamp, value in zip(stamps, values):
                    stamp = _utc(stamp)
                    if stamp not in expected:
                        continue
                    if (isinstance(value, bool) or not isinstance(value, (float, int))
                            or not math.isfinite(value) or not 0 <= value <= 100):
                        invalid.add(key)
                        continue
                    if stamp in samples[key] and samples[key][stamp] != value:
                        invalid.add(key)
                    samples[key][stamp] = value
            token = response.get("NextToken")
            if not token:
                break
            if token in tokens:
                raise RuntimeError("CloudWatch trả NextToken lặp lại")
            tokens.add(token)
            request["NextToken"] = token

        for i, (resource, instance) in enumerate(batch):
            key = f"cpu{i}"
            if key in invalid or statuses.get(key) != "Complete" or set(samples[key]) != expected:
                logger.warning("Chưa kết luận CPU %s: %s/%s ngày, status=%s",
                               resource, len(samples[key]), days, statuses.get(key, "Missing"))
                continue
            daily = list(samples[key].values())
            if all(cpu < cpu_threshold for cpu in daily):
                candidates.append({"instance_id": resource,
                                   "instance_type": instance["InstanceType"],
                                   "avg_cpu": sum(daily) / days,
                                   "tags": instance.get("Tags", [])})
    return candidates
