"""
config.py – Phần cấu hình (hỏi đáp) của CLI Cost Guardian
==========================================================
Phụ trách: Ngô Xuân Hậu (hạng mục 30)

Hợp đồng với cost-guardian.py:
    collect_config(path) -> path     hỏi người dùng, ghi terraform.tfvars.json, trả về đường dẫn đã ghi

Cách làm việc:
  * Danh sách câu hỏi khai báo trong QUESTIONS – thêm / bớt câu hỏi chỉ cần sửa danh sách này.
  * Mặc định của mỗi câu = giá trị trong file cấu hình cũ (nếu có), không có thì = default
    trong terraform/main/variables.tf (test kiểm tra hai bên luôn khớp nhau).
  * Mỗi câu trả lời được kiểm tra ngay theo đúng điều kiện `validation` của Terraform,
    sai thì hỏi lại – người dùng không phải chờ tới lúc terraform plan mới biết.
  * Câu hỏi chia 2 nhóm: cơ bản (ai cũng phải trả lời) và nâng cao (chỉ hỏi khi người dùng muốn).
  * Phần ngân sách (budget_*) vẫn là BẢN NHÁP theo CONVENTIONS mục 7.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import deploy

MAX_TRIES = 5          # nhập sai quá số lần này thì dừng (tránh lặp vô hạn khi chạy trong script)


class InvalidAnswer(ValueError):
    """Câu trả lời sai – thông báo được in ra rồi hỏi lại."""


# ======================================================================= bộ đọc giá trị
YES = {"y", "yes", "c", "co", "có"}
NO = {"n", "no", "k", "khong", "không"}


def parse_bool(text):
    t = text.strip().lower()
    if t in YES:
        return True
    if t in NO:
        return False
    raise InvalidAnswer("Trả lời y (có) hoặc n (không).")


def parse_number(text):
    """'100' -> 100, '12,5' -> 12.5 (chấp nhận dấu phẩy kiểu Việt Nam)."""
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError as e:
        raise InvalidAnswer("Cần nhập một số.") from e
    return int(value) if value.is_integer() else value


def parse_int(text):
    value = parse_number(text)
    if not isinstance(value, int):
        raise InvalidAnswer("Cần nhập số nguyên.")
    return value


def parse_number_list(text):
    """'80, 100' hoặc '80 100' -> [80, 100] – bỏ trùng, sắp xếp tăng dần."""
    parts = [p for p in re.split(r"[,;\s]+", text.strip()) if p]
    if not parts:
        raise InvalidAnswer("Nhập ít nhất một số, cách nhau bởi dấu phẩy, vd 80, 100.")
    values = []
    for p in parts:
        try:
            v = float(p)
        except ValueError as e:
            raise InvalidAnswer(f"'{p}' không phải là số.") from e
        values.append(int(v) if v.is_integer() else v)
    return sorted(set(values))


def parse_text(text):
    return text.strip()


def parse_time(text):
    """'7:00' -> '07:00'."""
    m = re.fullmatch(r"\s*(\d{1,2})[:hH.](\d{2})\s*", text)
    if not m:
        raise InvalidAnswer("Nhập giờ dạng HH:MM, vd 07:00.")
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise InvalidAnswer("Giờ phải từ 00:00 đến 23:59.")
    return f"{h:02d}:{mi:02d}"


# ======================================================================= điều kiện hợp lệ
# Giữ giống phần `validation` trong variables.tf.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-[0-9]$")


def check_email(v):
    if not EMAIL_RE.match(v):
        raise InvalidAnswer("Email không đúng định dạng, vd ten@gmail.com.")


def check_region(v):
    if not REGION_RE.match(v):
        raise InvalidAnswer("Mã Region không hợp lệ, vd us-east-1, ap-southeast-1.")


def check_range(lo, hi, lo_open=False, hi_open=False):
    def check(v):
        too_low = v <= lo if lo_open else v < lo
        too_high = v >= hi if hi_open else v > hi
        if too_low or too_high:
            left, right = "(" if lo_open else "[", ")" if hi_open else "]"
            raise InvalidAnswer(f"Giá trị phải trong khoảng {left}{lo}, {hi}{right}.")
    return check


def check_positive(v):
    if v <= 0:
        raise InvalidAnswer("Giá trị phải lớn hơn 0.")


def check_thresholds(values):
    if not 1 <= len(values) <= 4:
        raise InvalidAnswer("Cần từ 1 đến 4 ngưỡng (AWS Budgets cho tối đa 5 thông báo, 1 dành cho dự báo).")
    for t in values:
        if not 0 < t <= 1000:
            raise InvalidAnswer(f"Ngưỡng {t}% không hợp lệ – mỗi ngưỡng phải trong khoảng (0, 1000].")


# ======================================================================= danh sách câu hỏi
@dataclass
class Question:
    key: str                                  # tên biến Terraform
    label: str                                # câu hỏi hiển thị
    parse: Callable                           # chuỗi -> giá trị
    default: object = None                    # None = bắt buộc nhập
    check: Optional[Callable] = None          # ném InvalidAnswer nếu sai
    advanced: bool = False
    hint: str = ""                            # giải thích thêm, in dưới câu hỏi
    when: Callable = field(default=lambda answers: True)   # chỉ hỏi khi điều kiện đúng
    short: str = ""                           # nhãn ngắn trong bảng tóm tắt
    unit: str = ""                            # đơn vị in sau giá trị trong bảng tóm tắt


QUESTIONS = [
    # ---------------- cơ bản
    Question("alert_email", "Email nhận thông báo", parse_text, None, check_email,
             hint="AWS sẽ gửi thư xác nhận tới địa chỉ này – phải bấm 'Confirm subscription'.",
             short="Email"),
    Question("monthly_budget_usd", "Ngân sách hằng tháng (USD)", parse_number, 100, check_positive,
             short="Ngân sách / tháng", unit="USD"),
    Question("monitor_all_regions", "Giám sát mọi Region đang bật? (n = chỉ Region trung tâm)", parse_bool, True,
             short="Giám sát mọi Region"),
    Question("enable_ttl", "Bật tự động dừng tài nguyên có tag TTL?", parse_bool, True,
             short="Tự dừng theo TTL"),

    # ---------------- nâng cao
    Question("home_region", "Region trung tâm đặt hệ thống", parse_text, "us-east-1", check_region,
             advanced=True, hint="Đổi Region sau khi đã triển khai sẽ khiến Terraform tạo lại toàn bộ.",
             short="Region trung tâm"),
    Question("budget_actual_thresholds", "Các ngưỡng cảnh báo theo chi phí thực tế (%, cách nhau dấu phẩy)",
             parse_number_list, [80, 100], check_thresholds, advanced=True,
             short="Ngưỡng cảnh báo", unit="%"),
    Question("budget_forecast_alert", "Cảnh báo khi chi phí DỰ BÁO vượt ngân sách?", parse_bool, True,
             advanced=True,
             short="Cảnh báo theo dự báo"),
    Question("budget_exclude_credits", "Bỏ qua credits khi tính chi phí?", parse_bool, True, advanced=True,
             hint="Nên để y nếu tài khoản có AWS credits, nếu không ngân sách luôn trông như bằng 0.",
             short="Bỏ qua credits"),
    Question("daily_report_time", "Giờ gửi báo cáo hằng ngày (giờ Việt Nam, HH:MM)", parse_time, "07:00",
             advanced=True,
             short="Giờ báo cáo hằng ngày"),
    Question("snapshot_age_days", "Đưa snapshot cũ hơn bao nhiêu ngày vào báo cáo", parse_int, 90,
             check_range(1, 3650), advanced=True,
             short="Báo snapshot cũ hơn", unit="ngày"),
    Question("idle_cpu_threshold", "Ngưỡng CPU (%) coi là EC2 không hoạt động", parse_number, 5,
             check_range(0, 100, lo_open=True, hi_open=True), advanced=True,
             short="Ngưỡng CPU không hoạt động", unit="%"),
    Question("idle_days", "Số ngày liên tiếp CPU thấp mới coi là không hoạt động", parse_int, 7,
             check_range(1, 60), advanced=True,
             short="Số ngày CPU thấp", unit="ngày"),
    Question("ttl_check_interval_minutes", "Chu kỳ kiểm tra TTL (phút)", parse_int, 30,
             check_range(5, 1440), advanced=True, when=lambda a: a.get("enable_ttl", True),
             short="Chu kỳ kiểm tra TTL", unit="phút"),
]
BY_KEY = {q.key: q for q in QUESTIONS}


# ======================================================================= hiển thị
def show(value):
    """Giá trị Python -> chữ hiển thị cho người dùng."""
    if isinstance(value, bool):
        return "y" if value else "n"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def show_long(value):
    if isinstance(value, bool):
        return "có" if value else "không"
    return show(value)


def is_valid(q, value):
    """Giá trị cũ (đọc từ file) có còn hợp lệ để làm mặc định không."""
    if value is None:
        return False
    try:
        normalized = q.parse(show(value))
        if q.check:
            q.check(normalized)
        return True
    except InvalidAnswer:
        return False


# ======================================================================= hỏi đáp
def ask_one(q, default, ask=input):
    """Hỏi một câu; Enter = giữ mặc định; sai thì hỏi lại tối đa MAX_TRIES lần."""
    if q.hint:
        print(f"     ({q.hint})")
    suffix = f" [{show(default)}]" if default is not None else ""
    for _ in range(MAX_TRIES):
        try:
            text = ask(f"   {q.label}{suffix}: ")
        except EOFError as e:
            raise deploy.DeployError(
                "Không đọc được câu trả lời (không có bàn phím nhập).\n"
                "  Khi chạy tự động, dùng: deploy --skip-config với file terraform.tfvars.json có sẵn.") from e
        if not text.strip():
            if default is not None:
                return default
            print("     Câu này bắt buộc phải trả lời.")
            continue
        try:
            value = q.parse(text)
            if q.check:
                q.check(value)
            return value
        except InvalidAnswer as e:
            print(f"     {e}")
    raise deploy.DeployError(f"Nhập sai {MAX_TRIES} lần cho câu '{q.label}' – dừng lại, chưa lưu gì.")


def ask_round(defaults, ask=input):
    """Một lượt hỏi: phần cơ bản, rồi hỏi có muốn chỉnh phần nâng cao không."""
    answers = {}
    for q in QUESTIONS:
        if not q.advanced and q.when(answers):
            answers[q.key] = ask_one(q, defaults.get(q.key), ask)

    adv = Question("_advanced", "Tùy chỉnh nâng cao (ngưỡng ngân sách, giờ báo cáo, EC2 không hoạt động, TTL...)?",
                   parse_bool, False)
    if ask_one(adv, False, ask):
        print()
        for q in QUESTIONS:
            if q.advanced and q.when(answers):
                answers[q.key] = ask_one(q, defaults.get(q.key), ask)
    else:
        for q in QUESTIONS:
            if q.advanced and q.when(answers) and defaults.get(q.key) is not None:
                answers[q.key] = defaults[q.key]
    return answers


def print_summary(answers):
    width = max(len(q.short) for q in QUESTIONS if q.key in answers)
    for q in QUESTIONS:
        if q.key in answers:
            unit = f" {q.unit}" if q.unit else ""
            print(f"     {q.short.ljust(width)} : {show_long(answers[q.key])}{unit}")


# ======================================================================= đọc / ghi file
def load_existing(path):
    """Đọc cấu hình cũ. File hỏng thì báo và coi như chưa có."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("không phải object JSON")
        return data
    except ValueError as e:
        deploy.info(f"Bỏ qua {path.name} cũ vì không đọc được ({e}).")
        return {}


