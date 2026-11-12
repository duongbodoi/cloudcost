import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from notify import bullet, field_lines, fmt_time, send

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)


def utc_time(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Time must include a timezone")
    return stamp.astimezone(timezone.utc)


def duration(value):
    match = re.fullmatch(r"\s*(\d+)\s*([mhd])\s*", value, re.IGNORECASE)
    if not match or int(match[1]) <= 0:
        raise ValueError("TTL must be a positive integer followed by m, h or d")
    return timedelta(seconds=int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2].lower()])


def validate(event):
    if not isinstance(event, dict) or event.get("job") != "ttl-check":
        raise ValueError("Expected job=ttl-check")
    if type(event.get("dry_run", False)) is not bool:
        raise ValueError("dry_run must be a JSON boolean")


class Worker:
    def __init__(self, event, account, context=None):
        validate(event)
        self.now = utc_time(event["now"]) if "now" in event else datetime.now(timezone.utc)
        self.dry = event.get("dry_run", False)
        self.account, self.context = account, context
        self.result = {"dry_run": self.dry, "now": self.now.isoformat(),
                       "regions_scanned": [], "stop_requested": [], "would_stop": [],
                       "initialized": [], "would_initialize": [], "cleaned": [],
                       "would_clean": [], "skipped": [], "errors": []}

    def check_time(self):
        if self.context and self.context.get_remaining_time_in_millis() < 20000:
            raise TimeoutError("Scan incomplete: less than 20 seconds remain")

    def item(self, kind, resource, region, **extra):
        return {"type": kind, "id": resource, "region": region, **extra}

    def error(self, resource, region, exc):
        self.result["errors"].append({"resource": resource, "region": region, "error": str(exc)})
        LOG.error("%s/%s: %s", region, resource, exc)

    def skip(self, kind, resource, region, reason):
        item = self.item(kind, resource, region, reason=reason)
        self.result["skipped"].append(item)
        LOG.info("Skipped: %s", json.dumps(item))

    def pages(self, client, operation):
        for page in client.get_paginator(operation).paginate():
            self.check_time()
            yield page

    def stop(self, kind, resource, region, operation):
        item = self.item(kind, resource, region)
        if self.dry:
            self.result["would_stop"].append(item)
        else:
            operation()
            self.result["stop_requested"].append(item)

    def clear_clock(self, rds, arn, kind, resource, region):
        item = self.item(kind, resource, region)
        if self.dry:
            self.result["would_clean"].append(item)
        else:
            rds.remove_tags_from_resource(ResourceName=arn, TagKeys=["TTL-Start"])
            self.result["cleaned"].append(item)

    def scan_ec2(self, client, region):
        for page in self.pages(client, "describe_instances"):
            for reservation in page.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    self.check_time()
                    resource = instance["InstanceId"]
                    try:
                        tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                        if "TTL" not in tags or instance.get("State", {}).get("Name") != "running":
                            continue
                        if "aws:autoscaling:groupName" in tags:
                            self.skip("EC2", resource, region, "Auto Scaling Group")
                            continue
                        if self.now >= utc_time(instance["LaunchTime"]) + duration(tags["TTL"]):
                            self.stop("EC2", resource, region,
                                      lambda: client.stop_instances(InstanceIds=[resource]))
                    except Exception as exc:
                        self.error(resource, region, exc)

    def db(self, rds, obj, region, cluster=False):
        self.check_time()
        kind = "Aurora" if cluster else "RDS"
        resource = obj["DBClusterIdentifier" if cluster else "DBInstanceIdentifier"]
        try:
            tags = {t["Key"]: t["Value"] for t in obj.get("TagList", [])}
            if "TTL" not in tags:
                return
            status = obj.get("Status" if cluster else "DBInstanceStatus")
            if cluster:
                if obj.get("Engine") not in {"aurora", "aurora-mysql", "aurora-postgresql"}:
                    return
                if obj.get("EngineMode") == "serverless":
                    self.skip(kind, resource, region, "Aurora Serverless v1")
                    return
                if (obj.get("GlobalClusterIdentifier") or obj.get("ReplicationSourceIdentifier")
                        or obj.get("ReadReplicaIdentifiers")):
                    self.skip(kind, resource, region, "Global/cross-region replication topology")
                    return
            elif obj.get("DBClusterIdentifier"):
                self.skip(kind, resource, region, "Cluster member; process TTL on cluster")
                return
            elif (obj.get("ReadReplicaSourceDBInstanceIdentifier")
                  or obj.get("ReadReplicaSourceDBClusterIdentifier")
                  or obj.get("ReadReplicaDBInstanceIdentifiers")
                  or (obj.get("Engine", "").startswith("sqlserver") and obj.get("MultiAZ"))):
                self.skip(kind, resource, region, "Read replica/unsupported DB topology")
                return
            arn_type = "cluster" if cluster else "db"
            arn = obj.get("DBClusterArn" if cluster else "DBInstanceArn") or (
                f"arn:aws:rds:{region}:{self.account}:{arn_type}:{resource}")
            if status == "stopped":
                if "TTL-Start" in tags:
                    self.clear_clock(rds, arn, kind, resource, region)
                return
            if status != "available":
                self.skip(kind, resource, region, f"Busy state: {status}")
                return
            lifetime = duration(tags["TTL"])
            if "TTL-Start" not in tags:
                item = self.item(kind, resource, region)
                if self.dry:
                    self.result["would_initialize"].append(item)
                else:
                    rds.add_tags_to_resource(ResourceName=arn, Tags=[{
                        "Key": "TTL-Start", "Value": self.now.strftime("%Y-%m-%dT%H:%M:%SZ")}])
                    self.result["initialized"].append(item)
                return
            if self.now >= utc_time(tags["TTL-Start"]) + lifetime:
                operation = (lambda: rds.stop_db_cluster(DBClusterIdentifier=resource)) if cluster else (
                    lambda: rds.stop_db_instance(DBInstanceIdentifier=resource))
                self.stop(kind, resource, region, operation)
                if not self.dry:
                    self.clear_clock(rds, arn, kind, resource, region)
        except Exception as exc:
            self.error(resource, region, exc)

    def scan_db(self, rds, region, cluster):
        operation = "describe_db_clusters" if cluster else "describe_db_instances"
        key = "DBClusters" if cluster else "DBInstances"
        for page in self.pages(rds, operation):
            for obj in page.get(key, []):
                self.db(rds, obj, region, cluster)

    def scan(self, ec2, rds, region):
        for name, operation in [
                ("EC2 scan", lambda: self.scan_ec2(ec2, region)),
                ("RDS scan", lambda: self.scan_db(rds, region, False)),
                ("Aurora scan", lambda: self.scan_db(rds, region, True))]:
            try:
                self.check_time()
                operation()
            except Exception as exc:
                self.error(name, region, exc)
        self.result["regions_scanned"].append(region)

    def finish(self, topic):
        # notify.py dùng chung chịu trách nhiệm bỏ dấu/cắt tiêu đề và gửi SNS.
        actions = self.result["would_stop"] if self.dry else self.result["stop_requested"]
        self.result["notification"] = None
        if actions or self.result["errors"]:
            lines = ["Báo cáo TTL - Cloud Cost Guardian"]
            lines.extend(field_lines([
                ("Thời gian", fmt_time(self.now)),
                ("Chế độ", "Chạy thử, không thay đổi tài nguyên" if self.dry else "Chạy thật"),
            ]))
            lines.extend(["", "Tài nguyên sẽ được yêu cầu dừng:" if self.dry else
                          "Yêu cầu dừng đã được AWS chấp nhận (có thể vẫn đang Stopping):"])
            lines.extend(bullet(
                f"{item['type']} {item['id']} | Region: {item['region']}" for item in actions
            ))
            if not actions:
                lines.extend(bullet(["Không có."]))
            lines.append("Lỗi:")
            lines.extend(bullet(
                f"{item['region']}/{item['resource']}: {item['error']}"
                for item in self.result["errors"]
            ))
            if not self.result["errors"]:
                lines.extend(bullet(["Không có."]))
            message = "\n".join(lines)
            if len(message.encode("utf-8")) > 240000:
                message = message.encode("utf-8")[:238000].decode("utf-8", errors="ignore")
                message += "\n[Nội dung đã rút gọn; xem đầy đủ trong CloudWatch Logs.]"
            stopped_count = len(actions)
            error_count = len(self.result["errors"])
            if stopped_count:
                content = f"Đã dừng {stopped_count} tài nguyên hết hạn"
            else:
                content = f"Lỗi xử lý TTL ({error_count})"
            self.result["notification"] = send(
                "TTL", content, message.split("\n"), topic_arn=topic, dry_run=self.dry)
        LOG.info("TTL result: %s", json.dumps(self.result, ensure_ascii=False))
        return self.result


