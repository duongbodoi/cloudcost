"""Test phần kiểm tra môi trường của cli/deploy.py – không cần Terraform hay AWS thật."""
import json
import os
import subprocess
import sys

import pytest
from botocore.stub import Stubber

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cli"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import deploy  # noqa: E402


# ---------------------------------------------------------------- phiên bản
@pytest.mark.parametrize("text, expected", [
    ("1.16.6", (1, 16, 6)),
    ("v1.10.0", (1, 10, 0)),
    ("1.11.0-beta1", (1, 11, 0)),
])
def test_parse_version(text, expected):
    assert deploy.parse_version(text) == expected


def test_parse_version_sai():
    with pytest.raises(deploy.DeployError):
        deploy.parse_version("abc")


def _fake_terraform(monkeypatch, version):
    monkeypatch.setattr(deploy.shutil, "which", lambda name: "/usr/bin/terraform")
    out = json.dumps({"terraform_version": version})
    monkeypatch.setattr(deploy.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))


def test_terraform_du_moi(monkeypatch):
    _fake_terraform(monkeypatch, "1.16.6")
    assert deploy.check_terraform() == ("/usr/bin/terraform", (1, 16, 6))


def test_terraform_qua_cu(monkeypatch):
    _fake_terraform(monkeypatch, "1.9.8")
    with pytest.raises(deploy.DeployError, match="quá cũ"):
        deploy.check_terraform()


def test_khong_co_terraform(monkeypatch):
    monkeypatch.setattr(deploy.shutil, "which", lambda name: None)
    with pytest.raises(deploy.DeployError, match="Không tìm thấy Terraform"):
        deploy.check_terraform()


# ---------------------------------------------------------------- AWS
def _patch_sts(monkeypatch, setup):
    """Thay boto3.Session để client sts đi qua Stubber."""
    import boto3
    real_session = boto3.Session

    class FakeSession(real_session):
        def client(self, name, **kw):
            c = real_session(region_name="us-east-1").client(name, **kw)
            stub = Stubber(c)
            setup(stub)
            stub.activate()
            return c

    monkeypatch.setattr(boto3, "Session", lambda *a, **k: FakeSession(region_name="us-east-1"))


