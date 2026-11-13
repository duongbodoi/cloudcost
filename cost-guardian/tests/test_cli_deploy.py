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