def defaults_from(existing):
    """Mặc định cho từng câu: giá trị cũ nếu còn hợp lệ, không thì default của Terraform."""
    result = {}
    for q in QUESTIONS:
        old = existing.get(q.key)
        result[q.key] = old if is_valid(q, old) else q.default
    return result


def write_config(path, answers, existing):
    """Ghi JSON: các biến theo thứ tự câu hỏi, giữ nguyên các khóa khác có sẵn trong file cũ."""
    path = Path(path)
    data = {q.key: answers[q.key] for q in QUESTIONS if q.key in answers}
    for k, v in existing.items():                 # vd name_prefix do nhóm tự thêm
        if k not in BY_KEY:
            data[k] = v
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)                             # ghi qua file tạm để không bao giờ để lại file dở
    return path


def existing_is_complete(existing):
    """Đủ câu bắt buộc và mọi giá trị đang có đều hợp lệ – mới cho phép 'dùng lại'."""
    required_ok = all(q.key in existing for q in QUESTIONS if q.default is None)
    values_ok = all(is_valid(q, existing[q.key]) for q in QUESTIONS if q.key in existing)
    return required_ok and values_ok


# ======================================================================= hợp đồng
def collect_config(path, ask=input):
    """Hỏi đáp và ghi file cấu hình. Trả về đường dẫn file đã ghi."""
    path = Path(path)
    deploy.step("Cấu hình")
    existing = load_existing(path)

    if existing and existing_is_complete(existing):
        deploy.ok(f"Đã có cấu hình: {path}")
        current = defaults_from(existing)          # khóa thiếu trong file = default của Terraform
        if not current.get("enable_ttl", True):
            current.pop("ttl_check_interval_minutes", None)
        print_summary(current)
        keep = Question("_keep", "Dùng lại cấu hình này?", parse_bool, True)
        if ask_one(keep, True, ask):
            return path
        print()

    print("   Nhấn Enter để giữ giá trị trong [ngoặc vuông].")
    defaults = defaults_from(existing)
    while True:
        answers = ask_round(defaults, ask)
        print("\n   Cấu hình sẽ lưu:")
        print_summary(answers)
        save = Question("_save", "Lưu cấu hình này? (n = nhập lại)", parse_bool, True)
        if ask_one(save, True, ask):
            break
        defaults = {**defaults, **answers}        # nhập lại: giữ câu trả lời vừa rồi làm mặc định
        print()

    write_config(path, answers, existing)
    deploy.ok(f"Đã lưu: {path}")
    return path