def test_aws_dang_nhap_dung(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    _patch_sts(monkeypatch, lambda s: s.add_response("get_caller_identity", {
        "UserId": "AIDAX", "Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/dev"}))
    ident = deploy.aws_identity()
    assert ident["account"] == "123456789012" and ident["arn"].endswith("user/dev")


def test_aws_key_khong_hop_le(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    _patch_sts(monkeypatch, lambda s: s.add_client_error(
        "get_caller_identity", service_error_code="InvalidClientTokenId", http_status_code=403))
    with pytest.raises(deploy.DeployError, match="InvalidClientTokenId"):
        deploy.aws_identity()


def test_aws_profile_khong_ton_tai():
    with pytest.raises(deploy.DeployError, match="Không có AWS profile"):
        deploy.aws_identity(profile="profile-khong-ton-tai-xyz")


# ================================================================ Bước 2
PLAN_JSON = {"resource_changes": [
    {"address": "module.notification.aws_sns_topic.this", "module_address": "module.notification",
     "change": {"actions": ["create"]}},
    {"address": "module.ttl.module.function.aws_lambda_function.this",
     "module_address": "module.ttl.module.function", "change": {"actions": ["create"]}},
    {"address": "aws_iam_role.scheduler", "change": {"actions": ["update"]}},
    {"address": "module.ttl.aws_scheduler_schedule.this", "module_address": "module.ttl",
     "change": {"actions": ["delete", "create"]}},
    {"address": "module.alert.aws_cloudtrail.this", "module_address": "module.alert",
     "change": {"actions": ["delete"]}},
    {"address": "data.aws_caller_identity.current", "change": {"actions": ["read"]}},
    {"address": "module.notification.aws_budgets_budget.monthly", "module_address": "module.notification",
     "change": {"actions": ["no-op"]}},
]}


def test_summarize_plan_dem_dung_tung_loai():
    s = deploy.summarize_plan(PLAN_JSON)
    assert (s["create"], s["update"], s["replace"], s["delete"]) == (2, 1, 1, 1)
    # tài nguyên của module con được gom vào module cha; no-op và read không tính
    assert s["by_module"] == {"module.notification": 1, "module.ttl": 2, "(gốc)": 1, "module.alert": 1}


def test_khong_co_thay_doi():
    s = deploy.summarize_plan({"resource_changes": [{"change": {"actions": ["no-op"]}}]})
    assert not deploy.has_changes(s)


@pytest.fixture
def tf_dir(tmp_path, monkeypatch):
    """Thư mục Terraform giả: có terraform.tfvars.json, chưa có backend.tf."""
    monkeypatch.setattr(deploy, "TF_DIR", tmp_path)
    monkeypatch.setattr(deploy, "TFVARS_FILE", tmp_path / "terraform.tfvars.json")
    monkeypatch.setattr(deploy, "BACKEND_FILE", tmp_path / "backend.tf")
    monkeypatch.setattr(deploy, "BACKEND_HCL", tmp_path / "backend.hcl")
    (tmp_path / "terraform.tfvars.json").write_text('{"alert_email": "a@b.co"}')
    return tmp_path


def test_nguoi_dung_cuoi_dung_state_tren_may(tf_dir):
    assert deploy.backend_mode() == "local"
    assert not any(a.startswith("-backend-config") for a in deploy.init_args())


def test_nhom_phat_trien_dung_s3(tf_dir):
    (tf_dir / "backend.tf").write_text('terraform { backend "s3" {} }')
    (tf_dir / "backend.hcl").write_text('bucket = "x"')
    assert deploy.backend_mode() == "s3"
    assert "-backend-config=backend.hcl" in deploy.init_args()


def test_co_backend_tf_ma_thieu_backend_hcl(tf_dir):
    (tf_dir / "backend.tf").write_text('terraform { backend "s3" {} }')
    with pytest.raises(deploy.DeployError, match="thiếu backend.hcl"):
        deploy.init_args()


class FakeTerraform:
    """Thay run_tf: ghi lại các lệnh đã gọi, trả kết quả giả cho show / output."""

    def __init__(self, plan_json, tf_dir):
        self.plan_json, self.tf_dir, self.calls = plan_json, tf_dir, []

    def __call__(self, tf, args, profile=None, capture=True):
        self.calls.append(args[0])
        if args[0] == "plan":
            (self.tf_dir / deploy.PLAN_FILE).write_text("plan")
        if args[0] == "show":
            return json.dumps(self.plan_json)
        if args[0] == "output":
            return json.dumps({"topic_arn": {"value": "arn:aws:sns:us-east-1:123456789012:Cost-Guardian"}})
        return ""


ENV = {"terraform": "terraform", "account": "123456789012"}


def test_deploy_dong_y_thi_apply_va_xoa_file_plan(tf_dir, monkeypatch, capsys):
    fake = FakeTerraform(PLAN_JSON, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", fake)
    deploy.deploy(ENV, ask=lambda q: "yes")
    assert fake.calls == ["init", "plan", "show", "apply", "output"]
    assert not (tf_dir / deploy.PLAN_FILE).exists()
    out = capsys.readouterr().out
    assert "Confirm subscription" in out and "CẢNH BÁO" in out


def test_deploy_tu_choi_thi_khong_apply(tf_dir, monkeypatch):
    fake = FakeTerraform(PLAN_JSON, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", fake)
    with pytest.raises(deploy.DeployError, match="Đã hủy"):
        deploy.deploy(ENV, ask=lambda q: "y")       # phải gõ đúng 'yes'
    assert "apply" not in fake.calls
    assert not (tf_dir / deploy.PLAN_FILE).exists()


def test_deploy_khong_co_thay_doi_thi_khong_hoi(tf_dir, monkeypatch):
    fake = FakeTerraform({"resource_changes": []}, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", fake)
    deploy.deploy(ENV, ask=lambda q: pytest.fail("không được hỏi xác nhận"))
    assert "apply" not in fake.calls


def test_deploy_yes_khong_hoi(tf_dir, monkeypatch):
    fake = FakeTerraform(PLAN_JSON, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", fake)
    deploy.deploy(ENV, assume_yes=True, ask=lambda q: pytest.fail("không được hỏi xác nhận"))
    assert "apply" in fake.calls


def test_thieu_file_cau_hinh(tf_dir, monkeypatch):
    (tf_dir / "terraform.tfvars.json").unlink()
    monkeypatch.setattr(deploy, "run_tf", FakeTerraform(PLAN_JSON, tf_dir))
    with pytest.raises(deploy.DeployError, match="Chưa có cấu hình"):
        deploy.plan_only(ENV)


def test_run_tf_bao_loi_kem_chi_tiet(monkeypatch):
    monkeypatch.setattr(deploy.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="Error: No valid credential sources found"))
    with pytest.raises(deploy.DeployError, match="No valid credential"):
        deploy.run_tf("terraform", ["init"])


# ================================================================ Bước 3 – destroy
DESTROY_JSON = {"resource_changes": [
    {"address": "module.notification.aws_sns_topic.this", "module_address": "module.notification",
     "change": {"actions": ["delete"]}},
    {"address": "module.ttl.module.function.aws_lambda_function.this",
     "module_address": "module.ttl.module.function", "change": {"actions": ["delete"]}},
    {"address": "aws_iam_role.scheduler", "change": {"actions": ["delete"]}},
    {"address": "data.aws_caller_identity.current", "change": {"actions": ["read"]}},
]}


def test_destroy_go_dung_so_tai_khoan_thi_xoa(tf_dir, monkeypatch, capsys):
    fake = FakeTerraform(DESTROY_JSON, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", fake)
    deploy.destroy(ENV, ask=lambda q: " 123456789012 ")
    assert fake.calls == ["init", "plan", "show", "apply"]
    assert not (tf_dir / deploy.PLAN_FILE).exists()
    out = capsys.readouterr().out
    assert "Sẽ gỡ 3 tài nguyên" in out and "Đã gỡ 3 tài nguyên" in out


def test_destroy_dung_plan_destroy(tf_dir, monkeypatch):
    seen = []
    fake = FakeTerraform(DESTROY_JSON, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", lambda tf, args, profile=None, capture=True:
                        (seen.append(args), fake(tf, args, profile, capture))[1])
    deploy.destroy(ENV, ask=lambda q: "123456789012")
    assert "-destroy" in seen[1]                       # plan -destroy, rồi apply đúng file kế hoạch đó
    assert seen[3][0] == "apply" and seen[3][-1] == deploy.PLAN_FILE


@pytest.mark.parametrize("answer", ["yes", "123", "999999999999", ""])
def test_destroy_go_sai_thi_huy(tf_dir, monkeypatch, answer):
    fake = FakeTerraform(DESTROY_JSON, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", fake)
    with pytest.raises(deploy.DeployError, match="Đã hủy"):
        deploy.destroy(ENV, ask=lambda q: answer)
    assert "apply" not in fake.calls
    assert not (tf_dir / deploy.PLAN_FILE).exists()


def test_destroy_state_trong_thi_khong_hoi(tf_dir, monkeypatch, capsys):
    fake = FakeTerraform({"resource_changes": []}, tf_dir)
    monkeypatch.setattr(deploy, "run_tf", fake)
    deploy.destroy(ENV, ask=lambda q: pytest.fail("không được hỏi xác nhận"))
    assert "apply" not in fake.calls
    assert "không có gì để gỡ" in capsys.readouterr().out


def test_destroy_state_chung_cua_nhom_thi_canh_bao(tf_dir, monkeypatch, capsys):
    (tf_dir / "backend.tf").write_text('terraform { backend "s3" {} }')
    (tf_dir / "backend.hcl").write_text('bucket = "x"')
    monkeypatch.setattr(deploy, "run_tf", FakeTerraform(DESTROY_JSON, tf_dir))
    with pytest.raises(deploy.DeployError):
        deploy.destroy(ENV, ask=lambda q: "no")
    assert "S3 CHUNG" in capsys.readouterr().out


def test_destroy_thieu_file_cau_hinh(tf_dir, monkeypatch):
    (tf_dir / "terraform.tfvars.json").unlink()
    monkeypatch.setattr(deploy, "run_tf", FakeTerraform(DESTROY_JSON, tf_dir))
    with pytest.raises(deploy.DeployError, match="cả khi gỡ"):
        deploy.destroy(ENV, ask=lambda q: "123456789012")


# ================================================================ cost-guardian.py
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("cg_main", os.path.join(ROOT, "cli", "cost-guardian.py"))
cg_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg_main)


def test_lenh_config_khong_kiem_tra_aws(monkeypatch, tf_dir):
    called = []
    monkeypatch.setattr(cg_main.config, "collect_config", lambda path: called.append(path) or path)
    monkeypatch.setattr(deploy, "check_environment", lambda p: pytest.fail("config không cần AWS"))
    assert cg_main.main(["config"]) == 0
    assert called == [deploy.TFVARS_FILE]


def test_deploy_skip_config_thieu_file(monkeypatch, tf_dir):
    (tf_dir / "terraform.tfvars.json").unlink()
    monkeypatch.setattr(deploy, "check_environment", lambda p: ENV)
    assert cg_main.main(["deploy", "--skip-config"]) == 1


def test_lenh_destroy_goi_dung_ham(monkeypatch, tf_dir):
    seen = []
    monkeypatch.setattr(deploy, "check_environment", lambda p: ENV)
    monkeypatch.setattr(deploy, "destroy", lambda env, profile: seen.append((env, profile)))
    assert cg_main.main(["--profile", "cg-b", "destroy"]) == 0
    assert seen == [(ENV, "cg-b")]
