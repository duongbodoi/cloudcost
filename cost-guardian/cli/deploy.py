"""
deploy.py – Phần triển khai của CLI Cost Guardian
==================================================
Phụ trách: Nguyễn Tùng Dương (hạng mục 31)

Nhận file terraform.tfvars.json do config.py (Ngô Xuân Hậu) sinh ra, rồi:
  1. kiểm tra môi trường (Terraform, đăng nhập AWS)          ← Bước 1
  2. chuẩn bị Terraform: state lưu trên máy, terraform init   ← Bước 2
  3. plan → hỏi xác nhận → apply → nhắc xác nhận email         ← Bước 2
  4. destroy có xác nhận kỹ                                    ← Bước 3

Mọi lỗi người dùng cần biết đều ném DeployError với thông báo dễ hiểu;
cost-guardian.py bắt lỗi này, in ra và thoát với mã 1.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

MIN_TERRAFORM = (1, 10, 0)

# cli/deploy.py -> thư mục gốc dự án -> terraform/main
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TF_DIR = PROJECT_ROOT / "terraform" / "main"


class DeployError(Exception):
    """Lỗi người dùng có thể tự sửa – thông báo đã viết sẵn để in thẳng ra màn hình."""


# ----------------------------------------------------------------------- hiển thị
def step(msg):
    print(f"\n==> {msg}")


def ok(msg):
    print(f"   [OK] {msg}")


def info(msg):
    print(f"        {msg}")


# ----------------------------------------------------------------------- Terraform
def find_terraform():
    """Tìm lệnh terraform trong PATH."""
    path = shutil.which("terraform")
    if not path:
        raise DeployError(
            "Không tìm thấy Terraform.\n"
            "  Cài đặt: https://developer.hashicorp.com/terraform/install\n"
            "  Windows: winget install Hashicorp.Terraform   |   macOS: brew install hashicorp/tap/terraform"
        )
    return path


def parse_version(text):
    """'1.16.6' -> (1, 16, 6). Bỏ phần đuôi kiểu '-beta1'."""
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        raise DeployError(f"Không đọc được phiên bản Terraform: {text!r}")
    return tuple(int(x) for x in m.groups())


def terraform_version(tf):
    """Hỏi phiên bản Terraform bằng `terraform version -json`."""
    try:
        out = subprocess.run([tf, "version", "-json"], capture_output=True, text=True,
                             timeout=60, check=True).stdout
        return parse_version(json.loads(out)["terraform_version"])
    except (subprocess.SubprocessError, ValueError, KeyError) as e:
        raise DeployError(f"Không chạy được `terraform version`: {e}") from e


def check_terraform():
    tf = find_terraform()
    version = terraform_version(tf)
    if version < MIN_TERRAFORM:
        need = ".".join(map(str, MIN_TERRAFORM))
        have = ".".join(map(str, version))
        raise DeployError(f"Terraform {have} quá cũ, cần từ {need} trở lên. Hãy cập nhật Terraform.")
    return tf, version


# ----------------------------------------------------------------------- AWS
def aws_identity(profile=None):
    """Hỏi AWS 'tôi là ai' – cùng chuỗi thông tin đăng nhập mà Terraform sẽ dùng."""
    try:
        import boto3
        from botocore.exceptions import (BotoCoreError, ClientError, NoCredentialsError,
                                         ProfileNotFound)
    except ImportError as e:
        raise DeployError("Thiếu thư viện boto3. Chạy: pip install -r cli/requirements.txt") from e

    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        ident = session.client("sts").get_caller_identity()
    except ProfileNotFound as e:
        raise DeployError(f"Không có AWS profile '{profile}'. Tạo bằng: aws configure --profile {profile}") from e
    except NoCredentialsError as e:
        raise DeployError(
            "Chưa đăng nhập AWS.\n"
            "  Chạy `aws configure` (hoặc `aws sso login`), hoặc truyền --profile <tên profile>."
        ) from e
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("InvalidClientTokenId", "SignatureDoesNotMatch", "ExpiredToken"):
            raise DeployError(
                f"AWS từ chối thông tin đăng nhập ({code}).\n"
                "  Access key sai, đã bị vô hiệu hóa hoặc phiên tạm thời đã hết hạn.\n"
                "  Kiểm tra cả biến môi trường AWS_ACCESS_KEY_ID – nó được ưu tiên hơn profile."
            ) from e
        raise DeployError(f"Lỗi khi kiểm tra đăng nhập AWS: {e}") from e
    except BotoCoreError as e:
        raise DeployError(f"Không kết nối được AWS: {e}") from e

    return {
        "account": ident["Account"],
        "arn": ident["Arn"],
        "region": session.region_name,
        "profile": profile or os.environ.get("AWS_PROFILE") or "default",
    }


# ----------------------------------------------------------------------- tổng hợp
def check_environment(profile=None):
    """Bước 1 của mọi lệnh: Terraform đủ mới, đã đăng nhập AWS, có thư mục Terraform."""
    step("Kiểm tra môi trường")

    tf, version = check_terraform()
    ok(f"Terraform {'.'.join(map(str, version))}  ({tf})")

    if not (TF_DIR / "main.tf").exists():
        raise DeployError(f"Không tìm thấy mã Terraform ở {TF_DIR}")
    ok(f"Mã Terraform: {TF_DIR}")

    ident = aws_identity(profile)
    ok(f"Tài khoản AWS: {ident['account']}")
    info(f"Danh tính   : {ident['arn']}")
    info(f"Profile     : {ident['profile']}")
    if ident["region"] and ident["region"] != "us-east-1":
        info(f"Lưu ý: Region mặc định của profile là {ident['region']}; hệ thống vẫn được đặt "
             f"theo biến home_region (mặc định us-east-1).")

    return {"terraform": tf, "version": version, **ident}