def run(event, ec2, rds, topic_arn, account_id):
    """Chạy một Region với client giả lập và đồng hồ cố định để test offline."""
    worker = Worker(event, account_id)
    worker.scan(ec2, rds, rds.meta.region_name)
    return worker.finish(topic_arn)


def run_regions(event, factory, regions, topic, account, context=None):
    worker = Worker(event, account, context)
    for region in dict.fromkeys(regions):
        try:
            worker.check_time()
            worker.scan(factory("ec2", region), factory("rds", region), region)
        except Exception as exc:
            worker.error("Region scan", region, exc)
    return worker.finish(topic)


def lambda_handler(event, context):
    print("EVENT:", json.dumps(event, ensure_ascii=False))
    import boto3
    from botocore.config import Config

    validate(event)
    if "now" in event and not event.get("dry_run", False):
        raise ValueError("A fixed now is only allowed for dry-run in AWS")

    topic = os.environ["TOPIC_ARN"]
    home = os.environ.get("AWS_REGION", "us-east-1")
    parts = topic.split(":")
    if len(parts) != 6 or parts[2] != "sns" or parts[3] != home:
        raise ValueError("TOPIC_ARN must be a topic in the Lambda region")

    all_regions = os.environ.get("ALL_REGIONS", "true").strip().lower()
    if all_regions not in {"true", "false"}:
        raise ValueError("ALL_REGIONS must be true or false")

    config = Config(connect_timeout=3, read_timeout=5,
                    retries={"mode": "standard", "total_max_attempts": 2})

    def factory(service, region):
        return boto3.client(service, region_name=region, config=config)

    regions = [home]
    if all_regions == "true":
        try:
            response = factory("ec2", home).describe_regions(AllRegions=False)
            regions = sorted({x["RegionName"] for x in response["Regions"]
                              if x.get("OptInStatus") in {None, "opt-in-not-required", "opted-in"}})
            if not regions:
                raise ValueError("No enabled regions returned by DescribeRegions")
        except Exception as exc:
            worker = Worker(event, parts[4], context)
            worker.error("Region discovery", home, exc)
            return worker.finish(topic)

    LOG.info("TTL regions: %s", regions)
    return run_regions(event, factory, regions, topic, parts[4], context)
