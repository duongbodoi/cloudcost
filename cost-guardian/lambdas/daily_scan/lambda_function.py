import glob
import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.config import Config

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
COST_SECTIONS = {"EC2", "RDS", "NAT", "LB", "VPCE", "EIP", "EBS", "SNAP"}
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


# ---------------------------------------------------------------- tiện ích
def date_text(dt):
    """dd/mm/yyyy theo UTC+7; '?' nếu thiếu (fmt_date(None) của notify trả ngày hôm nay)."""
    return "?" if dt is None else fmt_date(dt)


def cost_text(est):
    """'~43 USD/tháng' theo kiểu fmt_monthly của notify (<10: một số lẻ, từ 10: làm tròn nguyên)."""
    if not est:
        return "(chưa có giá)"
    m = est["monthly"]
    txt = "<0,1 USD/tháng" if m < 0.05 else f"~{fmt_monthly(m)} USD/tháng"
    return txt + (" (giá tham khảo)" if est.get("source") == "fallback" else "")


def tags_dict(tags):
    return {t["Key"]: t["Value"] for t in (tags or [])}


def owner_ttl(tags):
    d = tags_dict(tags)
    return f"Owner={d.get('Owner', NONE_TEXT)}, TTL={d.get('TTL', NONE_TEXT)}"


def owner_only(tags):
    return f"Owner={tags_dict(tags).get('Owner', NONE_TEXT)}"


def item(section, text, est=None, **flags):
    """text là nội dung dòng, chưa có gạch đầu dòng (render_item sẽ thêm bằng notify.bullet)."""
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


# ---------------------------------------------------------------- chế độ chạy bằng dữ liệu mẫu
RESPONSE_KEYS = {   # tên API -> khóa chứa danh sách trong phản hồi của AWS
    "describe_instances": "Reservations",
    "describe_db_instances": "DBInstances",
    "describe_nat_gateways": "NatGateways",
    "describe_volumes": "Volumes",
    "describe_load_balancers": "LoadBalancers",
    "describe_vpc_endpoints": "VpcEndpoints",
    "describe_snapshots": "Snapshots",
    "describe_db_snapshots": "DBSnapshots",
    "describe_db_cluster_snapshots": "DBClusterSnapshots",
}
# Khóa nhận diện event là một file mẫu dán thẳng (vd nguyên nội dung describe_db_instances.json)
FIXTURE_KEYS = set(RESPONSE_KEYS.values()) | {
    "Addresses", "Images", "TargetGroups", "TargetHealth", "CpuDailyAverages"}
TIME_KEYS = {"LaunchTime", "CreateTime", "InstanceCreateTime", "StartTime",
             "SnapshotCreateTime", "CreatedTime"}


def _parse_times(obj):
    """Đổi chuỗi ISO 8601 ở các trường thời gian thành datetime, như boto3 trả về."""
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
    """Thay thế client boto3 khi test: trả dữ liệu mẫu, bỏ qua mọi bộ lọc của API.
    Các hàm quét tự lọc lại theo trạng thái nên kết quả vẫn đúng như khi chạy thật."""

    class exceptions:
        class TargetGroupNotFoundException(Exception):
            pass

    def __init__(self, data):
        self.data = _parse_times(data)

    def get_paginator(self, name):
        key = RESPONSE_KEYS[name]
        pages = [{key: self.data.get(key, [])}]

        class Paginator:
            def paginate(self, **kwargs):
                return pages
        return Paginator()

    def describe_addresses(self, **kwargs):
        return {"Addresses": self.data.get("Addresses", [])}

    def describe_images(self, **kwargs):
        return {"Images": self.data.get("Images", [])}

    def describe_target_groups(self, LoadBalancerArn=None, **kwargs):
        tgs = [t for t in self.data.get("TargetGroups", [])
               if LoadBalancerArn in t.get("LoadBalancerArns", [])]
        return {"TargetGroups": tgs}

    def describe_target_health(self, TargetGroupArn=None, **kwargs):
        return {"TargetHealthDescriptions": self.data.get("TargetHealth", {}).get(TargetGroupArn, [])}

    def get_metric_statistics(self, Dimensions=None, **kwargs):
        values = self.data.get("CpuDailyAverages", {}).get(Dimensions[0]["Value"], [])
        return {"Datapoints": [{"Average": v} for v in values]}


