import glob
import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

from idle import find_idle_instances
from notify import bullet, field_lines, fmt_date, fmt_monthly, send

try:
    from pricing import estimate
except Exception as _e:  # chưa deploy pricing.py -> vẫn quét, chỉ không có giá
    print(f"[WARN] Không import được pricing.py ({_e}); báo cáo sẽ không có ước tính chi phí")

    def estimate(*args, **kwargs):
        return None


def _env_num(name, default, cast=float):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return cast(default)


TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
ALL_REGIONS = os.environ.get("ALL_REGIONS", "true").strip().lower() in ("true", "1", "yes", "y")
HOME_REGION = os.environ.get("AWS_REGION", "us-east-1")
SNAPSHOT_AGE_DAYS = _env_num("SNAPSHOT_AGE_DAYS", 90, int)
IDLE_CPU_THRESHOLD = _env_num("IDLE_CPU_THRESHOLD", 5, float)
IDLE_DAYS = _env_num("IDLE_DAYS", 7, int)

CFG = Config(retries={"max_attempts": 5, "mode": "standard"},
             connect_timeout=5, read_timeout=20)

NONE_TEXT = "(không có)"
NO_TARGET_MARK = "⚠"     # ký hiệu đánh dấu LB không có target (tài liệu ghi △; đổi ở đây nếu muốn)
COST_COL = 68             # cột bắt đầu của "~xx USD/tháng" trong từng dòng
REGION_BAR = 58           # tổng độ rộng dòng "==== Region xxx ===="
MAX_BODY_BYTES = 240_000  # SNS giới hạn 256 KB

# Thứ tự mục trong từng Region / trong phần tóm tắt
SECTION_ORDER = ["EC2", "RDS", "NAT", "LB", "VPCE", "EIP", "EBS", "IDLE", "SNAP"]
SUMMARY_ORDER = ["EC2", "RDS", "NAT", "LB", "VPCE", "EIP", "EBS", "SNAP", "IDLE"]
COST_SECTIONS = {"EC2", "RDS", "NAT", "LB", "VPCE", "EIP", "EBS", "SNAP"}   # mục nào cũng ghi giá từng dòng
RUNNING_SECTIONS = {"EC2", "RDS", "NAT", "LB", "VPCE"}   # chỉ các mục này được cộng vào dòng tổng
LB_PRICE_KEY = {"application": "alb", "network": "nlb", "gateway": "gwlb"}  # loại LB -> pricing.py


def section_labels():
    thr = f"{IDLE_CPU_THRESHOLD:g}"
    summary = {
        "EC2": "EC2 đang chạy",
        "RDS": "RDS đang chạy",
        "NAT": "NAT Gateway",
        "LB": "Load Balancer",
        "VPCE": "VPC Interface Endpoint",
        "EIP": "Elastic IP chưa gắn (bị bỏ quên)",
        "EBS": "Ổ EBS không gắn máy (bị bỏ quên)",
        "SNAP": f"Snapshot cũ hơn {SNAPSHOT_AGE_DAYS} ngày",
        "IDLE": f"Có thể đang bị bỏ quên (CPU < {thr}%)",
    }
    region = dict(summary, EIP="Elastic IP chưa gắn", EBS="Ổ EBS không gắn máy",
                  IDLE="Có thể đang bị bỏ quên")
    return summary, region

def date_text(dt):
    return "?" if dt is None else fmt_date(dt)


def cost_text(est):
    if not est:
        return "(chưa có giá)"
    m = est["monthly"]
    txt = "<0,1 USD/tháng" if m < 0.05 else f"~{fmt_monthly(m)} USD/tháng"
    return txt + (" (giá tham khảo)" if est.get("source") == "fallback" else "")


def price(kind, region, **attrs):
    try:
        return estimate(kind, region, **attrs)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Không ước tính được giá {kind} ({region}): {type(e).__name__}: {e}")
        return None


def tags_dict(tags):
    return {t["Key"]: t["Value"] for t in (tags or [])}


