import json

import pytest
from botocore.stub import ANY, Stubber

import pricing


def product(usd, unit="Hrs", usagetype="BoxUsage:t3.small"):
    """Tạo một phần tử PriceList giống định dạng thật của Price List API."""
    return json.dumps({
        "product": {"attributes": {"usagetype": usagetype}},
        "terms": {"OnDemand": {"T1": {"priceDimensions": {"D1": {
            "unit": unit, "pricePerUnit": {"USD": str(usd)}}}}}},
    })


@pytest.fixture(autouse=True)
def _reset():
    pricing.clear_cache()
    yield
    pricing.clear_cache()


def stub_products(stub, *pages):
    for i, items in enumerate(pages):
        resp = {"PriceList": items, "FormatVersion": "aws_v1"}
        if i < len(pages) - 1:
            resp["NextToken"] = f"t{i}"
        stub.add_response("get_products", resp, {
            "ServiceCode": ANY, "Filters": ANY, "FormatVersion": "aws_v1", "MaxResults": 100,
            **({"NextToken": f"t{i-1}"} if i else {})})


def test_ec2_gia_theo_gio_va_thang():
    c = pricing._client()
    with Stubber(c) as stub:
        stub_products(stub, [product("0.0208")])
        r = pricing.estimate("ec2", "us-east-1", instance_type="t3.small")
    assert r["hourly"] == 0.0208 and r["monthly"] == round(0.0208 * 730, 4)


def test_luu_dem_chi_goi_api_mot_lan():
    c = pricing._client()
    with Stubber(c) as stub:
        stub_products(stub, [product("0.0208")])
        a = pricing.estimate("ec2", "us-east-1", instance_type="t3.small")
        b = pricing.estimate("ec2", "us-east-1", instance_type="t3.small", count=2)
        stub.assert_no_pending_responses()
    assert b["hourly"] == pytest.approx(a["hourly"] * 2)


def test_nat_loc_theo_usagetype_qua_nhieu_trang():
    c = pricing._client()
    with Stubber(c) as stub:
        stub_products(stub,
                      [product("0.059", unit="GB", usagetype="APS1-NatGateway-Bytes")],
                      [product("0.059", usagetype="APS1-NatGateway-Hours")])
        r = pricing.estimate("nat", "ap-southeast-1")
    assert r["unit"] == "Hrs" and r["monthly"] == round(0.059 * 730, 4)


def test_ebs_gia_theo_gb_thang():
    c = pricing._client()
    with Stubber(c) as stub:
        stub_products(stub, [product("0.08", unit="GB-Mo", usagetype="EBS:VolumeUsage.gp3")])
        r = pricing.estimate("ebs", "us-east-1", size_gb=30)
    assert r["monthly"] == pytest.approx(2.4) and r["hourly"] == pytest.approx(2.4 / 730, rel=1e-3)


def test_loi_api_tra_none_va_khong_luu_dem():
    c = pricing._client()
    with Stubber(c) as stub:
        stub.add_client_error("get_products", "ThrottlingException")
        assert pricing.estimate("ec2", "us-east-1", instance_type="t3.small") is None
        stub_products(stub, [product("0.0208")])
        assert pricing.estimate("ec2", "us-east-1", instance_type="t3.small") is not None


@pytest.mark.parametrize("args", [
    ("s3", {}),                                   # loại chưa hỗ trợ
    ("ec2", {}),                                  # thiếu instance_type
    ("rds", {"instance_class": "db.t3.micro", "engine": "oracle-ee"}),   # engine chưa hỗ trợ
])
def test_khong_ho_tro_hoac_thieu_tham_so_tra_none(args):
    assert pricing.estimate(args[0], "us-east-1", **args[1]) is None


def test_khong_tim_thay_gia_tra_none():
    c = pricing._client()
    with Stubber(c) as stub:
        stub_products(stub, [])
        assert pricing.estimate("eks", "us-east-1") is None
