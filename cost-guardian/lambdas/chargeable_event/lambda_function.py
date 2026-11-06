"""
cg-chargeable-event – Cảnh báo tài nguyên tính phí mới (luồng ALERT)
=====================================================================

Kích hoạt: EventBridge rule `cg-chargeable-events` (sự kiện CloudTrail nguyên bản,
           xem event_pattern.json). Mọi sự kiện đã qua pattern đều là lệnh THÀNH CÔNG.

Việc làm cho mỗi sự kiện:
  1. Bỏ qua lệnh lỗi (phòng hờ) và lệnh do Auto Scaling tự gọi (CONVENTIONS / báo cáo 2.5.3).
  2. Trích loại tài nguyên + ID (gom nhiều máy tạo cùng lúc vào một email).
  3. Sự kiện TẠO MỚI: gắn tag Owner / CreatedBy (giữ nguyên Owner nếu người tạo đã gắn).
     Client boto3 tạo theo Region của sự kiện (detail.awsRegion) – hạng mục 17.
  4. Ước tính chi phí bằng pricing.py.
  5. Gửi email ALERT qua notify.py, đúng thứ tự trường ở CONVENTIONS mục 4.2.

Biến môi trường: TOPIC_ARN (bắt buộc).
File đi kèm (cùng thư mục khi deploy): notify.py, pricing.py (lấy từ lambdas/common/).

Tham số chỉ dùng khi KIỂM THỬ (thêm vào cấp ngoài cùng của sự kiện mẫu):
  "dry_run": true  -> không gắn tag, không gửi email; trả nội dung email trong kết quả.

Gắn tag thất bại (tài nguyên đã bị xóa, ID giả khi test, thiếu quyền…) KHÔNG làm hỏng
email: email vẫn được gửi và ghi "không gắn được (<mã lỗi>)". Hàm không ném lỗi ra ngoài
vì khi đó Lambda tự chạy lại và gửi email trùng.
"""
import json
import logging
import re

import boto3

from notify import field_lines, fmt_money, fmt_time, send
from pricing import estimate

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ALERT_KIND_TEXT = "Tài nguyên tính phí mới được tạo/bật"
SUGGESTION = ("Xóa hoặc dừng tài nguyên nếu không còn dùng; "
              "gắn tag TTL (vd: 4h) cho tài nguyên thử nghiệm.")
AUTOSCALING_PRINCIPAL = "autoscaling.amazonaws.com"
OWNER, CREATED_BY = "Owner", "CreatedBy"

# Ký tự được phép trong giá trị tag ở mọi dịch vụ (ELB/EKS/ElastiCache chặt hơn EC2)
_TAG_VALUE_BAD = re.compile(r"[^\w\s+\-=._:/@]")

_clients = {}


def _client(service, region):
    """Một client cho mỗi (dịch vụ, Region), dùng lại giữa các lần Lambda chạy ấm."""
    key = (service, region)
    if key not in _clients:
        _clients[key] = boto3.client(service, region_name=region)
    return _clients[key]


