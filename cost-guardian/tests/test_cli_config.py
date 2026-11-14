"""Test cli/config.py – phần hỏi đáp cấu hình. Không cần Terraform hay AWS."""
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "cli"))

import config  # noqa: E402
import deploy  # noqa: E402

VARIABLES_TF = ROOT / "terraform" / "main" / "variables.tf"


def answers(*items):
    """Giả lập người gõ phím: trả lần lượt từng câu; hết thì báo lỗi rõ ràng."""
    it = iter(items)

    def ask(prompt):
        try:
            return next(it)
        except StopIteration:
            pytest.fail(f"CLI hỏi thêm câu không có trong kịch bản: {prompt!r}")
    return ask


# ---------------------------------------------------------------- khớp với variables.tf
def read_terraform_variables():
    text = VARIABLES_TF.read_text(encoding="utf-8")
    result = {}
    for m in re.finditer(r'variable\s+"(\w+)"\s*\{(.*?)\n\}', text, re.S):
        d = re.search(r"^\s*default\s*=\s*(.+?)\s*$", m.group(2), re.M)
        result[m.group(1)] = json.loads(d.group(1)) if d else None
    return result


@pytest.mark.skipif(not VARIABLES_TF.exists(), reason="không có terraform/main/variables.tf")
def test_cau_hoi_khop_voi_variables_tf():
    tf_vars = read_terraform_variables()
    for q in config.QUESTIONS:
        assert q.key in tf_vars, f"Câu hỏi {q.key} không có biến tương ứng trong variables.tf"
        assert q.default == tf_vars[q.key], f"Mặc định của {q.key} lệch với variables.tf"
    # biến nào trong variables.tf không được hỏi thì phải là cố ý
    assert set(tf_vars) - set(config.BY_KEY) == {"name_prefix"}


# ---------------------------------------------------------------- bộ đọc giá trị
@pytest.mark.parametrize("text, expected", [("y", True), ("Có", True), ("n", False), ("không", False)])
def test_parse_bool(text, expected):
    assert config.parse_bool(text) is expected


def test_parse_number_va_danh_sach():
    assert config.parse_number("100") == 100 and isinstance(config.parse_number("100"), int)
    assert config.parse_number("12,5") == 12.5
    assert config.parse_number_list("100, 80 80;120") == [80, 100, 120]
    with pytest.raises(config.InvalidAnswer):
        config.parse_int("7.5")


@pytest.mark.parametrize("text, expected", [("7:00", "07:00"), ("19h30", "19:30"), ("07:05", "07:05")])
def test_parse_time(text, expected):
    assert config.parse_time(text) == expected


@pytest.mark.parametrize("text", ["24:00", "7", "07:60"])
def test_parse_time_sai(text):
    with pytest.raises(config.InvalidAnswer):
        config.parse_time(text)


def test_nguong_ngan_sach():
    with pytest.raises(config.InvalidAnswer, match="1 đến 4"):
        config.check_thresholds([50, 80, 100, 120, 150])
    with pytest.raises(config.InvalidAnswer):
        config.check_thresholds([0])


# ---------------------------------------------------------------- luồng hỏi đáp
@pytest.fixture
def cfg(tmp_path):
    return tmp_path / "terraform.tfvars.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_lan_dau_chi_tra_loi_email_con_lai_enter(cfg):
    # email, budget, all regions, ttl, nâng cao?, lưu?
    config.collect_config(cfg, ask=answers("me@gmail.com", "", "", "", "", ""))
    data = read(cfg)
    assert data["alert_email"] == "me@gmail.com"
    assert data["monthly_budget_usd"] == 100 and data["monitor_all_regions"] is True
    assert data["budget_actual_thresholds"] == [80, 100]       # nâng cao lấy mặc định
    assert data["ttl_check_interval_minutes"] == 30


def test_email_sai_thi_hoi_lai(cfg, capsys):
    config.collect_config(cfg, ask=answers("", "abc", "me@gmail.com", "10", "n", "", "", ""))
    out = capsys.readouterr().out
    assert "bắt buộc" in out and "không đúng định dạng" in out
    data = read(cfg)
    assert data["monthly_budget_usd"] == 10 and data["monitor_all_regions"] is False