def is_raw_fixture(event):
    """Event là file mẫu dán thẳng: có khóa dữ liệu mẫu nhưng không bọc trong "fixture"/"fixture_dir"."""
    return (not isinstance(event.get("fixture"), dict) and not event.get("fixture_dir")
            and not isinstance(event.get("fixture_regions"), dict)
            and any(k in event for k in FIXTURE_KEYS))


def _merge_json(folder):
    """Gộp mọi file *.json (dạng dict) trong thư mục thành một dict."""
    data = {}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        with open(path, encoding="utf-8") as f:
            part = json.load(f)
        if isinstance(part, dict):
            data.update(part)
    return data


def _load_fixture_dir(folder, default_region):
    """File *.json trực tiếp trong thư mục -> default_region; mỗi thư mục con -> Region mang tên thư mục."""
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
    """Dữ liệu mẫu lấy từ event, dạng {region: dữ liệu}; None nghĩa là quét tài nguyên thật.
    Chấp nhận: "fixture_regions" (mỗi Region một khối), "fixture" (một Region), "fixture_dir",
    hoặc dán thẳng nội dung các file describe_*.json. Region mặc định: "fixture_region" hoặc Region của Lambda."""
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


# ---------------------------------------------------------------- hạng mục 8
def cpu_idle_text(cw, inst, now_utc):
    """Chuỗi mô tả nếu EC2 đã chạy đủ IDLE_DAYS ngày và CPU trung bình MỖI NGÀY đều < ngưỡng.

    Thiếu số liệu của một số ngày thì không kết luận (trả None) để tránh báo nhầm."""
    if IDLE_DAYS <= 0:
        return None
    launch = inst.get("LaunchTime")
    if launch and now_utc - launch < timedelta(days=IDLE_DAYS):
        return None  # máy mới, chưa đủ dữ liệu
    resp = cw.get_metric_statistics(
        Namespace="AWS/EC2", MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": inst["InstanceId"]}],
        StartTime=now_utc - timedelta(days=IDLE_DAYS), EndTime=now_utc,
        Period=86400, Statistics=["Average"])
    points = resp.get("Datapoints", [])
    if len(points) < IDLE_DAYS:          # chưa đủ số liệu cho từng ngày
        return None
    if max(p["Average"] for p in points) >= IDLE_CPU_THRESHOLD:
        return None                      # có ít nhất một ngày CPU không thấp
    avg = sum(p["Average"] for p in points) / len(points)
    return f"CPU trung bình {avg:.1f}".replace(".", ",") + f"% trong {IDLE_DAYS} ngày qua"


def scan_ec2(region, errors):
    ec2, cw = client("ec2", region), client("cloudwatch", region)
    now_utc = datetime.now(timezone.utc)
    cpu_ok = True
    out = []
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
    for page in pages:
        for res in page["Reservations"]:
            for i in res["Instances"]:
                if i.get("State", {}).get("Name", "running") != "running":
                    continue            # API đã lọc sẵn; kiểm tra lại để chạy được bằng dữ liệu mẫu
                est = estimate("ec2", region, instance_type=i["InstanceType"],
                               platform=i.get("PlatformDetails", ""))
                out.append(item(
                    "EC2", f"{i['InstanceId']}  {i['InstanceType']}  {owner_ttl(i.get('Tags'))}", est,
                    no_ttl="TTL" not in tags_dict(i.get("Tags"))))
                if cpu_ok:
                    try:
                        idle = cpu_idle_text(cw, i, now_utc)
                        if idle:
                            out.append(item("IDLE", f"{i['InstanceId']}  {idle}"))
                    except Exception as e:  # lỗi CloudWatch chỉ báo một lần mỗi Region
                        cpu_ok = False
                        errors.append(f"{region} / EC2 (kiểm tra CPU): {type(e).__name__}: {e}")
    return out