def owner_ttl(tags):
    d = tags_dict(tags)
    return f"Owner={d.get('Owner', NONE_TEXT)}, TTL={d.get('TTL', NONE_TEXT)}"


def owner_only(tags):
    return f"Owner={tags_dict(tags).get('Owner', NONE_TEXT)}"


def item(section, text, est=None, **flags):
    d = {"section": section, "text": text, "est": est}
    d.update(flags)
    return d


def get_regions():
    if not ALL_REGIONS:
        return [HOME_REGION]
    ec2 = boto3.client("ec2", region_name=HOME_REGION, config=CFG)
    return sorted(r["RegionName"] for r in ec2.describe_regions()["Regions"])


_FIXTURE = None  # {region: FixtureClient} khi đang chạy bằng dữ liệu mẫu; None = quét AWS thật


def client(service, region):
    if _FIXTURE is not None:
        return _FIXTURE[region]
    return boto3.client(service, region_name=region, config=CFG)


RESPONSE_KEYS = {
    "describe_instances": "Reservations",
    "describe_db_instances": "DBInstances",
    "describe_nat_gateways": "NatGateways",
    "describe_volumes": "Volumes",
    "describe_load_balancers": "LoadBalancers",
    "describe_vpc_endpoints": "VpcEndpoints",
    "describe_snapshots": "Snapshots",
    "describe_db_snapshots": "DBSnapshots",
    "describe_images": "Images",
    "describe_target_groups": "TargetGroups",
}
FIXTURE_KEYS = set(RESPONSE_KEYS.values()) | {
    "Addresses", "Images", "TargetGroups", "TargetHealth", "CpuDailyAverages"}
TIME_KEYS = {"LaunchTime", "CreateTime", "InstanceCreateTime", "StartTime",
             "SnapshotCreateTime", "CreatedTime"}