# =============================================================== tiện ích đọc sự kiện
def _get(obj, *path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _items(obj, *path):
    value = _get(obj, *path)
    return value if isinstance(value, list) else []


def _find_key(obj, key):
    """Tìm giá trị đầu tiên có tên `key` ở bất kỳ độ sâu nào (dùng cho NAT Gateway)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


# Loại tài nguyên trong tagSpecification của EC2 mà ta quan tâm khi tìm tag Owner có sẵn
_SPEC_TYPES = {"instance", "natgateway", "elastic-ip"}


def request_tags(req):
    """Gom các tag người tạo đã khai báo trong requestParameters -> {key: value}.

    Mỗi dịch vụ ghi tag một kiểu:
      EC2   tagSpecificationSet.items[].tags[] = {key, value}   (có resourceType)
      NAT   CreateNatGatewayRequest.TagSpecification[].Tag[] = {Key, Value}
      RDS / ELB / ElastiCache  tags[] = {key, value}
      EKS   tags = {"Owner": "x", ...}
    """
    found = {}

    def walk(node, parent_key=""):
        if isinstance(node, dict):
            lower = {k.lower(): v for k, v in node.items()}
            # Bỏ qua tagSpecification cho loại khác (vd volume) để không nhận nhầm
            rtype = lower.get("resourcetype")
            if isinstance(rtype, str) and rtype.lower() not in _SPEC_TYPES:
                return
            if "key" in lower and "value" in lower and isinstance(lower["key"], str):
                found[lower["key"]] = str(lower["value"])
                return
            if parent_key in ("tags", "tag") and node and all(
                    isinstance(v, str) for v in node.values()):
                found.update(node)                         # dạng map của EKS
                return
            for k, v in node.items():
                walk(v, k.lower())
        elif isinstance(node, list):
            for v in node:
                walk(v, parent_key)

    walk(req or {})
    return found


# =============================================================== người thực hiện
def actor_info(user_identity):
    """-> (owner, created_by, arn_hiển_thị) theo CONVENTIONS mục 3."""
    ui = user_identity or {}
    kind = ui.get("type")
    arn = ui.get("arn") or ""

    if kind == "Root":
        owner = "root"
    elif kind == "IAMUser":
        owner = ui.get("userName") or arn.rsplit("/", 1)[-1]
    elif arn:                                              # AssumedRole, FederatedUser…
        owner = arn.rsplit("/", 1)[-1]                     # tên phiên
    else:
        owner = ui.get("invokedBy") or ui.get("principalId") or "unknown"

    created_by = arn.rsplit(":", 1)[-1] if arn else owner  # vd user/cg-tv2, assumed-role/R/s
    shown = arn or ui.get("invokedBy") or owner
    return _clean_tag_value(owner), _clean_tag_value(created_by), shown


def _clean_tag_value(value):
    return _TAG_VALUE_BAD.sub("_", str(value))[:256]


# =============================================================== bóc tách từng sự kiện
# Mỗi hàm parse trả về (label, resources). Mỗi resource là dict:
#   id      ID hiển thị trong email
#   tag     (dịch vụ, tham chiếu) để gắn tag, hoặc None
#   price   (resource_type, {tham số}) cho pricing.estimate, hoặc None
#   lookup  ("ec2"|"rds", id) khi cần Describe để biết loại máy (sự kiện bật lại)

def _arn(service, region, account, suffix):
    return f"arn:aws:{service}:{region}:{account}:{suffix}"


def parse_run_instances(d, region, account):
    req, resp = d.get("requestParameters") or {}, d.get("responseElements") or {}
    res = []
    for inst in _items(resp, "instancesSet", "items"):
        itype = inst.get("instanceType") or req.get("instanceType")
        res.append({"id": inst.get("instanceId"),
                    "tag": ("ec2", inst.get("instanceId")),
                    "price": ("ec2", {"instance_type": itype}) if itype else None})
    return "EC2", res


def parse_start_instances(d, region, account):
    resp, req = d.get("responseElements") or {}, d.get("requestParameters") or {}
    items = _items(resp, "instancesSet", "items") or _items(req, "instancesSet", "items")
    return "EC2", [{"id": i.get("instanceId"), "lookup": ("ec2", i.get("instanceId"))}
                   for i in items if i.get("instanceId")]


def parse_create_nat_gateway(d, region, account):
    nat_id = _find_key(d.get("responseElements") or {}, "natGatewayId")
    if not nat_id:
        return "NAT Gateway", []
    return "NAT Gateway", [{"id": nat_id, "tag": ("ec2", nat_id), "price": ("nat", {})}]


def parse_allocate_address(d, region, account):
    resp = d.get("responseElements") or {}
    alloc = resp.get("allocationId")
    if not alloc:
        return "Elastic IP", []
    shown = f"{alloc} ({resp['publicIp']})" if resp.get("publicIp") else alloc
    return "Elastic IP", [{"id": shown, "tag": ("ec2", alloc), "price": ("eip", {})}]


def _rds_price(class_, engine, multi_az):
    if not class_:
        return None
    return ("rds", {"instance_class": class_, "engine": engine or "mysql", "multi_az": bool(multi_az)})


def parse_create_db_instance(d, region, account):
    req, resp = d.get("requestParameters") or {}, d.get("responseElements") or {}
    db_id = resp.get("dBInstanceIdentifier") or req.get("dBInstanceIdentifier")
    if not db_id:
        return "RDS", []
    arn = resp.get("dBInstanceArn") or _arn("rds", region, account, f"db:{db_id}")
    price = _rds_price(resp.get("dBInstanceClass") or req.get("dBInstanceClass"),
                       resp.get("engine") or req.get("engine"),
                       resp.get("multiAZ", req.get("multiAZ")))
    return "RDS", [{"id": db_id, "tag": ("rds", arn), "price": price}]


def parse_start_db_instance(d, region, account):
    req, resp = d.get("requestParameters") or {}, d.get("responseElements") or {}
    db_id = resp.get("dBInstanceIdentifier") or req.get("dBInstanceIdentifier")
    if not db_id:
        return "RDS", []
    price = _rds_price(resp.get("dBInstanceClass"), resp.get("engine"), resp.get("multiAZ"))
    item = {"id": db_id, "price": price}
    if price is None:
        item["lookup"] = ("rds", db_id)        # phản hồi không có loại máy -> Describe
    return "RDS", [item]


def parse_create_load_balancer(d, region, account):
    req, resp = d.get("requestParameters") or {}, d.get("responseElements") or {}
    lbs = _items(resp, "loadBalancers")
    if lbs:                                    # ELBv2: ALB / NLB / GWLB
        res = []
        types = set()
        for lb in lbs:
            lb_type = (lb.get("type") or req.get("type") or "application").lower()
            types.add(lb_type)
            rt = {"application": "alb", "network": "nlb"}.get(lb_type)
            res.append({"id": lb.get("loadBalancerName") or lb.get("loadBalancerArn"),
                        "tag": ("elbv2", lb.get("loadBalancerArn")),
                        "price": (rt, {}) if rt else None})
        short = {"application": "ALB", "network": "NLB", "gateway": "GWLB"}
        suffix = ", ".join(short.get(t, t) for t in sorted(types))
        return f"Load Balancer ({suffix})", res
    name = req.get("loadBalancerName")         # Classic Load Balancer (API 2012-06-01)
    if not name:
        return "Load Balancer", []
    return "Load Balancer (Classic)", [{"id": name, "tag": ("elb", name), "price": None}]


def parse_create_eks_cluster(d, region, account):
    cluster = _get(d, "responseElements", "cluster") or {}
    name = cluster.get("name") or _get(d, "requestParameters", "name")
    if not name:
        return "EKS cluster", []
    arn = cluster.get("arn") or _arn("eks", region, account, f"cluster/{name}")
    return "EKS cluster", [{"id": name, "tag": ("eks", arn), "price": ("eks", {})}]


def parse_create_nodegroup(d, region, account):
    req = d.get("requestParameters") or {}
    ng = _get(d, "responseElements", "nodegroup") or {}
    name = ng.get("nodegroupName") or req.get("nodegroupName")
    cluster = ng.get("clusterName") or req.get("name") or req.get("clusterName")
    arn = ng.get("nodegroupArn")
    if not name:
        return "EKS node group", []
    types = ng.get("instanceTypes") or req.get("instanceTypes") or []
    desired = (_get(ng, "scalingConfig", "desiredSize")
               or _get(req, "scalingConfig", "desiredSize") or 1)
    price = ("ec2", {"instance_type": types[0], "count": int(desired)}) if types else None
    return "EKS node group", [{"id": f"{cluster}/{name}" if cluster else name,
                               "tag": ("eks", arn) if arn else None, "price": price}]


def _cache_engine(*values):
    for v in values:
        if v:
            return str(v).lower()
    return "redis"


def parse_create_cache_cluster(d, region, account):
    req, resp = d.get("requestParameters") or {}, d.get("responseElements") or {}
    cid = resp.get("cacheClusterId") or req.get("cacheClusterId")
    if not cid:
        return "ElastiCache", []
    node = resp.get("cacheNodeType") or req.get("cacheNodeType")
    count = int(resp.get("numCacheNodes") or req.get("numCacheNodes") or 1)
    arn = resp.get("aRN") or resp.get("arn") or _arn("elasticache", region, account, f"cluster:{cid}")
    price = ("elasticache", {"node_type": node, "count": count,
                             "engine": _cache_engine(resp.get("engine"), req.get("engine"))}) if node else None
    return "ElastiCache", [{"id": cid, "tag": ("elasticache", arn), "price": price}]


def parse_create_replication_group(d, region, account):
    req, resp = d.get("requestParameters") or {}, d.get("responseElements") or {}
    gid = resp.get("replicationGroupId") or req.get("replicationGroupId")
    if not gid:
        return "ElastiCache", []
    node = resp.get("cacheNodeType") or req.get("cacheNodeType")
    members = resp.get("memberClusters") or []
    if members:
        count = len(members)
    elif req.get("numCacheClusters"):
        count = int(req["numCacheClusters"])
    else:
        count = int(req.get("numNodeGroups") or 1) * (int(req.get("replicasPerNodeGroup") or 0) + 1)
    arn = resp.get("aRN") or resp.get("arn") or _arn(
        "elasticache", region, account, f"replicationgroup:{gid}")
    price = ("elasticache", {"node_type": node, "count": count,
                             "engine": _cache_engine(resp.get("engine"), req.get("engine"))}) if node else None
    return "ElastiCache", [{"id": gid, "tag": ("elasticache", arn), "price": price}]


def parse_create_serverless_cache(d, region, account):
    sc = _get(d, "responseElements", "serverlessCache") or {}
    name = sc.get("serverlessCacheName") or _get(d, "requestParameters", "serverlessCacheName")
    if not name:
        return "ElastiCache Serverless", []
    arn = sc.get("aRN") or sc.get("arn") or _arn(
        "elasticache", region, account, f"serverlesscache:{name}")
    # Serverless tính phí theo dung lượng + ECPU: pricing.py chưa hỗ trợ
    return "ElastiCache Serverless", [{"id": name, "tag": ("elasticache", arn), "price": None}]


# (eventSource, eventName) -> (hàm parse, là sự kiện tạo mới?)
HANDLERS = {
    ("ec2.amazonaws.com", "RunInstances"): (parse_run_instances, True),
    ("ec2.amazonaws.com", "StartInstances"): (parse_start_instances, False),
    ("ec2.amazonaws.com", "CreateNatGateway"): (parse_create_nat_gateway, True),
    ("ec2.amazonaws.com", "AllocateAddress"): (parse_allocate_address, True),
    ("rds.amazonaws.com", "CreateDBInstance"): (parse_create_db_instance, True),
    ("rds.amazonaws.com", "StartDBInstance"): (parse_start_db_instance, False),
    ("elasticloadbalancing.amazonaws.com", "CreateLoadBalancer"): (parse_create_load_balancer, True),
    ("eks.amazonaws.com", "CreateCluster"): (parse_create_eks_cluster, True),
    ("eks.amazonaws.com", "CreateNodegroup"): (parse_create_nodegroup, True),
    ("elasticache.amazonaws.com", "CreateCacheCluster"): (parse_create_cache_cluster, True),
    ("elasticache.amazonaws.com", "CreateReplicationGroup"): (parse_create_replication_group, True),
    ("elasticache.amazonaws.com", "CreateServerlessCache"): (parse_create_serverless_cache, True),
}


# =============================================================== gắn tag
def _error_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code") or type(exc).__name__


def apply_tags(resources, region, tags):
    """Gắn `tags` ({key: value}) lên các tài nguyên.

    Trả về (số lần gọi API gắn tag, danh sách mã lỗi). EC2 gom mọi ID vào một lần gọi.
    """
    calls, errors = 0, []
    ec2_ids = [r["tag"][1] for r in resources if r.get("tag") and r["tag"][0] == "ec2"]
    if ec2_ids:                                            # EC2 gắn nhiều ID một lần
        calls += 1
        try:
            _client("ec2", region).create_tags(
                Resources=ec2_ids, Tags=[{"Key": k, "Value": v} for k, v in tags.items()])
        except Exception as e:  # noqa: BLE001
            logger.warning("create_tags %s thất bại: %s", ec2_ids, e)
            errors.append(_error_code(e))

    for r in resources:
        if not r.get("tag") or r["tag"][0] == "ec2" or not r["tag"][1]:
            continue
        service, ref = r["tag"]
        calls += 1
        pairs = [{"Key": k, "Value": v} for k, v in tags.items()]
        try:
            c = _client(service, region)
            if service == "rds":
                c.add_tags_to_resource(ResourceName=ref, Tags=pairs)
            elif service == "elbv2":
                c.add_tags(ResourceArns=[ref], Tags=pairs)
            elif service == "elb":
                c.add_tags(LoadBalancerNames=[ref], Tags=pairs)
            elif service == "eks":
                c.tag_resource(resourceArn=ref, tags=dict(tags))
            elif service == "elasticache":
                c.add_tags_to_resource(ResourceName=ref, Tags=pairs)
        except Exception as e:  # noqa: BLE001
            logger.warning("Gắn tag %s %s thất bại: %s", service, ref, e)
            errors.append(_error_code(e))
    return calls, errors


def tag_line(calls, tags, kept_owner, errors):
    """Giá trị dòng 'Tag đã gắn'. calls = None nghĩa là dry_run (chưa gắn thật)."""
    if calls == 0:
        return "không gắn được (không xác định được tài nguyên)"
    if calls and len(errors) >= calls:
        return f"không gắn được ({', '.join(sorted(set(errors)))})"
    text = ", ".join(f"{k}={v}" for k, v in tags.items()) + " (tự động)"
    if kept_owner is not None:
        text += f"; giữ nguyên Owner={kept_owner} do người tạo gắn"
    if errors:
        text += f"; lỗi một phần: {', '.join(sorted(set(errors)))}"
    return text


# =============================================================== ước tính chi phí
def _lookup_price(kind, ident, region):
    """Sự kiện bật lại không có loại máy trong phản hồi -> hỏi Describe (chỉ đọc)."""
    try:
        if kind == "ec2":
            resp = _client("ec2", region).describe_instances(InstanceIds=[ident])
            for res in resp.get("Reservations", []):
                for inst in res.get("Instances", []):
                    return ("ec2", {"instance_type": inst["InstanceType"]})
        if kind == "rds":
            resp = _client("rds", region).describe_db_instances(DBInstanceIdentifier=ident)
            db = resp["DBInstances"][0]
            return _rds_price(db.get("DBInstanceClass"), db.get("Engine"), db.get("MultiAZ"))
    except Exception as e:  # noqa: BLE001
        logger.warning("Không tra được loại máy %s %s: %s", kind, ident, e)
    return None


def estimate_total(resources, region):
    """Tổng USD/giờ của các tài nguyên. -> (tổng hoặc None, số mục không tra được giá)."""
    total, missing = 0.0, 0
    for r in resources:
        price = r.get("price")
        if price is None and r.get("lookup"):
            price = _lookup_price(*r["lookup"], region)
        found = estimate(price[0], region, **price[1]) if price else None
        if found is None:
            missing += 1
        else:
            total += found["hourly"]
    if missing == len(resources):
        return None, missing
    return total, missing


def money_line(hourly, missing):
    if hourly is None:
        return fmt_money(None)
    text = f"{fmt_money(hourly)} (giá On-Demand)"
    if missing:
        text += f"; chưa tính {missing} mục không tra được giá"
    return text


# =============================================================== hàm chính
def _skip(reason, **extra):
    logger.info("Bỏ qua: %s", reason)
    return {"status": "skipped", "reason": reason, **extra}


def lambda_handler(event, context):
    print("EVENT:", json.dumps(event, default=str))
    d = event.get("detail") or {}
    name, source = d.get("eventName"), d.get("eventSource")
    dry_run = bool(event.get("dry_run"))

    if d.get("errorCode"):                                   # pattern đã lọc; phòng hờ
        return _skip(f"lệnh lỗi {d['errorCode']}", event=name)
    ui = d.get("userIdentity") or {}
    if AUTOSCALING_PRINCIPAL in (ui.get("invokedBy"), d.get("sourceIPAddress")):
        return _skip("do Auto Scaling tự tạo", event=name)
    if (source, name) not in HANDLERS:
        return _skip("sự kiện không thuộc danh sách cảnh báo", event=name, source=source)

    parse, is_create = HANDLERS[(source, name)]
    region = d.get("awsRegion") or event.get("region") or "us-east-1"
    account = d.get("recipientAccountId") or event.get("account") or ""

    try:
        label, resources = parse(d, region, account)
    except Exception:  # noqa: BLE001 – lỗi bóc tách không được chặn cảnh báo
        logger.exception("Không bóc tách được %s", name)
        label, resources = "Không rõ", []
    resources = [r for r in resources if r.get("id")]
    ids = [r["id"] for r in resources]

    owner, created_by, actor_shown = actor_info(ui)

    # ---- gắn tag (chỉ sự kiện tạo mới)
    tag_text, tags, tag_errors = None, {}, []
    if is_create:
        preset = request_tags(d.get("requestParameters"))
        kept_owner = preset.get(OWNER)
        tags = {CREATED_BY: created_by} if kept_owner is not None else {OWNER: owner, CREATED_BY: created_by}
        if dry_run:
            calls = None if any(r.get("tag") and r["tag"][1] for r in resources) else 0
        else:
            calls, tag_errors = apply_tags(resources, region, tags)
        tag_text = tag_line(calls, tags, kept_owner, tag_errors)

    # ---- ước tính chi phí
    hourly, missing = estimate_total(resources, region) if resources else (None, 0)

    # ---- email
    restart = not is_create
    subject_type = f"{label} (bật lại)" if restart else label
    lines = field_lines([
        ("Loại cảnh báo", ALERT_KIND_TEXT),
        ("Tài nguyên", f"{label}  {', '.join(ids) if ids else '(không xác định được ID)'}"),
        ("Sự kiện", name),
        ("Người thực hiện", actor_shown),
        ("Thời điểm", fmt_time(d["eventTime"]) if d.get("eventTime") else None),
        ("Region", region),
        ("Tag đã gắn", tag_text),
        ("Ước tính", money_line(hourly, missing)),
        ("Gợi ý", SUGGESTION),
    ])
    result = send("ALERT", f"Tai nguyen tinh phi moi: {subject_type}", lines, dry_run=dry_run)

    summary = {
        "status": "dry_run" if dry_run else "sent",
        "event": name,
        "region": region,
        "resource_type": label,
        "ids": ids,
        "tags": tags,
        "tag_errors": tag_errors,
        "hourly_usd": None if hourly is None else round(hourly, 6),
        "subject": result["subject"],
        "message_id": result.get("message_id"),
    }
    if dry_run:
        summary["message"] = result["message"]
    print("RESULT:", json.dumps(summary, ensure_ascii=False))
    return summary
