"""
pricing.py – Ước tính chi phí qua AWS Price List API (dùng chung cho ALERT và DAILY)
===================================================================================

Cách dùng:

    from pricing import estimate
    from notify import fmt_money

    gia = estimate("ec2", "us-east-1", instance_type="t3.small")
    # -> {"hourly": 0.0208, "monthly": 15.184, "unit_price": 0.0208, "unit": "Hrs"}  hoặc None
    print(fmt_money(gia["hourly"] if gia else None))

Các loại tài nguyên hỗ trợ và tham số cần truyền:

    | resource_type | tham số bắt buộc            | tham số tùy chọn                     |
    |---------------|-----------------------------|--------------------------------------|
    | ec2           | instance_type               | count, platform (vd "Windows")       |
    | rds           | instance_class              | engine (mặc định mysql), multi_az    |
    | nat           | –                           | count                                |
    | eip           | –                           | count                                |
    | alb, nlb, gwlb| –                           | count                                |
    | vpce          | –                           | count (= số AZ/subnet của endpoint)  |
    | eks           | –                           | count                                |
    | elasticache   | node_type                   | engine (mặc định redis), count       |
    | ebs           | size_gb                     | volume_type (mặc định gp3)           |
    | snapshot      | size_gb                     | (snapshot EBS)                       |
    | rds_snapshot  | size_gb                     | (snapshot RDS thủ công / cluster)    |

    platform của ec2 lấy từ PlatformDetails của EC2 (mặc định "Linux/UNIX"). Hệ điều hành có
    kèm SQL Server chưa hỗ trợ -> trả None.

Quy tắc:
  - Lấy giá On-Demand. Tháng = giờ × 730.
  - KHÔNG BAO GIỜ ném lỗi ra ngoài: tra không được (loại chưa hỗ trợ, thiếu tham số,
    API lỗi, không tìm thấy giá) -> trả về None. Email ghi "không ước tính được".
  - Kết quả được lưu đệm trong bộ nhớ: nhiều máy cùng loại chỉ tra giá một lần.

Quyền IAM cần cho Lambda gọi file này:   pricing:GetProducts   (Resource "*")
Price List API chỉ có endpoint ở us-east-1 (và vài Region khác), nên client luôn
tạo ở us-east-1, bất kể Lambda chạy ở đâu và tài nguyên nằm ở Region nào.

⚠ Bộ lọc cho ec2 và rds theo đúng tài liệu AWS. Bộ lọc cho các loại còn lại
  là bản dự kiến, CẦN KIỂM CHỨNG trên AWS thật bằng scripts/check_pricing.py
  (thuộc hạng mục 18 – mô-đun ước tính chi phí).

Giai đoạn 1 (thử trên console): tạo file pricing.py ngang hàng lambda_function.py,
dán toàn bộ nội dung file này vào, thêm quyền pricing:GetProducts cho role của Lambda.
"""
import json
import logging

import boto3

logger = logging.getLogger(__name__)

HOURS_PER_MONTH = 730
PRICING_REGION = "us-east-1"
MAX_PAGES = 10                     # tối đa 10 trang × 100 sản phẩm cho một lần tra

RDS_ENGINES = {
    "mysql": "MySQL",
    "postgres": "PostgreSQL",
    "mariadb": "MariaDB",
    "aurora-mysql": "Aurora MySQL",
    "aurora-postgresql": "Aurora PostgreSQL",
}
CACHE_ENGINES = {"redis": "Redis", "valkey": "Valkey", "memcached": "Memcached"}
LB_FAMILIES = {
    "alb": "Load Balancer-Application",
    "nlb": "Load Balancer-Network",
    "gwlb": "Load Balancer-Gateway",
}

_pricing = None
_cache = {}


def _client():
    global _pricing
    if _pricing is None:
        _pricing = boto3.client("pricing", region_name=PRICING_REGION)
    return _pricing


def clear_cache():
    """Xóa bộ nhớ đệm (dùng khi test)."""
    _cache.clear()


# ----------------------------------------------------------------------- tra giá thô
def _on_demand_price(product):
    """Lấy (giá USD, đơn vị) đầu tiên lớn hơn 0 trong điều khoản On-Demand của một sản phẩm."""
    for term in product.get("terms", {}).get("OnDemand", {}).values():
        for dim in term.get("priceDimensions", {}).values():
            usd = float(dim.get("pricePerUnit", {}).get("USD", "0") or 0)
            if usd > 0:
                return usd, dim.get("unit", "")
    return None