def test_nhap_sai_qua_nhieu_lan_thi_dung(cfg):
    with pytest.raises(deploy.DeployError, match="Nhập sai"):
        config.collect_config(cfg, ask=answers(*["abc"] * config.MAX_TRIES))
    assert not cfg.exists()


def test_tuy_chinh_nang_cao(cfg):
    config.collect_config(cfg, ask=answers(
        "me@gmail.com", "50", "y", "y", "y",       # cơ bản + vào nâng cao
        "ap-southeast-1", "50, 90", "n", "", "8:30", "30", "3", "", "15",
        ""))                                          # lưu
    data = read(cfg)
    assert data["home_region"] == "ap-southeast-1"
    assert data["budget_actual_thresholds"] == [50, 90] and data["budget_forecast_alert"] is False
    assert data["daily_report_time"] == "08:30" and data["idle_cpu_threshold"] == 3
    assert data["ttl_check_interval_minutes"] == 15


def test_tat_ttl_thi_khong_hoi_chu_ky(cfg):
    # nâng cao: region, ngưỡng, dự báo, credits, giờ, snapshot, cpu, ngày – KHÔNG có câu chu kỳ TTL
    config.collect_config(cfg, ask=answers("me@gmail.com", "", "", "n", "y", *[""] * 8, ""))
    data = read(cfg)
    assert data["enable_ttl"] is False and "ttl_check_interval_minutes" not in data


def test_khong_luu_thi_nhap_lai_va_giu_cau_tra_loi_cu(cfg):
    config.collect_config(cfg, ask=answers(
        "me@gmail.com", "20", "", "", "", "n",       # lượt 1, không lưu
        "", "30", "", "", "", "y"))                  # lượt 2: Enter = giữ email vừa nhập
    data = read(cfg)
    assert data["alert_email"] == "me@gmail.com" and data["monthly_budget_usd"] == 30


def test_co_cau_hinh_cu_thi_hoi_dung_lai(cfg):
    cfg.write_text('{"alert_email": "old@gmail.com", "monthly_budget_usd": 10}', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    config.collect_config(cfg, ask=answers(""))      # Enter = dùng lại
    assert cfg.read_text(encoding="utf-8") == before  # không ghi đè


def test_khong_dung_lai_thi_gia_tri_cu_la_mac_dinh(cfg):
    cfg.write_text('{"alert_email": "old@gmail.com", "monthly_budget_usd": 10, "name_prefix": "cg"}',
                   encoding="utf-8")
    config.collect_config(cfg, ask=answers("n", "", "", "", "", "", ""))
    data = read(cfg)
    assert data["alert_email"] == "old@gmail.com" and data["monthly_budget_usd"] == 10
    assert data["name_prefix"] == "cg"               # khóa ngoài danh sách câu hỏi được giữ nguyên


def test_cau_hinh_cu_co_gia_tri_sai_thi_hoi_lai_tu_dau(cfg):
    cfg.write_text('{"alert_email": "old@gmail.com", "daily_report_time": "25:00"}', encoding="utf-8")
    config.collect_config(cfg, ask=answers("", "", "", "", "", ""))   # không có câu 'dùng lại?'
    assert read(cfg)["daily_report_time"] == "07:00"


def test_file_cu_hong_thi_bo_qua(cfg, capsys):
    cfg.write_text("{hỏng", encoding="utf-8")
    config.collect_config(cfg, ask=answers("me@gmail.com", "", "", "", "", ""))
    assert "Bỏ qua" in capsys.readouterr().out
    assert read(cfg)["alert_email"] == "me@gmail.com"


def test_khong_co_ban_phim(cfg):
    def eof(prompt):
        raise EOFError
    with pytest.raises(deploy.DeployError, match="--skip-config"):
        config.collect_config(cfg, ask=eof)


def test_file_ghi_ra_terraform_doc_duoc(cfg):
    config.collect_config(cfg, ask=answers("dương@gmail.com", "", "", "", "", ""))
    text = cfg.read_text(encoding="utf-8")
    assert "dương@gmail.com" in text                  # giữ nguyên chữ có dấu, không thành \u...
    assert not (cfg.parent / (cfg.name + ".tmp")).exists()
