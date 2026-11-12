"""Kiểm thử TTL/Aurora bằng fixture và botocore.Stubber, không gọi AWS thật."""
import importlib.util
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import Stubber

import notify
from load_fixture import load_fixture

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test_events/ttl/fixtures"
SPEC = importlib.util.spec_from_file_location("ttl_under_test", ROOT / "lambdas/ttl/lambda_function.py")
ttl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ttl)
TOPIC = "arn:aws:sns:us-east-1:123456789012:Cost-Guardian"
NOW = "2026-11-12T10:00:00Z"


def client(service):
    return boto3.client(service, region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")


@pytest.fixture
def sns(monkeypatch):
    """Dùng notify.send thật nhưng thay client SNS bằng mock chỉ ghi nhận call."""
    mock = Mock()
    mock.publish.return_value = {"MessageId": "test-message"}
    monkeypatch.setattr(notify, "_sns", mock)
    return mock


def clusters():
    return load_fixture(str(FIXTURES / "describe_db_clusters.json"))["DBClusters"]


def scan(cluster_rows=(), *, event=None, ec2_data=None, db_data=None,
         ec2_calls=(), db_calls=(), cluster_calls=()):
    ec2, rds = client("ec2"), client("rds")
    event = {"job": "ttl-check", "now": NOW, **(event or {})}
    with ExitStack() as stack:
        es = stack.enter_context(Stubber(ec2))
        rs = stack.enter_context(Stubber(rds))
        es.add_response("describe_instances", ec2_data or {"Reservations": []}, {})
        for operation, response, params in ec2_calls:
            es.add_response(operation, response, params)
        rs.add_response("describe_db_instances", db_data or {"DBInstances": []}, {})
        for operation, response, params in db_calls:
            rs.add_response(operation, response, params)
        rs.add_response("describe_db_clusters", {"DBClusters": list(cluster_rows)}, {})
        for operation, response, params in cluster_calls:
            if response is None:
                rs.add_client_error(operation, "AccessDenied", "denied", expected_params=params)
            else:
                rs.add_response(operation, response, params)
        result = ttl.run(event, ec2, rds, TOPIC, "123456789012")
        es.assert_no_pending_responses()
        rs.assert_no_pending_responses()
    return result


def remove_clock(obj):
    return ("remove_tags_from_resource", {}, {"ResourceName": obj["DBClusterArn"], "TagKeys": ["TTL-Start"]})


def test_c2_cluster_first_seen_initializes_without_stop_or_email(sns):
    obj = clusters()[2]
    result = scan([obj], cluster_calls=[("add_tags_to_resource", {}, {
        "ResourceName": obj["DBClusterArn"], "Tags": [{"Key": "TTL-Start", "Value": NOW}]})])
    assert result["initialized"] == [{"type": "Aurora", "id": "aurora-first-seen", "region": "us-east-1"}]
    assert not result["errors"] and not result["stop_requested"]
    sns.publish.assert_not_called()


def test_c2_expired_cluster_stops_clears_clock_and_one_email(sns):
    obj = clusters()[0]
    result = scan([obj], cluster_calls=[
        ("stop_db_cluster", {"DBCluster": {"Status": "stopping"}}, {"DBClusterIdentifier": "aurora-expired"}),
        remove_clock(obj)])
    assert result["stop_requested"] == [{"type": "Aurora", "id": "aurora-expired", "region": "us-east-1"}]
    assert result["cleaned"] == result["stop_requested"]
    assert not result["errors"]
    sns.publish.assert_called_once()
    args = sns.publish.call_args.kwargs
    assert args["Subject"] == "[Cost-Guardian][TTL] Da dung 1 tai nguyen het han"
    assert args["TopicArn"] == TOPIC
    assert "aurora-expired" in args["Message"]
    assert "2026-11-12 17:00:00 (UTC+7)" in args["Message"]
    assert result["notification"]["sent"] is True


def test_c3_serverless_v1_skipped_without_error_or_mail(sns):
    result = scan([clusters()[4]])
    assert result["skipped"][0]["reason"] == "Aurora Serverless v1"
    assert not result["errors"] and not result["stop_requested"]
    sns.publish.assert_not_called()


def test_stopped_cluster_clears_stale_clock_without_email(sns):
    obj = clusters()[3]
    result = scan([obj], cluster_calls=[remove_clock(obj)])
    assert len(result["cleaned"]) == 1
    assert not result["stop_requested"] and not result["errors"]
    sns.publish.assert_not_called()


def test_unexpired_busy_and_untagged_clusters_not_changed(sns):
    data = clusters()
    result = scan([data[1], data[5], data[6]])
    assert not result["errors"]
    assert not result["stop_requested"] and not result["cleaned"] and not result["initialized"]
    sns.publish.assert_not_called()


def test_dry_run_all_cluster_fixtures_performs_no_writes(sns):
    # Không xếp bất kỳ response cho Stop/AddTags/RemoveTags: gọi nhầm sẽ thành lỗi Stubber.
    result = scan(clusters(), event={"dry_run": True})
    assert [x["id"] for x in result["would_stop"]] == ["aurora-expired"]
    assert [x["id"] for x in result["would_initialize"]] == ["aurora-first-seen"]
    assert [x["id"] for x in result["would_clean"]] == ["aurora-stopped"]
    assert not result["stop_requested"] and not result["cleaned"] and not result["errors"]
    assert result["notification"]["sent"] is False
    sns.publish.assert_not_called()


def test_failed_cluster_stop_preserves_clock_and_sends_error(sns):
    result = scan([clusters()[0]], cluster_calls=[
        ("stop_db_cluster", None, {"DBClusterIdentifier": "aurora-expired"})])
    assert not result["stop_requested"] and not result["cleaned"]
    assert len(result["errors"]) == 1
    assert sns.publish.call_args.kwargs["Subject"] == "[Cost-Guardian][TTL] Loi xu ly TTL (1)"


def test_cleanup_failure_keeps_accepted_stop_in_same_email(sns):
    obj = clusters()[0]
    result = scan([obj], cluster_calls=[
        ("stop_db_cluster", {}, {"DBClusterIdentifier": "aurora-expired"}),
        ("remove_tags_from_resource", None, {"ResourceName": obj["DBClusterArn"], "TagKeys": ["TTL-Start"]})])
    assert len(result["stop_requested"]) == 1 and len(result["errors"]) == 1
    sns.publish.assert_called_once()
    assert "AccessDenied" in sns.publish.call_args.kwargs["Message"]


def test_invalid_ttl_cluster_not_stopped(sns):
    obj = clusters()[0]
    obj["TagList"][0]["Value"] = "4 gio"
    result = scan([obj])
    assert not result["stop_requested"] and len(result["errors"]) == 1
    assert sns.publish.call_args.kwargs["Subject"] == "[Cost-Guardian][TTL] Loi xu ly TTL (1)"


def test_original_ec2_rds_fixtures_regression(sns):
    ec2_data = load_fixture(str(FIXTURES / "describe_instances.json"))
    db_data = load_fixture(str(FIXTURES / "describe_db_instances.json"))
    arn = "arn:aws:rds:us-east-1:123456789012:db:"
    result = scan(event={"now": "2026-10-18T10:00:00Z"}, ec2_data=ec2_data, db_data=db_data,
        ec2_calls=[("stop_instances", {}, {"InstanceIds": [resource]}) for resource in [
            "i-ttl0000expired4h", "i-ttl0000expired30", "i-ttl0000uppercase"]],
        db_calls=[
            ("stop_db_instance", {}, {"DBInstanceIdentifier": "db-ttl-expired"}),
            ("remove_tags_from_resource", {}, {"ResourceName": arn + "db-ttl-expired", "TagKeys": ["TTL-Start"]}),
            ("add_tags_to_resource", {}, {"ResourceName": arn + "db-ttl-first-seen", "Tags": [
                {"Key": "TTL-Start", "Value": "2026-10-18T10:00:00Z"}]}),
            ("remove_tags_from_resource", {}, {"ResourceName": arn + "db-ttl-stopped", "TagKeys": ["TTL-Start"]})])
    assert len(result["stop_requested"]) == 4
    assert len(result["errors"]) == 1 and result["errors"][0]["resource"] == "i-ttl0000invalid00"
    assert {x["id"] for x in result["skipped"]} >= {"i-ttl0000inasg0000", "db-aurora-writer", "db-replica"}
    sns.publish.assert_called_once()
    assert "i-ttl0000invalid00" in sns.publish.call_args.kwargs["Message"]


@pytest.mark.parametrize("value,seconds", [("30m", 1800), (" 4 H ", 14400), ("2d", 172800)])
def test_ttl_accepts_case_and_whitespace(value, seconds):
    assert ttl.duration(value).total_seconds() == seconds


@pytest.mark.parametrize("value", ["0h", "-1h", "1.5h", "4 gio", ""])
def test_bad_ttl_rejected(value):
    with pytest.raises(ValueError):
        ttl.duration(value)


def test_serverless_v2_not_confused_with_v1(sns):
    obj = clusters()[0]
    obj["ServerlessV2ScalingConfiguration"] = {"MinCapacity": 0.5, "MaxCapacity": 1.0}
    result = scan([obj], event={"dry_run": True})
    assert len(result["would_stop"]) == 1 and not result["errors"]


def test_no_resources_no_email(sns):
    result = scan()
    assert result["notification"] is None
    sns.publish.assert_not_called()


def test_sns_failure_propagates(sns):
    sns.publish.side_effect = RuntimeError("SNS unavailable")
    obj = clusters()[0]
    obj["TagList"][0]["Value"] = "bad"
    with pytest.raises(RuntimeError, match="SNS unavailable"):
        scan([obj])


def test_cluster_paginator_reads_all_pages(sns):
    ec2, rds = client("ec2"), client("rds")
    with Stubber(ec2) as es, Stubber(rds) as rs:
        es.add_response("describe_instances", {"Reservations": []}, {})
        rs.add_response("describe_db_instances", {"DBInstances": []}, {})
        rs.add_response("describe_db_clusters", {"DBClusters": [], "Marker": "page2"}, {})
        rs.add_response("describe_db_clusters", {"DBClusters": [clusters()[0]]}, {"Marker": "page2"})
        result = ttl.run({"job": "ttl-check", "now": NOW, "dry_run": True}, ec2, rds, TOPIC, "123456789012")
        assert len(result["would_stop"]) == 1 and not result["errors"]
        rs.assert_no_pending_responses()


def test_region_failure_does_not_hide_next_region(sns):
    ec2, rds = client("ec2"), client("rds")
    def factory(service, region):
        if region == "ap-southeast-1":
            raise RuntimeError("Region unavailable")
        return ec2 if service == "ec2" else rds
    with Stubber(ec2) as es, Stubber(rds) as rs:
        es.add_response("describe_instances", {"Reservations": []}, {})
        rs.add_response("describe_db_instances", {"DBInstances": []}, {})
        rs.add_response("describe_db_clusters", {"DBClusters": []}, {})
        result = ttl.run_regions({"job": "ttl-check"}, factory, ["ap-southeast-1", "us-east-1"], TOPIC, "123456789012")
    assert result["regions_scanned"] == ["us-east-1"] and len(result["errors"]) == 1
    sns.publish.assert_called_once()


@pytest.mark.parametrize("all_regions", ["true", "false"])
def test_handler_obeys_repo_environment_and_does_not_discover_when_false(monkeypatch, sns, all_regions):
    ec2, rds = client("ec2"), client("rds")
    monkeypatch.setenv("TOPIC_ARN", TOPIC)
    monkeypatch.setenv("ALL_REGIONS", all_regions)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("SNS_TOPIC_ARN", raising=False)
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: ec2 if service == "ec2" else rds)
    with Stubber(ec2) as es, Stubber(rds) as rs:
        regions = ["us-east-1"]
        if all_regions == "true":
            es.add_response("describe_regions", {"Regions": [
                {"RegionName": "us-east-1", "OptInStatus": "opt-in-not-required"},
                {"RegionName": "ap-southeast-1", "OptInStatus": "opted-in"}]}, {"AllRegions": False})
            regions = ["ap-southeast-1", "us-east-1"]
        for _ in regions:
            es.add_response("describe_instances", {"Reservations": []}, {})
            rs.add_response("describe_db_instances", {"DBInstances": []}, {})
            rs.add_response("describe_db_clusters", {"DBClusters": []}, {})
        result = ttl.lambda_handler({"job": "ttl-check", "dry_run": True, "now": NOW}, None)
        assert result["regions_scanned"] == regions and not result["errors"]
        es.assert_no_pending_responses()
        rs.assert_no_pending_responses()
    sns.publish.assert_not_called()


def test_live_handler_rejects_fake_clock_before_any_api(sns):
    with pytest.raises(ValueError, match="only allowed for dry-run"):
        ttl.lambda_handler({"job": "ttl-check", "now": NOW}, None)
    sns.publish.assert_not_called()


def test_unknown_job_rejected(sns):
    with pytest.raises(ValueError, match="job=ttl-check"):
        ttl.lambda_handler({"job": "daily-scan", "dry_run": True}, None)


def test_invalid_all_regions_rejected(monkeypatch, sns):
    monkeypatch.setenv("TOPIC_ARN", TOPIC)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("ALL_REGIONS", "typo")
    with pytest.raises(ValueError, match="ALL_REGIONS"):
        ttl.lambda_handler({"job": "ttl-check", "dry_run": True}, None)