def scan_rds(region, errors):
    rds = client("rds", region)
    out = []
    for page in rds.get_paginator("describe_db_instances").paginate():
        for d in page["DBInstances"]:
            if d.get("DBInstanceStatus") != "available":
                continue
            est = estimate("rds", region, instance_class=d["DBInstanceClass"],
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
                       f"{owner_only(n.get('Tags'))}", estimate("nat", region)))
    return out


def scan_eip(region, errors):
    ec2 = client("ec2", region)
    out = []
    # describe_addresses không có paginator, trả toàn bộ trong một lần
    for a in ec2.describe_addresses()["Addresses"]:
        if a.get("AssociationId"):
            continue
        ident = a.get("AllocationId") or a.get("PublicIp")
        out.append(item(
            "EIP", f"{ident}  {a.get('PublicIp', '?')}  {owner_only(a.get('Tags'))}",
            estimate("eip", region)))
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
            est = estimate("ebs", region, volume_type=v["VolumeType"], size_gb=v["Size"])
            out.append(item(
                "EBS", f"{v['VolumeId']}  {v['VolumeType']}  {v['Size']} GiB  "
                       f"tạo {date_text(v.get('CreateTime'))}  {owner_only(v.get('Tags'))}", est))
    return out


# ---------------------------------------------------------------- hạng mục 20
def count_targets(elb, lb_arn):
    """Tổng số target đã đăng ký ở mọi target group của LB (0 nếu chưa có target group)."""
    try:
        tgs = elb.describe_target_groups(LoadBalancerArn=lb_arn)["TargetGroups"]
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
            est = estimate(LB_PRICE_KEY.get(lb_type, "alb"), region)
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
                estimate("vpce", region, count=azs)))
    return out


# ---------------------------------------------------------------- hạng mục 21
def ami_snapshot_ids(ec2):
    """Tập snapshot đang được AMI của tài khoản này tham chiếu."""
    ids = set()
    for img in ec2.describe_images(Owners=["self"])["Images"]:
        for m in img.get("BlockDeviceMappings", []):
            sid = m.get("Ebs", {}).get("SnapshotId")
            if sid:
                ids.add(sid)
    return ids


def scan_ebs_snapshots(region, errors):
    ec2 = client("ec2", region)
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_AGE_DAYS)
    in_use = ami_snapshot_ids(ec2)  # lỗi ở đây -> cả mục lỗi, tránh báo nhầm snapshot của AMI
    out = []
    for page in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"]):
        for s in page["Snapshots"]:
            if s["SnapshotId"] in in_use or s["StartTime"] > cutoff:
                continue
            size = s.get("VolumeSize", 0)
            out.append(item(
                "SNAP", f"{s['SnapshotId']}  {size} GiB  tạo {date_text(s['StartTime'])}",
                estimate("snapshot", region, size_gb=size)))
    return out


def scan_rds_snapshots(region, errors):
    rds = client("rds", region)
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_AGE_DAYS)
    out = []
    # Snapshot RDS không thuộc AMI nên không cần loại trừ
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
                estimate("rds_snapshot", region, size_gb=size)))
    try:  # snapshot cluster Aurora
        pages = rds.get_paginator("describe_db_cluster_snapshots").paginate(SnapshotType="manual")
        for page in pages:
            for s in page["DBClusterSnapshots"]:
                created = s.get("SnapshotCreateTime")
                if not created or created > cutoff or s.get("Status") != "available":
                    continue
                if s.get("SnapshotType", "manual") != "manual":
                    continue
                size = s.get("AllocatedStorage", 0)
                out.append(item(
                    "SNAP", f"{s['DBClusterSnapshotIdentifier']} (RDS cluster, thủ công)  "
                            f"{size} GiB  tạo {date_text(created)}",
                    estimate("rds_snapshot", region, size_gb=size)))
    except Exception as e:
        errors.append(f"{region} / Snapshot RDS (cluster): {type(e).__name__}: {e}")
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


