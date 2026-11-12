"""Kiểm thử hạng mục 22 bằng Stubber: không gọi AWS thật."""
import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import ANY, Stubber

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("idle_under_test", ROOT / "lambdas/daily_scan/idle.py")
idle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(idle)
FILTER = {"Filters": [{"Name": "instance-state-name", "Values": ["running"]}]}


def load_case():
    """Đổi thời gian JSON thành datetime đúng kiểu boto3."""
    data = json.loads((ROOT / "test_events/scan/fixtures/idle_cpu_c1.json").read_text(encoding="utf-8"))
    data["now"] = datetime.fromisoformat(data["now"].replace("Z", "+00:00"))
    for instance in data["ec2"]["Reservations"][0]["Instances"]:
        instance["LaunchTime"] = datetime.fromisoformat(instance["LaunchTime"].replace("Z", "+00:00"))
    for metric in data["cloudwatch"]["MetricDataResults"]:
        metric["Timestamps"] = [datetime.fromisoformat(x.replace("Z", "+00:00")) for x in metric["Timestamps"]]
    return data


def client(service):
    return boto3.client(service, region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")


def run_case(case, cw_pages=None, ec2_pages=None):
    """Stub cả inventory lẫn metrics; kiểm tra request query có đúng 2 máy A/C."""
    ec2, cw = client("ec2"), client("cloudwatch")
    with Stubber(ec2) as es, Stubber(cw) as cs:
        if ec2_pages:
            for i, page in enumerate(ec2_pages):
                es.add_response("describe_instances", page, {**FILTER, **({"NextToken": "p2"} if i else {})})
        else:
            es.add_response("describe_instances", case["ec2"], FILTER)
        for i, response in enumerate(cw_pages or [case["cloudwatch"]]):
            cs.add_response("get_metric_data", response, {
                "MetricDataQueries": ANY, "StartTime": case["now"] - timedelta(days=7),
                "EndTime": case["now"], "ScanBy": "TimestampAscending",
                **({"NextToken": "p2"} if i else {})})
        result = idle.find_idle_instances(ec2, cw, case["now"], case["cpu_threshold"], case["days"])
        es.assert_no_pending_responses()
        cs.assert_no_pending_responses()
    return result


def test_c1_chi_may_a_cpu_thap_du_bay_ngay():
    case = load_case()
    result = run_case(case)
    assert [x["instance_id"] for x in result] == case["expected_instance_ids"]
    assert set(result[0]) == {"instance_id", "instance_type", "avg_cpu", "tags"}
    assert result[0]["avg_cpu"] == pytest.approx(11 / 7)
    assert result[0]["tags"][0] == {"Key": "Owner", "Value": "Chien"}


def test_thieu_ngay_khong_coi_la_cpu_khong():
    case = load_case()
    metric = case["cloudwatch"]["MetricDataResults"][0]
    metric["Timestamps"].pop()
    metric["Values"].pop()
    assert run_case(case) == []


def test_mot_ngay_bang_nguong_khong_phai_idle():
    case = load_case()
    case["cloudwatch"]["MetricDataResults"][0]["Values"][2] = 5
    assert run_case(case) == []


def test_partial_data_khong_ket_luan():
    case = load_case()
    case["cloudwatch"]["MetricDataResults"][0]["StatusCode"] = "PartialData"
    assert run_case(case) == []


def test_ec2_va_metrics_deu_co_phan_trang():
    case = load_case()
    instances = case["ec2"]["Reservations"][0]["Instances"]
    metrics = case["cloudwatch"]["MetricDataResults"]
    assert run_case(case,
        ec2_pages=[{"Reservations": [{"Instances": instances[:1]}], "NextToken": "p2"},
                   {"Reservations": [{"Instances": instances[1:]}]}],
        cw_pages=[{"MetricDataResults": metrics[:1], "NextToken": "p2"}, {"MetricDataResults": metrics[1:]}])


def test_metrics_cua_cung_may_duoc_ghep_qua_nhieu_trang():
    case = load_case()
    a, c = case["cloudwatch"]["MetricDataResults"]
    first, second = copy.deepcopy(a), copy.deepcopy(a)
    first.update(Timestamps=a["Timestamps"][:3], Values=a["Values"][:3], StatusCode="PartialData")
    second.update(Timestamps=a["Timestamps"][3:], Values=a["Values"][3:])
    result = run_case(case, cw_pages=[{"MetricDataResults": [first], "NextToken": "p2"},
                                    {"MetricDataResults": [second, c]}])
    assert len(result) == 1


def test_may_stopped_khong_goi_cloudwatch():
    case = load_case()
    for obj in case["ec2"]["Reservations"][0]["Instances"]:
        obj["State"] = {"Name": "stopped", "Code": 80}
    ec2, cw = client("ec2"), Mock()
    with Stubber(ec2) as stub:
        stub.add_response("describe_instances", case["ec2"], FILTER)
        assert idle.find_idle_instances(ec2, cw, case["now"], 5, 7) == []
    cw.get_metric_data.assert_not_called()


def test_loi_api_duoc_day_ve_daily():
    case = load_case()
    ec2, cw = client("ec2"), client("cloudwatch")
    with Stubber(ec2) as es, Stubber(cw) as cs:
        es.add_response("describe_instances", case["ec2"], FILTER)
        cs.add_client_error("get_metric_data", "AccessDenied", "no permission")
        with pytest.raises(Exception, match="AccessDenied"):
            idle.find_idle_instances(ec2, cw, case["now"], 5, 7)


def test_gom_500_query_va_dung_average_tung_ngay():
    case = load_case()
    template = case["ec2"]["Reservations"][0]["Instances"][0]
    ec2, cw = Mock(), Mock()
    instances = [{**template, "InstanceId": f"i-{i:017x}"} for i in range(501)]
    ec2.get_paginator.return_value.paginate.return_value = [{"Reservations": [{"Instances": instances}]}]
    cw.get_metric_data.return_value = {"MetricDataResults": []}
    assert idle.find_idle_instances(ec2, cw, case["now"], 5, 7) == []
    assert [len(x.kwargs["MetricDataQueries"]) for x in cw.get_metric_data.call_args_list] == [500, 1]
    stat = cw.get_metric_data.call_args_list[0].kwargs["MetricDataQueries"][0]["MetricStat"]
    assert stat["Period"] == 86400 and stat["Stat"] == "Average"
    assert stat["Metric"]["Namespace"] == "AWS/EC2"


def test_c1_khong_gui_query_cho_may_b_moi_ba_ngay():
    case = load_case()
    ec2, cw = Mock(), Mock()
    ec2.get_paginator.return_value.paginate.return_value = [case["ec2"]]
    cw.get_metric_data.return_value = case["cloudwatch"]
    result = idle.find_idle_instances(ec2, cw, case["now"], 5, 7)
    queries = cw.get_metric_data.call_args.kwargs["MetricDataQueries"]
    ids = [q["MetricStat"]["Metric"]["Dimensions"][0]["Value"] for q in queries]
    assert ids == ["i-0000000000000000a", "i-0000000000000000c"]
    assert [x["instance_id"] for x in result] == case["expected_instance_ids"]


@pytest.mark.parametrize("days,threshold", [(0, 5), (True, 5), (7, 0), (7, 100), (7, float("nan"))])
def test_cau_hinh_sai_bi_tu_choi(days, threshold):
    with pytest.raises(ValueError):
        idle.find_idle_instances(Mock(), Mock(), datetime.now(timezone.utc), threshold, days)


def test_cac_moc_thoi_gian_phai_co_mui_gio():
    with pytest.raises(ValueError):
        idle.find_idle_instances(Mock(), Mock(), datetime(2026, 11, 12), 5, 7)
