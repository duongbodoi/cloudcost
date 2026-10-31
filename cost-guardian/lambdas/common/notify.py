"""
notify.py – Tiện ích gửi thông báo dùng chung cho cả 3 Lambda (ALERT, DAILY, TTL)
==================================================================================

Gom mọi thứ liên quan đến định dạng và gửi email về MỘT chỗ, để cả nhóm
cùng tuân theo mục 4 của CONVENTIONS.md mà không phải chép lại code.

Cách dùng trong lambda_function.py:

    from notify import send, field_lines, bullet, fmt_time, fmt_money

    lines = field_lines([
        ("Loại cảnh báo", "Tài nguyên tính phí mới được tạo/bật"),
        ("Tài nguyên", "EC2  i-0123456789abcdef0"),
        ("Thời điểm", fmt_time(event_time)),
        ("Ước tính", fmt_money(0.0208)),
    ])
    send("ALERT", "Tai nguyen tinh phi moi: EC2", lines)

Biến môi trường:
    TOPIC_ARN   ARN của SNS topic Cost-Guardian (bắt buộc khi gửi thật)

Giai đoạn 1 (thử trên console): tạo file notify.py ngang hàng lambda_function.py
(File → New File), dán toàn bộ nội dung file này vào rồi Deploy.
"""
import os
import unicodedata
from datetime import datetime, timedelta, timezone

import boto3

VN_TZ = timezone(timedelta(hours=7))
HOURS_PER_MONTH = 730            # số giờ trung bình của một tháng (AWS dùng con số này)
SUBJECT_MAX = 99                 # SNS Subject phải dưới 100 ký tự
KINDS = ("ALERT", "DAILY", "TTL")

_sns = None


def _sns_client():
    """Tạo client SNS một lần, dùng lại cho các lần gọi sau (kể cả giữa các lần Lambda chạy ấm)."""
    global _sns
    if _sns is None:
        _sns = boto3.client("sns")
    return _sns


# ----------------------------------------------------------------------- văn bản
def to_ascii(text):
    """Bỏ dấu tiếng Việt: 'Đã dừng tài nguyên' -> 'Da dung tai nguyen'. Dùng cho SNS Subject."""
    text = str(text).replace("đ", "d").replace("Đ", "D")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def make_subject(kind, content):
    """Tiêu đề chuẩn: [Cost-Guardian][<KIND>] <nội dung>, không dấu, tối đa 99 ký tự."""
    if kind not in KINDS:
        raise ValueError(f"kind phải là một trong {KINDS}, nhận được: {kind!r}")
    return to_ascii(f"[Cost-Guardian][{kind}] {content}")[:SUBJECT_MAX]


def field_lines(pairs):
    """[('Nhãn', 'giá trị'), ...] -> các dòng 'Nhãn     : giá trị' có dấu ':' thẳng hàng.

    Cặp có giá trị None sẽ bị bỏ qua (tiện cho các trường không bắt buộc).
    """
    pairs = [(label, value) for label, value in pairs if value is not None]
    if not pairs:
        return []
    width = max(len(label) for label, _ in pairs)
    return [f"{label.ljust(width)}: {value}" for label, value in pairs]


def bullet(items):
    """['a', 'b'] -> ['  - a', '  - b']"""
    return [f"  - {x}" for x in items]


# ----------------------------------------------------------------------- thời gian
def _to_datetime(value):
    """Nhận datetime (có hoặc không có múi giờ) hoặc chuỗi ISO 8601 ('...Z'); trả về datetime có múi giờ.
    Datetime không có múi giờ được coi là UTC."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def fmt_time(value):
    """-> '2026-10-31 15:02:41 (UTC+7)'"""
    return _to_datetime(value).astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M:%S (UTC+7)")


def fmt_date(value=None):
    """-> '31/10/2026' theo giờ Việt Nam. Không truyền gì thì lấy ngày hiện tại."""
    value = datetime.now(timezone.utc) if value is None else _to_datetime(value)
    return value.astimezone(VN_TZ).strftime("%d/%m/%Y")


# ----------------------------------------------------------------------- tiền
def _vn_number(x, decimals):
    """1234.5 -> '1.234,5' (dấu chấm ngăn hàng nghìn, dấu phẩy thập phân)."""
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_hourly(hourly):
    """0.0208 -> '0,021'; 0.1 -> '0,10'; 1.5 -> '1,50'"""
    return _vn_number(hourly, 3 if hourly < 0.1 else 2)


def fmt_monthly(monthly):
    """7.59 -> '7,6'; 43.07 -> '43'; 1520 -> '1.520'"""
    return _vn_number(monthly, 1 if monthly < 10 else 0)


def fmt_money(hourly):
    """Giá theo giờ -> '~0,059 USD/giờ ≈ 43 USD/tháng'. None -> 'không ước tính được'."""
    if hourly is None:
        return "không ước tính được"
    return f"~{fmt_hourly(hourly)} USD/giờ ≈ {fmt_monthly(hourly * HOURS_PER_MONTH)} USD/tháng"


# ----------------------------------------------------------------------- gửi
def send(kind, content, lines, topic_arn=None, dry_run=False):
    """Gửi một email qua SNS.

    kind      'ALERT' | 'DAILY' | 'TTL'
    content   phần sau tiền tố trong tiêu đề (có dấu cũng được, sẽ tự bỏ dấu)
    lines     danh sách dòng nội dung (tiếng Việt có dấu)
    dry_run   True -> không gửi, chỉ trả về subject/message để kiểm tra

    Trả về {"subject": ..., "message": ..., "sent": bool, "message_id": ...}
    """
    subject = make_subject(kind, content)
    message = "\n".join(lines)
    result = {"subject": subject, "message": message, "sent": False, "message_id": None}
    if dry_run:
        return result
    arn = topic_arn or os.environ.get("TOPIC_ARN")
    if not arn:
        raise RuntimeError("Chưa cấu hình biến môi trường TOPIC_ARN")
    resp = _sns_client().publish(TopicArn=arn, Subject=subject, Message=message)
    result.update(sent=True, message_id=resp.get("MessageId"))
    return result
