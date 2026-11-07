"""Test offline cho Lambda cg-chargeable-event (không gọi AWS thật).

- Gắn tag / Describe: giả lập bằng botocore Stubber, kiểm tra đúng tham số gửi đi.
- Tra giá: thay pricing.estimate bằng bảng giá giả (giá thật kiểm tra bằng scripts/check_pricing.py).
- Gửi email: dùng notify.send ở chế độ dry_run để kiểm tra đúng tiêu đề và nội dung.
"""
import json
import os
import sys

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "chargeable_event"))

import lambda_function as lf  # noqa: E402
import notify  # noqa: E402

EVENTS = os.path.join(ROOT, "test_events", "chargeable_event")
PATTERN = os.path.join(ROOT, "lambdas", "chargeable_event", "event_pattern.json")

FAKE_PRICES = {  # USD/giờ – chỉ để test phép cộng, không phải giá thật
    ("ec2", "t3.micro"): 0.0104, ("ec2", "t3.small"): 0.0208, ("ec2", "t3.medium"): 0.0416,
    ("rds", "db.t3.micro"): 0.017, ("elasticache", "cache.t3.micro"): 0.017,
    ("nat", None): 0.045, ("eip", None): 0.005, ("alb", None): 0.0225, ("eks", None): 0.10,
}