# ---------------------------------------------------------------- báo cáo
def collect(fixture_regions=None):
    """findings: {region: {mã_mục: [item, ...]}}. Mỗi Region / mỗi loại có try/except riêng.
    fixture_regions: chỉ đặt khi chạy bằng dữ liệu mẫu (khi đó không hỏi AWS danh sách Region)."""
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
    """Cắt bớt nếu báo cáo vượt giới hạn kích thước của SNS."""
    out, size = [], 0
    for line in lines:
        size += len(line.encode("utf-8")) + 1
        if size > MAX_BODY_BYTES:
            out.append("... (báo cáo bị cắt vì quá dài, xem kết quả đầy đủ trong CloudWatch Logs)")
            break
        out.append(line)
    return out


def build_report(findings, errors, n_regions, fixture=False):
    """Trả (content cho tiêu đề, danh sách dòng nội dung, tổng số mục, tổng USD/giờ, tổng USD/tháng)."""
    summary_lbl, region_lbl = section_labels()
    counts = {k: 0 for k in SECTION_ORDER}
    for by in findings.values():
        for sec, its in by.items():
            counts[sec] += len(its)
    # "Có thể đang bị bỏ quên" chỉ là cảnh báo trên EC2 đã đếm, không đếm lại thành tài nguyên mới
    total = sum(n for sec, n in counts.items() if sec != "IDLE")

    priced = [it for by in findings.values() for sec in COST_SECTIONS
              for it in by.get(sec, []) if it["est"]]
    n_cost_items = sum(counts[s] for s in COST_SECTIONS)
    sum_m = sum(it["est"]["monthly"] for it in priced)
    sum_h = sum(it["est"]["hourly"] for it in priced)

    content = f"Bao cao tai nguyen ngay {fmt_date()} ({total} muc)" + (" [DU LIEU MAU]" if fixture else "")
    lines = [f"Báo cáo tài nguyên AWS ngày {fmt_date()} (quét {n_regions} Region)"]
    if fixture:
        lines.append("⚠ DỮ LIỆU MẪU (fixture) - KHÔNG phải tài nguyên thật trong tài khoản")
    lines.append("")

    if total == 0:
        lines.append("Không có tài nguyên nào đang phát sinh chi phí "
                     "(EC2, RDS, NAT Gateway, Load Balancer, VPC Endpoint, Elastic IP, EBS, "
                     f"snapshot cũ hơn {SNAPSHOT_AGE_DAYS} ngày).")
        lines.append("")
    else:
        cost_txt = cost_text({"monthly": sum_m})
        if len(priced) < n_cost_items:
            cost_txt += f" (chưa tính được giá {n_cost_items - len(priced)} mục)"
        pairs = [(summary_lbl[sec], counts[sec]) for sec in SUMMARY_ORDER
                 if not (sec == "IDLE" and IDLE_DAYS <= 0)]
        pairs.append(("Ước tính chi phí hằng tháng", cost_txt))
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
        dry_run = True   # dán thẳng file mẫu: mặc định không gửi email vào topic chung của cả nhóm
    _FIXTURE = {r: FixtureClient(d) for r, d in fixture.items()} if fixture is not None else None
    try:
        findings, errors, n_regions = collect(fixture_regions=list(fixture) if fixture is not None else None)
    finally:
        _FIXTURE = None   # Lambda có thể giữ module cho lần gọi sau: không để lọt sang lần quét thật
    content, lines, total, sum_h, sum_m = build_report(
        findings, errors, n_regions, fixture=fixture is not None)

    # notify.send lo tiêu đề chuẩn [Cost-Guardian][DAILY] (không dấu, <= 99 ký tự) và việc gửi SNS;
    # dry_run=True thì không gửi, chỉ trả về nội dung để kiểm tra.
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