def lookup_price(service_code, filters, usagetype_endswith=None):
    """Tra giá On-Demand của sản phẩm đầu tiên khớp bộ lọc.

    service_code         vd "AmazonEC2", "AmazonRDS"
    filters              dict thuộc tính -> giá trị (so khớp tuyệt đối, TERM_MATCH)
    usagetype_endswith   lọc thêm theo đuôi trường usagetype, vì usagetype có tiền tố
                         Region thay đổi theo nơi (vd 'APS1-NatGateway-Hours')

    Trả về (giá USD, đơn vị) hoặc None.
    """
    key = (service_code, tuple(sorted(filters.items())), usagetype_endswith)
    if key in _cache:
        return _cache[key]

    kwargs = {
        "ServiceCode": service_code,
        "Filters": [{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in filters.items()],
        "FormatVersion": "aws_v1",
        "MaxResults": 100,
    }
    result = None
    try:
        for _ in range(MAX_PAGES):
            resp = _client().get_products(**kwargs)
            for raw in resp.get("PriceList", []):
                product = json.loads(raw) if isinstance(raw, str) else raw
                usagetype = product.get("product", {}).get("attributes", {}).get("usagetype", "")
                if usagetype_endswith and not usagetype.endswith(usagetype_endswith):
                    continue
                result = _on_demand_price(product)
                if result:
                    break
            if result or not resp.get("NextToken"):
                break
            kwargs["NextToken"] = resp["NextToken"]
    except Exception as e:  # noqa: BLE001 – lỗi tra giá không được làm hỏng email
        logger.warning("Không tra được giá %s %s: %s", service_code, filters, e)
        return None          # lỗi tạm thời -> không lưu đệm, lần sau thử lại

    _cache[key] = result     # lưu cả trường hợp "không tìm thấy" để khỏi tra lại
    return result


# ----------------------------------------------------------------------- ước tính theo loại
def _os_name(platform):
    """PlatformDetails của EC2 -> giá trị operatingSystem của Price List; None nếu chưa hỗ trợ."""
    p = platform or "Linux/UNIX"
    if "SQL" in p:
        return None                      # có kèm SQL Server: bộ lọc preInstalledSw khác, chưa hỗ trợ
    if "Windows" in p:
        return "Windows"
    if "Red Hat" in p:
        return "RHEL"
    if "SUSE" in p:
        return "SUSE"
    if "Ubuntu Pro" in p:
        return "Ubuntu Pro"
    return "Linux"


def _query(resource_type, region, attrs):
    """Trả về (service_code, filters, usagetype_endswith) cho từng loại tài nguyên, hoặc None."""
    rt = resource_type.lower()
    if rt == "ec2":
        os_name = _os_name(attrs.get("platform"))
        if os_name is None:
            return None
        return "AmazonEC2", {
            "regionCode": region,
            "instanceType": attrs["instance_type"],
            "operatingSystem": os_name,
            "tenancy": "Shared",
            "preInstalledSw": "NA",
            "capacitystatus": "Used",
        }, None
    if rt == "rds":
        engine = RDS_ENGINES.get(str(attrs.get("engine", "mysql")).lower())
        if not engine:
            return None
        f = {"regionCode": region, "instanceType": attrs["instance_class"], "databaseEngine": engine}
        if not engine.startswith("Aurora"):
            f["deploymentOption"] = "Multi-AZ" if attrs.get("multi_az") else "Single-AZ"
        return "AmazonRDS", f, None
    if rt == "nat":
        return "AmazonEC2", {"regionCode": region, "productFamily": "NAT Gateway"}, "NatGateway-Hours"
    if rt == "eip":
        return "AmazonVPC", {"regionCode": region}, "PublicIPv4:IdleAddress"
    if rt in LB_FAMILIES:
        return "AmazonEC2", {
            "regionCode": region, "productFamily": LB_FAMILIES[rt]}, "LoadBalancerUsage"
    if rt == "vpce":     # Interface Endpoint: giá theo giờ cho MỖI AZ -> truyền count = số AZ
        return "AmazonVPC", {"regionCode": region, "productFamily": "VpcEndpoint"}, "VpcEndpoint-Hours"
    if rt == "eks":
        return "AmazonEKS", {"regionCode": region}, "AmazonEKS-Hours:perCluster"
    if rt == "elasticache":
        engine = CACHE_ENGINES.get(str(attrs.get("engine", "redis")).lower())
        if not engine:
            return None
        return "AmazonElastiCache", {
            "regionCode": region, "instanceType": attrs["node_type"], "cacheEngine": engine}, None
    if rt == "ebs":
        return "AmazonEC2", {
            "regionCode": region, "productFamily": "Storage",
            "volumeApiName": attrs.get("volume_type", "gp3")}, None
    if rt == "snapshot":
        return "AmazonEC2", {"regionCode": region, "productFamily": "Storage Snapshot"}, "EBS:SnapshotUsage"
    if rt == "rds_snapshot":
        return "AmazonRDS", {"regionCode": region, "productFamily": "Storage Snapshot"}, "ChargedBackupUsage"
    return None


def estimate(resource_type, region, **attrs):
    """Ước tính chi phí của một tài nguyên.

    Trả về {"hourly": USD/giờ, "monthly": USD/tháng, "unit_price": giá gốc, "unit": đơn vị}
    hoặc None nếu không ước tính được.
    """
    try:
        query = _query(resource_type, region, attrs)
    except KeyError as e:
        logger.warning("estimate(%s): thiếu tham số %s", resource_type, e)
        return None
    if query is None:
        return None

    found = lookup_price(*query)
    if found is None:
        return None
    usd, unit = found

    if unit.lower().startswith("gb-mo"):          # lưu trữ: giá theo GB mỗi tháng
        size = attrs.get("size_gb")
        if not size:
            return None
        monthly = usd * float(size)
        hourly = monthly / HOURS_PER_MONTH
    else:                                          # giá theo giờ
        hourly = usd * int(attrs.get("count", 1))
        monthly = hourly * HOURS_PER_MONTH

    return {"hourly": round(hourly, 6), "monthly": round(monthly, 4), "unit_price": usd, "unit": unit}