def _parse_times(obj):
    if isinstance(obj, dict):
        return {k: (_parse_time(v) if k in TIME_KEYS and isinstance(v, str) else _parse_times(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_parse_times(x) for x in obj]
    return obj


def _parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class FixtureClient:

    class exceptions:
        class TargetGroupNotFoundException(Exception):
            pass

    def __init__(self, data):
        self.data = _parse_times(data)

    def get_paginator(self, name):
        key = RESPONSE_KEYS[name]
        items = self.data.get(key, [])

        class Paginator:
            def paginate(self, **kwargs):
                if name == "describe_target_groups":   # chỉ target group của đúng load balancer được hỏi
                    arn = kwargs.get("LoadBalancerArn")
                    return [{key: [t for t in items if arn in t.get("LoadBalancerArns", [])]}]
                return [{key: items}]
        return Paginator()

    def describe_addresses(self, **kwargs):
        return {"Addresses": self.data.get("Addresses", [])}

    def describe_target_health(self, TargetGroupArn=None, **kwargs):
        return {"TargetHealthDescriptions": self.data.get("TargetHealth", {}).get(TargetGroupArn, [])}

    def get_metric_data(self, MetricDataQueries=None, EndTime=None, **kwargs):
        results = []
        for query in MetricDataQueries or []:
            instance_id = query["MetricStat"]["Metric"]["Dimensions"][0]["Value"]
            values = list(self.data.get("CpuDailyAverages", {}).get(instance_id, []))
            n = len(values)
            stamps = [EndTime - timedelta(days=n - i) for i in range(n)]
            results.append({"Id": query["Id"], "Timestamps": stamps, "Values": values,
                            "StatusCode": "Complete"})
        return {"MetricDataResults": results}


def is_raw_fixture(event):
    return (not isinstance(event.get("fixture"), dict) and not event.get("fixture_dir")
            and not isinstance(event.get("fixture_regions"), dict)
            and any(k in event for k in FIXTURE_KEYS))


def _merge_json(folder):
    data = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        with open(path, encoding="utf-8") as f:
            part = json.load(f)
        if isinstance(part, dict):
            data.update(part)
    return data


def _load_fixture_dir(folder, default_region):
    result = {}
    data = _merge_json(folder)
    if data:
        result[default_region] = data
    for name in sorted(os.listdir(folder)):
        sub = os.path.join(folder, name)
        if os.path.isdir(sub):
            part = _merge_json(sub)
            if part:
                result[name] = part
    if not result:
        raise ValueError(f"fixture_dir '{folder}' không có file *.json nào dùng được")
    return result


def load_fixture(event):
    default_region = event.get("fixture_region") or HOME_REGION
    multi = event.get("fixture_regions")
    if isinstance(multi, dict):
        bad = [r for r, d in multi.items() if not isinstance(d, dict)]
        if not multi or bad:
            raise ValueError("fixture_regions phải là {tên Region: {dữ liệu mẫu}}"
                             + (f"; sai ở: {', '.join(bad)}" if bad else ""))
        return dict(multi)
    if isinstance(event.get("fixture"), dict):
        return {default_region: event["fixture"]}
    if event.get("fixture_dir"):
        return _load_fixture_dir(event["fixture_dir"], default_region)
    if is_raw_fixture(event):
        return {default_region: {k: v for k, v in event.items() if k in FIXTURE_KEYS}}
    return None

def idle_text(avg_cpu):
    return f"CPU trung bình {avg_cpu:.1f}".replace(".", ",") + f"% trong {IDLE_DAYS} ngày qua"


def scan_ec2(region, errors):
    ec2, cw = client("ec2", region), client("cloudwatch", region)
    out = []
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
    for page in pages:
        for res in page["Reservations"]:
            for i in res["Instances"]:
                if i.get("State", {}).get("Name", "running") != "running":
                    continue
                est = price("ec2", region, instance_type=i["InstanceType"],
                               platform=i.get("PlatformDetails", ""))
                out.append(item(
                    "EC2", f"{i['InstanceId']}  {i['InstanceType']}  {owner_ttl(i.get('Tags'))}", est,
                    no_ttl="TTL" not in tags_dict(i.get("Tags"))))
    if IDLE_DAYS > 0:
        try:
            for c in find_idle_instances(ec2, cw, datetime.now(timezone.utc), IDLE_CPU_THRESHOLD, IDLE_DAYS):
                out.append(item("IDLE", f"{c['instance_id']}  {idle_text(c['avg_cpu'])}"))
        except Exception as e:
            errors.append(f"{region} / EC2 (kiểm tra CPU): {type(e).__name__}: {e}")
    return out


def scan_rds(region, errors):
    rds = client("rds", region)
    out = []
    for page in rds.get_paginator("describe_db_instances").paginate():
        for d in page["DBInstances"]:
            if d.get("DBInstanceStatus") != "available":
                continue
            est = price("rds", region, instance_class=d["DBInstanceClass"],
                           engine=d["Engine"], multi_az=d.get("MultiAZ", False))
            out.append(item(
                "RDS", f"{d['DBInstanceIdentifier']}  {d['DBInstanceClass']}  "
                       f"{owner_ttl(d.get('TagList'))}", est,
                no_ttl="TTL" not in tags_dict(d.get("TagList"))))
    return out


def scan_nat(region, errors):
    ec2 = client("ec2", region)
    out = []
    pages = ec2.get_paginator("describe_nat_gateways").paginate(
        Filter=[{"Name": "state", "Values": ["available"]}])
    for page in pages:
        for n in page["NatGateways"]:
            if n.get("State", "available") != "available":
                continue
            out.append(item(
                "NAT", f"{n['NatGatewayId']}  tạo {date_text(n.get('CreateTime'))}  "
                       f"{owner_only(n.get('Tags'))}", price("nat", region)))
    return out


def scan_eip(region, errors):
    ec2 = client("ec2", region)
    out = []
    for a in ec2.describe_addresses()["Addresses"]:
        if a.get("AssociationId"):
            continue
        ident = a.get("AllocationId") or a.get("PublicIp")
        out.append(item(
            "EIP", f"{ident}  {a.get('PublicIp', '?')}  {owner_only(a.get('Tags'))}",
            price("eip", region)))
    return out


def scan_ebs(region, errors):
    ec2 = client("ec2", region)
    out = []
    pages = ec2.get_paginator("describe_volumes").paginate(
        Filters=[{"Name": "status", "Values": ["available"]}])
    for page in pages:
        for v in page["Volumes"]:
            if v.get("State", "available") != "available":
                continue
            est = price("ebs", region, volume_type=v["VolumeType"], size_gb=v["Size"])
            out.append(item(
                "EBS", f"{v['VolumeId']}  {v['VolumeType']}  {v['Size']} GiB  "
                       f"tạo {date_text(v.get('CreateTime'))}  {owner_only(v.get('Tags'))}", est))
    return out


def count_targets(elb, lb_arn):
    try:
        tgs = [tg for page in elb.get_paginator("describe_target_groups").paginate(LoadBalancerArn=lb_arn)
               for tg in page["TargetGroups"]]
    except elb.exceptions.TargetGroupNotFoundException:
        tgs = []  # LB chưa có target group nào
    total = 0
    for tg in tgs:
        total += len(elb.describe_target_health(
            TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"])
    return total


def scan_lb(region, errors):
    elb = client("elbv2", region)
    out = []
    for page in elb.get_paginator("describe_load_balancers").paginate():
        for lb in page["LoadBalancers"]:
            if lb.get("State", {}).get("Code") != "active":
                continue                 # chỉ tính LB trạng thái active (bỏ provisioning, failed...)
            warn = ""
            try:
                if count_targets(elb, lb["LoadBalancerArn"]) == 0:
                    warn = f"  {NO_TARGET_MARK} không có target nào"
            except Exception as e:
                errors.append(f"{region} / Load Balancer {lb['LoadBalancerName']} "
                              f"(kiểm tra target): {type(e).__name__}: {e}")
            lb_type = lb.get("Type", "application")
            est = price(LB_PRICE_KEY.get(lb_type, "alb"), region)
            out.append(item("LB", f"{lb['LoadBalancerName']}  {lb_type}{warn}", est,
                            empty_lb=bool(warn)))
    return out


def scan_vpce(region, errors):
    ec2 = client("ec2", region)
    out = []
    pages = ec2.get_paginator("describe_vpc_endpoints").paginate(
        Filters=[{"Name": "vpc-endpoint-type", "Values": ["Interface"]}])
    for page in pages:
        for ep in page["VpcEndpoints"]:
            if ep.get("VpcEndpointType", "Interface") != "Interface" or ep.get("State") != "available":
                continue                 # loại Gateway (S3, DynamoDB) miễn phí nên bỏ qua
            azs = len(ep.get("SubnetIds", [])) or 1
            service = ep.get("ServiceName", "?").split(".", 3)[-1]
            out.append(item(
                "VPCE", f"{ep['VpcEndpointId']}  {service}  {azs} AZ  {owner_only(ep.get('Tags'))}",
                price("vpce", region, count=azs)))
    return out

def ami_snapshot_ids(ec2):
    ids = set()
    for page in ec2.get_paginator("describe_images").paginate(Owners=["self"]):
        for img in page["Images"]:
            for m in img.get("BlockDeviceMappings", []):
                sid = m.get("Ebs", {}).get("SnapshotId")
                if sid:
                    ids.add(sid)
    return ids


def scan_ebs_snapshots(region, errors):
    ec2 = client("ec2", region)
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_AGE_DAYS)
    in_use = ami_snapshot_ids(ec2)
    out = []
    for page in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"]):
        for s in page["Snapshots"]:
            if s["SnapshotId"] in in_use or s["StartTime"] > cutoff:
                continue
            size = s.get("VolumeSize", 0)
            out.append(item(
                "SNAP", f"{s['SnapshotId']}  {size} GiB  tạo {date_text(s['StartTime'])}",
                price("snapshot", region, size_gb=size)))
    return out


def scan_rds_snapshots(region, errors):
    rds = client("rds", region)
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_AGE_DAYS)
    out = []
    for page in rds.get_paginator("describe_db_snapshots").paginate(SnapshotType="manual"):
        for s in page["DBSnapshots"]:
            created = s.get("SnapshotCreateTime")
            if not created or created > cutoff or s.get("Status") != "available":
                continue
            if s.get("SnapshotType", "manual") != "manual":
                continue
            size = s.get("AllocatedStorage", 0)
            out.append(item(
                "SNAP", f"{s['DBSnapshotIdentifier']} (RDS, thủ công)  {size} GiB  "
                        f"tạo {date_text(created)}",
                price("rds_snapshot", region, size_gb=size)))
    return out


SCANNERS = [
    ("EC2", scan_ec2),
    ("RDS", scan_rds),
    ("NAT Gateway", scan_nat),
    ("Elastic IP", scan_eip),
    ("EBS", scan_ebs),
    ("Load Balancer", scan_lb),
    ("VPC Endpoint", scan_vpce),
    ("Snapshot EBS", scan_ebs_snapshots),
    ("Snapshot RDS", scan_rds_snapshots),
]

def collect(fixture_regions=None):
    findings, errors = {}, []
    if fixture_regions:
        regions = sorted(fixture_regions)
    else:
        try:
            regions = get_regions()
        except Exception as e:
            errors.append(f"Không lấy được danh sách Region ({type(e).__name__}: {e}); "
                          f"chỉ quét {HOME_REGION}")
            regions = [HOME_REGION]

    for region in regions:
        for label, fn in SCANNERS:
            try:
                items = fn(region, errors)
            except Exception as e:
                errors.append(f"{region} / {label}: {type(e).__name__}: {e}")
                print(f"[ERROR] {region} {label}: {e}")
                continue
            for it in items:
                findings.setdefault(region, {}).setdefault(it["section"], []).append(it)
    return findings, errors, len(regions)


def region_bar(region):
    mid = f" Region {region} "
    return "=" * 20 + mid + "=" * max(3, REGION_BAR - 20 - len(mid))


def render_item(it):
    line = bullet([it["text"]])[0]
    if it["section"] not in COST_SECTIONS:
        return line
    return line + " " * max(2, COST_COL - len(line)) + cost_text(it["est"])


def cap_lines(lines):
    out, size = [], 0
    for line in lines:
        size += len(line.encode("utf-8")) + 1
        if size > MAX_BODY_BYTES:
            out.append("... (báo cáo bị cắt vì quá dài, xem kết quả đầy đủ trong CloudWatch Logs)")
            break
        out.append(line)
    return out


def build_report(findings, errors, n_regions, fixture=False):
    summary_lbl, region_lbl = section_labels()
    counts = {k: 0 for k in SECTION_ORDER}
    for by in findings.values():
        for sec, its in by.items():
            counts[sec] += len(its)
    total = sum(n for sec, n in counts.items() if sec != "IDLE")

    priced = [it for by in findings.values() for sec in RUNNING_SECTIONS
              for it in by.get(sec, []) if it["est"]]
    n_cost_items = sum(counts[s] for s in RUNNING_SECTIONS)
    sum_m = sum(it["est"]["monthly"] for it in priced)
    sum_h = sum(it["est"]["hourly"] for it in priced)

    content = f"Bao cao tai nguyen ngay {fmt_date()} ({total} muc)" + (" [DU LIEU MAU]" if fixture else "")
    lines = [f"Báo cáo tài nguyên AWS ngày {fmt_date()} (quét {n_regions} Region)"]
    if fixture:
        lines.append("⚠ DỮ LIỆU MẪU")
    if errors and total > 0:
        lines.append(f"⚠ Kết quả chưa đầy đủ: quá trình quét gặp {len(errors)} lỗi "
                     "(xem mục \"Lỗi khi quét\" bên dưới)")
    lines.append("")

    if total == 0:
        if errors:
            lines.append("Chưa ước tính được chi phí: "
                         f"quá trình quét gặp {len(errors)} lỗi nên kết quả chưa đầy đủ "
                         "(xem mục \"Lỗi khi quét\" bên dưới).")
        else:
            lines.append("Không có tài nguyên nào đang phát sinh chi phí "
                         "(EC2, RDS, NAT Gateway, Load Balancer, VPC Endpoint, Elastic IP, EBS, "
                         f"snapshot cũ hơn {SNAPSHOT_AGE_DAYS} ngày).")
        lines.append("")
    else:
        if n_cost_items == 0:
            cost_txt = "0 USD/tháng"
        elif not priced:
            cost_txt = f"chưa tính được giá {n_cost_items} mục"
        else:
            cost_txt = cost_text({"monthly": sum_m})
            if len(priced) < n_cost_items:
                cost_txt += f" (chưa tính được giá {n_cost_items - len(priced)} mục)"
        pairs = [(summary_lbl[sec], counts[sec]) for sec in SUMMARY_ORDER
                 if not (sec == "IDLE" and IDLE_DAYS <= 0)]
        pairs.append(("Ước tính tài nguyên đang chạy", cost_txt))
        lines.extend("  " + l for l in field_lines(pairs))
        lines.append("")

        for region in sorted(findings):
            lines.append(region_bar(region))
            by = findings[region]
            for sec in SECTION_ORDER:
                its = by.get(sec)
                if not its:
                    continue
                lines.append(f"{region_lbl[sec]} ({len(its)})")
                lines.extend(render_item(it) for it in its)
            lines.append("")

    if errors:
        lines.append(f"Lỗi khi quét ({len(errors)})")
        lines.extend(bullet(errors))
        lines.append("")

    all_items = [it for by in findings.values() for its in by.values() for it in its]
    n_no_ttl = sum(1 for it in all_items if it.get("no_ttl"))
    n_empty_lb = sum(1 for it in all_items if it.get("empty_lb"))
    n_forgotten = counts["EIP"] + counts["EBS"] + counts["SNAP"] + counts["IDLE"] + n_empty_lb
    if n_forgotten:
        lines.append(f"Gợi ý: có {n_forgotten} mục có vẻ bị bỏ quên "
                     "(Elastic IP / ổ EBS chưa gắn, snapshot cũ, Load Balancer không target, "
                     "máy CPU thấp) - xem xét xóa nếu không còn dùng.")
    if n_no_ttl:
        lines.append(f"Gợi ý: gắn tag TTL (vd 4h) cho {n_no_ttl} EC2/RDS chưa có TTL "
                     "để tự động dừng khi hết hạn.")
    if counts["SNAP"]:
        lines.append("Lưu ý: giá snapshot tính theo dung lượng ổ gốc nên là mức tối đa.")

    while lines and lines[-1] == "":
        lines.pop()
    return content, lines, total, sum_h, sum_m


def lambda_handler(event, context):
    event = event or {}
    dry_run = bool(event.get("dry_run"))

    global _FIXTURE
    fixture = load_fixture(event)
    if fixture is not None and is_raw_fixture(event) and "dry_run" not in event:
        dry_run = True
    _FIXTURE = {r: FixtureClient(d) for r, d in fixture.items()} if fixture is not None else None
    try:
        findings, errors, n_regions = collect(fixture_regions=list(fixture) if fixture is not None else None)
    finally:
        _FIXTURE = None
    content, lines, total, sum_h, sum_m = build_report(
        findings, errors, n_regions, fixture=fixture is not None)

    res = send("DAILY", content, cap_lines(lines), topic_arn=TOPIC_ARN or None, dry_run=dry_run)
    print(res["message"])

    return {
        "dry_run": dry_run,
        "fixture": fixture is not None,
        "total": total,
        "errors": len(errors),
        "estimated_hourly_usd": round(sum_h, 4),
        "estimated_monthly_usd": round(sum_m, 2),
        "subject": res["subject"],
        "report": res["message"],
        "sent": res["sent"],
        "message_id": res["message_id"],
    }