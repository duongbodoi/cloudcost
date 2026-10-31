from datetime import datetime, timezone

import pytest
from botocore.stub import Stubber

import notify

TOPIC = "arn:aws:sns:us-east-1:123456789012:Cost-Guardian"


def test_to_ascii_bo_dau():
    assert notify.to_ascii("Đã dừng tài nguyên – bật lại") == "Da dung tai nguyen  bat lai"


def test_make_subject_dung_mau_va_cat_99_ky_tu():
    s = notify.make_subject("TTL", "Đã dừng 2 tài nguyên hết hạn")
    assert s == "[Cost-Guardian][TTL] Da dung 2 tai nguyen het han"
    assert len(notify.make_subject("DAILY", "x" * 300)) == 99


def test_make_subject_sai_loai():
    with pytest.raises(ValueError):
        notify.make_subject("BUDGET", "abc")


def test_field_lines_thang_hang_va_bo_none():
    lines = notify.field_lines([("Sự kiện", "RunInstances"), ("Người thực hiện", "user-b"), ("Tag", None)])
    assert lines == ["Sự kiện        : RunInstances", "Người thực hiện: user-b"]


def test_fmt_time_doi_sang_utc7():
    assert notify.fmt_time("2026-10-31T08:02:41Z") == "2026-10-31 15:02:41 (UTC+7)"
    assert notify.fmt_time(datetime(2026, 10, 31, 20, 0)) == "2026-11-01 03:00:00 (UTC+7)"   # naive = UTC


def test_fmt_date():
    assert notify.fmt_date(datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)) == "01/11/2026"


@pytest.mark.parametrize("hourly, expected", [
    (0.059, "~0,059 USD/giờ ≈ 43 USD/tháng"),
    (0.0104, "~0,010 USD/giờ ≈ 7,6 USD/tháng"),
    (2.5, "~2,50 USD/giờ ≈ 1.825 USD/tháng"),
    (None, "không ước tính được"),
])
def test_fmt_money(hourly, expected):
    assert notify.fmt_money(hourly) == expected


def test_send_dry_run_khong_goi_sns():
    r = notify.send("ALERT", "Tài nguyên mới: EC2", ["a", "b"], dry_run=True)
    assert r == {"subject": "[Cost-Guardian][ALERT] Tai nguyen moi: EC2", "message": "a\nb",
                 "sent": False, "message_id": None}


def test_send_that_goi_publish_dung_tham_so(monkeypatch):
    monkeypatch.setenv("TOPIC_ARN", TOPIC)
    client = notify._sns_client()
    with Stubber(client) as stub:
        stub.add_response("publish", {"MessageId": "m-1"}, {
            "TopicArn": TOPIC, "Subject": "[Cost-Guardian][DAILY] Bao cao", "Message": "dòng 1"})
        r = notify.send("DAILY", "Báo cáo", ["dòng 1"])
    assert r["sent"] and r["message_id"] == "m-1"


def test_send_thieu_topic(monkeypatch):
    monkeypatch.delenv("TOPIC_ARN", raising=False)
    with pytest.raises(RuntimeError):
        notify.send("TTL", "x", ["y"])