def load(name):
    with open(os.path.join(EVENTS, name), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ môi trường giả
class Env:
    def __init__(self):
        self.clients, self.stubs, self.sent, self.client_calls = {}, {}, [], []

    def stub(self, service, region="us-east-1"):
        key = (service, region)
        if key not in self.clients:
            c = boto3.client(service, region_name=region)
            s = Stubber(c)
            s.activate()
            self.clients[key], self.stubs[key] = c, s
        return self.stubs[key]

    def client(self, service, region):
        self.client_calls.append((service, region))
        self.stub(service, region)
        return self.clients[(service, region)]


@pytest.fixture
def env(monkeypatch):
    e = Env()
    monkeypatch.setattr(lf, "_client", e.client)

    def fake_estimate(rt, region, **attrs):
        key = attrs.get("instance_type") or attrs.get("instance_class") or attrs.get("node_type")
        hourly = FAKE_PRICES.get((rt, key))
        return None if hourly is None else {"hourly": hourly * int(attrs.get("count", 1))}
    monkeypatch.setattr(lf, "estimate", fake_estimate)

    def fake_send(kind, content, lines, dry_run=False):
        r = notify.send(kind, content, lines, dry_run=True)
        e.sent.append(r)
        return {**r, "sent": not dry_run, "message_id": None if dry_run else "msg-1"}
    monkeypatch.setattr(lf, "send", fake_send)
    yield e
    for s in e.stubs.values():
        s.assert_no_pending_responses()


def tags(owner, created_by):
    out = []
    if owner is not None:
        out.append({"Key": "Owner", "Value": owner})
    out.append({"Key": "CreatedBy", "Value": created_by})
    return out


def field(message, label):
    for line in message.splitlines():
        if line.startswith(label):
            return line.split(":", 1)[1].strip()
    return None


# ------------------------------------------------------------------ event pattern (kiểm tra sơ bộ)
def _match(pattern, data):
    """Bộ so khớp tối giản cho các toán tử dùng trong event_pattern.json.
    Kiểm tra chính thức vẫn là nút Test pattern trên console EventBridge."""
    for key, rule in pattern.items():
        if key == "$or":
            if not any(_match(sub, data) for sub in rule):
                return False
            continue
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(rule, dict):
            if not isinstance(value, dict) or not _match(rule, value):
                return False
            continue
        ok = False
        for r in rule:
            if isinstance(r, dict) and "exists" in r:
                ok = ok or ((key in data) == r["exists"])
            elif value == r:
                ok = True
        if not ok:
            return False
    return True


MATCH = [f for f in sorted(os.listdir(EVENTS)) if f[0].isdigit()] + ["neg_04_run_instances_by_autoscaling.json"]
NO_MATCH = ["neg_01_run_instances_failed.json", "neg_02_terminate_instances.json",
            "neg_03_describe_instances.json", "neg_05_ecs_create_cluster.json"]


@pytest.mark.parametrize("name", MATCH)
def test_pattern_khop(name):
    assert _match(json.load(open(PATTERN)), load(name))


@pytest.mark.parametrize("name", NO_MATCH)
def test_pattern_khong_khop(name):
    assert not _match(json.load(open(PATTERN)), load(name))


# ------------------------------------------------------------------ các sự kiện tạo mới
def test_01_run_instances_gan_tag_va_dung_thu_tu_truong(env):
    env.stub("ec2").add_response("create_tags", {}, {
        "Resources": ["i-0d41f3e7a68c2b915"], "Tags": tags("cg-tv2", "user/cg-tv2")})
    r = lf.lambda_handler(load("01_run_instances.json"), None)

    assert r["status"] == "sent" and r["ids"] == ["i-0d41f3e7a68c2b915"]
    mail = env.sent[0]
    assert mail["subject"] == "[Cost-Guardian][ALERT] Tai nguyen tinh phi moi: EC2"
    labels = [line.split(":", 1)[0].strip() for line in mail["message"].splitlines()]
    assert labels == ["Loại cảnh báo", "Tài nguyên", "Sự kiện", "Người thực hiện", "Thời điểm",
                      "Region", "Tag đã gắn", "Ước tính", "Gợi ý"]
    assert field(mail["message"], "Tài nguyên") == "EC2  i-0d41f3e7a68c2b915"
    assert field(mail["message"], "Tag đã gắn") == "Owner=cg-tv2, CreatedBy=user/cg-tv2 (tự động)"
    assert field(mail["message"], "Thời điểm") == "2026-10-18 10:15:20 (UTC+7)"
    assert field(mail["message"], "Ước tính").startswith("~0,010 USD/giờ")
    assert field(mail["message"], "Ước tính").endswith("(giá On-Demand)")


def test_03_nat_gateway(env):
    env.stub("ec2").add_response("create_tags", {}, {
        "Resources": ["nat-09f1ab82c9e4d1a7b"], "Tags": tags("cg-tv2", "user/cg-tv2")})
    r = lf.lambda_handler(load("03_create_nat_gateway.json"), None)
    assert r["ids"] == ["nat-09f1ab82c9e4d1a7b"]
    assert env.sent[0]["subject"].endswith(": NAT Gateway")


def test_04_elastic_ip(env):
    env.stub("ec2").add_response("create_tags", {}, {
        "Resources": ["eipalloc-0a3f5c7e9b1d24680"], "Tags": tags("cg-tv2", "user/cg-tv2")})
    r = lf.lambda_handler(load("04_allocate_address.json"), None)
    assert r["ids"] == ["eipalloc-0a3f5c7e9b1d24680 (54.210.11.23)"]


def test_05_rds(env):
    env.stub("rds").add_response("add_tags_to_resource", {}, {
        "ResourceName": "arn:aws:rds:us-east-1:123456789012:db:db-lab-mysql",
        "Tags": tags("cg-tv2", "user/cg-tv2")})
    r = lf.lambda_handler(load("05_create_db_instance.json"), None)
    assert r["ids"] == ["db-lab-mysql"] and r["hourly_usd"] == 0.017


def test_07_root_gom_2_may_mot_email(env):
    env.stub("ec2").add_response("create_tags", {}, {
        "Resources": ["i-0aa11bb22cc33dd44", "i-0ee55ff66aa77bb88"], "Tags": tags("root", "root")})
    r = lf.lambda_handler(load("07_run_instances_by_root.json"), None)
    assert len(env.sent) == 1
    assert r["ids"] == ["i-0aa11bb22cc33dd44", "i-0ee55ff66aa77bb88"]
    assert field(env.sent[0]["message"], "Người thực hiện") == "arn:aws:iam::123456789012:root"
    assert r["hourly_usd"] == pytest.approx(2 * 0.0208)


def test_08_load_balancer(env):
    arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/lab-alb/50dc6c495c0c9188"
    env.stub("elbv2").add_response("add_tags", {}, {"ResourceArns": [arn], "Tags": tags("cg-tv2", "user/cg-tv2")})
    r = lf.lambda_handler(load("08_create_load_balancer.json"), None)
    assert r["resource_type"] == "Load Balancer (ALB)" and r["ids"] == ["lab-alb"]
    assert r["hourly_usd"] == 0.0225


def test_09_eks_cluster_nguoi_tao_la_role(env):
    env.stub("eks").add_response("tag_resource", {}, {
        "resourceArn": "arn:aws:eks:us-east-1:123456789012:cluster/lab-eks",
        "tags": {"Owner": "cg-tv3", "CreatedBy": "assumed-role/DevRole/cg-tv3"}})
    r = lf.lambda_handler(load("09_create_eks_cluster.json"), None)
    assert r["ids"] == ["lab-eks"] and r["hourly_usd"] == 0.10


def test_10_eks_nodegroup_gia_theo_so_may(env):
    env.stub("eks").add_response("tag_resource", {}, {
        "resourceArn": "arn:aws:eks:us-east-1:123456789012:nodegroup/lab-eks/lab-ng/4ec5f1e2-7a3b-4c1d-9e8f-0a1b2c3d4e5f",
        "tags": {"Owner": "cg-tv3", "CreatedBy": "assumed-role/DevRole/cg-tv3"}})
    r = lf.lambda_handler(load("10_create_eks_nodegroup.json"), None)
    assert r["ids"] == ["lab-eks/lab-ng"]
    assert r["hourly_usd"] == pytest.approx(2 * 0.0416)


@pytest.mark.parametrize("name, arn, ident, hourly", [
    ("11_create_cache_cluster.json", "arn:aws:elasticache:us-east-1:123456789012:cluster:lab-redis",
     "lab-redis", 0.017),
    ("12_create_replication_group.json",
     "arn:aws:elasticache:us-east-1:123456789012:replicationgroup:lab-valkey", "lab-valkey", 2 * 0.017),
    ("13_create_serverless_cache.json",
     "arn:aws:elasticache:us-east-1:123456789012:serverlesscache:lab-serverless", "lab-serverless", None),
])
def test_11_13_elasticache(env, name, arn, ident, hourly):
    env.stub("elasticache").add_response("add_tags_to_resource", {}, {
        "ResourceName": arn, "Tags": tags("cg-tv2", "user/cg-tv2")})
    r = lf.lambda_handler(load(name), None)
    assert r["ids"] == [ident]
    if hourly is None:
        assert field(env.sent[0]["message"], "Ước tính") == "không ước tính được"
    else:
        assert r["hourly_usd"] == pytest.approx(hourly)


def test_14_region_khac_dung_client_cua_region_do(env):
    env.stub("ec2", "ap-southeast-1").add_response("create_tags", {}, {
        "Resources": ["i-0c5e7a9b1d3f02468"], "Tags": tags("cg-tv2", "user/cg-tv2")})
    r = lf.lambda_handler(load("14_run_instances_other_region.json"), None)
    assert r["region"] == "ap-southeast-1"
    assert env.client_calls == [("ec2", "ap-southeast-1")]
    assert field(env.sent[0]["message"], "Region") == "ap-southeast-1"


def test_15_giu_nguyen_owner_nguoi_tao_da_gan(env):
    env.stub("ec2").add_response("create_tags", {}, {
        "Resources": ["i-0f1e2d3c4b5a69788"], "Tags": tags(None, "user/cg-tv2")})
    r = lf.lambda_handler(load("15_run_instances_owner_preset.json"), None)
    assert r["tags"] == {"CreatedBy": "user/cg-tv2"}
    assert field(env.sent[0]["message"], "Tag đã gắn") == \
        "CreatedBy=user/cg-tv2 (tự động); giữ nguyên Owner=nhom-11 do người tạo gắn"


# ------------------------------------------------------------------ sự kiện bật lại
def test_02_start_instances_khong_gan_tag_tra_loai_may(env):
    env.stub("ec2").add_response(
        "describe_instances",
        {"Reservations": [{"Instances": [{"InstanceId": "i-07c2e9a1f04b3d882", "InstanceType": "t3.micro"}]}]},
        {"InstanceIds": ["i-07c2e9a1f04b3d882"]})
    r = lf.lambda_handler(load("02_start_instances.json"), None)
    mail = env.sent[0]
    assert mail["subject"] == "[Cost-Guardian][ALERT] Tai nguyen tinh phi moi: EC2 (bat lai)"
    assert "Tag đã gắn" not in mail["message"] and r["tags"] == {}
    assert r["hourly_usd"] == 0.0104


def test_06_start_db_instance(env):
    env.stub("rds").add_response(
        "describe_db_instances",
        {"DBInstances": [{"DBInstanceIdentifier": "db-lab-mysql", "DBInstanceClass": "db.t3.micro",
                          "Engine": "mysql", "MultiAZ": False}]},
        {"DBInstanceIdentifier": "db-lab-mysql"})
    r = lf.lambda_handler(load("06_start_db_instance.json"), None)
    assert env.sent[0]["subject"].endswith(": RDS (bat lai)")
    assert r["hourly_usd"] == 0.017


def test_bat_lai_describe_loi_van_gui_email(env):
    env.stub("ec2").add_client_error("describe_instances", "InvalidInstanceID.NotFound")
    r = lf.lambda_handler(load("02_start_instances.json"), None)
    assert r["status"] == "sent"
    assert field(env.sent[0]["message"], "Ước tính") == "không ước tính được"


# ------------------------------------------------------------------ bỏ qua / lỗi / dry_run
def test_neg_04_auto_scaling_bi_bo_qua(env):
    r = lf.lambda_handler(load("neg_04_run_instances_by_autoscaling.json"), None)
    assert r["status"] == "skipped" and env.sent == []


def test_neg_01_lenh_loi_bi_bo_qua(env):
    r = lf.lambda_handler(load("neg_01_run_instances_failed.json"), None)
    assert r["status"] == "skipped" and env.sent == []


def test_gan_tag_that_bai_van_gui_email(env):
    env.stub("ec2").add_client_error("create_tags", "InvalidInstanceID.NotFound")
    r = lf.lambda_handler(load("01_run_instances.json"), None)
    assert r["status"] == "sent" and r["tag_errors"] == ["InvalidInstanceID.NotFound"]
    assert field(env.sent[0]["message"], "Tag đã gắn") == "không gắn được (InvalidInstanceID.NotFound)"


def test_dry_run_khong_gan_tag_khong_gui(env):
    event = dict(load("01_run_instances.json"), dry_run=True)
    r = lf.lambda_handler(event, None)
    assert r["status"] == "dry_run" and r["message_id"] is None
    assert "Owner=cg-tv2" in r["message"]
    assert env.client_calls == []           # không gọi API gắn tag nào


@pytest.mark.parametrize("name", [f for f in sorted(os.listdir(EVENTS)) if f[0].isdigit()])
def test_tieu_de_khong_dau_duoi_100_ky_tu(env, monkeypatch, name):
    monkeypatch.setattr(lf, "_lookup_price", lambda *a: None)   # không gọi Describe trong test này
    event = dict(load(name), dry_run=True)
    r = lf.lambda_handler(event, None)
    assert r["subject"].isascii() and len(r["subject"]) <= 99


def test_request_tags_doc_moi_kieu():
    assert lf.request_tags({"tags": {"Owner": "a"}}) == {"Owner": "a"}                       # EKS
    assert lf.request_tags({"tags": [{"key": "Owner", "value": "b"}]}) == {"Owner": "b"}     # RDS/ELB
    assert lf.request_tags({"CreateNatGatewayRequest": {"TagSpecification": [
        {"ResourceType": "natgateway", "Tag": [{"Key": "Owner", "Value": "c"}]}]}}) == {"Owner": "c"}
    assert lf.request_tags({"tagSpecificationSet": {"items": [
        {"resourceType": "volume", "tags": [{"key": "Owner", "value": "x"}]}]}}) == {}


def test_actor_info():
    assert lf.actor_info({"type": "Root", "arn": "arn:aws:iam::1:root"})[:2] == ("root", "root")
    assert lf.actor_info({"type": "AssumedRole",
                          "arn": "arn:aws:sts::1:assumed-role/R/a,b@x.com"})[:2] == (
        "a_b@x.com", "assumed-role/R/a_b@x.com")         # dấu phẩy không hợp lệ trong tag
