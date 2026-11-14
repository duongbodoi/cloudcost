"""
deploy.py – Phần triển khai của CLI Cost Guardian
==================================================
Phụ trách: Nguyễn Tùng Dương (hạng mục 31)

Nhận file terraform.tfvars.json do config.py (Ngô Xuân Hậu) sinh ra, rồi:
  1. kiểm tra môi trường (Terraform, đăng nhập AWS)          ← Bước 1
  2. terraform init: người dùng cuối lưu state trên máy,
     nhóm phát triển dùng backend S3 (nếu có file backend.tf)  ← Bước 2
  3. plan → tóm tắt → hỏi xác nhận → apply → nhắc xác nhận email ← Bước 2
  4. plan -destroy → gõ lại số tài khoản → gỡ toàn bộ             ← Bước 3

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
TFVARS_FILE = TF_DIR / "terraform.tfvars.json"   # config.py (Hậu) ghi file này
BACKEND_FILE = TF_DIR / "backend.tf"             # chỉ nhóm phát triển có (không commit)
BACKEND_HCL = TF_DIR / "backend.hcl"
PLAN_FILE = "cli.tfplan"                         # nằm trong TF_DIR, bị .gitignore chặn


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


# =======================================================================
# Bước 2 – init / plan / apply
# =======================================================================
def tf_env(profile=None):
    """Biến môi trường cho Terraform: dùng đúng profile đã kiểm tra, không hỏi gì giữa chừng."""
    env = os.environ.copy()
    if profile:
        env["AWS_PROFILE"] = profile
    env["TF_IN_AUTOMATION"] = "1"   # bớt các dòng gợi ý "hãy chạy lệnh ..." của Terraform
    env["TF_INPUT"] = "0"           # thiếu biến thì báo lỗi thay vì treo chờ nhập
    return env


def run_tf(tf, args, profile=None, capture=True):
    """Chạy `terraform -chdir=<TF_DIR> <args>`.

    capture=True : gom output lại (dùng cho init, show, output) – chỉ in ra khi lỗi.
    capture=False: in thẳng ra màn hình (dùng cho apply/destroy để người dùng thấy tiến trình).
    """
    cmd = [tf, f"-chdir={TF_DIR}", *args]
    kwargs = {"env": tf_env(profile)}
    if capture:
        kwargs.update(capture_output=True, text=True, encoding="utf-8", errors="replace")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        detail = ((result.stderr or "") + (result.stdout or "")).strip() if capture else ""
        raise DeployError(f"`terraform {args[0]}` thất bại (mã {result.returncode}).\n{detail}".rstrip())
    return result.stdout if capture else ""


def backend_mode():
    """'s3' khi máy có backend.tf (nhóm phát triển), 'local' với người dùng cuối."""
    return "s3" if BACKEND_FILE.exists() else "local"


def init_args():
    # -reconfigure: dùng đúng backend hiện tại, không hỏi "có chép state cũ sang không"
    # (không bao giờ xóa state cũ – chỉ không chép).
    args = ["init", "-input=false", "-no-color", "-reconfigure"]
    if backend_mode() == "s3":
        if not BACKEND_HCL.exists():
            raise DeployError(
                f"Có {BACKEND_FILE.name} (backend S3 của nhóm) nhưng thiếu {BACKEND_HCL.name}.\n"
                f"  Chép từ mẫu: Copy-Item backend.hcl.example backend.hcl (trong {TF_DIR})")
        args.append(f"-backend-config={BACKEND_HCL.name}")
    return args


def terraform_init(tf, profile=None):
    step("Chuẩn bị Terraform")
    run_tf(tf, init_args(), profile)
    if backend_mode() == "s3":
        ok("Đã khởi tạo – state lưu trên S3 của nhóm (backend.tf)")
    else:
        ok(f"Đã khởi tạo – state lưu trên máy: {TF_DIR / 'terraform.tfstate'}")


def summarize_plan(plan_json):
    """Đếm thay đổi trong `terraform show -json <plan>`.

    Trả về {"create": n, "update": n, "delete": n, "replace": n, "by_module": {...}}
    """
    counts = {"create": 0, "update": 0, "delete": 0, "replace": 0}
    by_module = {}
    for rc in plan_json.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        if set(actions) == {"create", "delete"}:
            kind = "replace"
        elif actions == ["create"]:
            kind = "create"
        elif actions == ["update"]:
            kind = "update"
        elif actions == ["delete"]:
            kind = "delete"
        else:                       # no-op, read
            continue
        counts[kind] += 1
        module = rc.get("module_address") or "(gốc)"
        module = module.split(".module.")[0]          # gom tài nguyên của module con vào module cha
        by_module[module] = by_module.get(module, 0) + 1
    counts["by_module"] = by_module
    return counts


def terraform_plan(tf, profile=None):
    """Chạy plan, lưu ra PLAN_FILE, in tóm tắt. Trả về số liệu tóm tắt."""
    step("Xem trước thay đổi (terraform plan)")
    if not TFVARS_FILE.exists():
        raise DeployError(
            f"Chưa có cấu hình {TFVARS_FILE.name}.\n"
            f"  Chạy lại `deploy` để trả lời câu hỏi cấu hình, hoặc chép từ mẫu {TFVARS_FILE.name}.example")
    run_tf(tf, ["plan", "-input=false", "-no-color", f"-out={PLAN_FILE}"], profile)
    summary = summarize_plan(json.loads(run_tf(tf, ["show", "-json", PLAN_FILE], profile)))

    ok(f"Tạo mới: {summary['create']}   Sửa: {summary['update']}   "
       f"Thay thế: {summary['replace']}   Xóa: {summary['delete']}")
    for module, n in sorted(summary["by_module"].items()):
        info(f"{module}: {n} thay đổi")
    if summary["delete"] or summary["replace"]:
        info("CẢNH BÁO: kế hoạch có XÓA hoặc THAY THẾ tài nguyên. Đọc kỹ trước khi đồng ý.")
    return summary


def has_changes(summary):
    return any(summary[k] for k in ("create", "update", "delete", "replace"))


def confirm(question, expected="yes", ask=input):
    """Hỏi người dùng; chỉ chấp nhận đúng chuỗi expected."""
    answer = ask(f"\n{question} Gõ '{expected}' để đồng ý: ").strip()
    if answer != expected:
        raise DeployError("Đã hủy theo yêu cầu – không có gì được thay đổi.")


def terraform_apply(tf, profile=None):
    step("Triển khai (terraform apply)")
    run_tf(tf, ["apply", "-input=false", "-compact-warnings", PLAN_FILE], profile, capture=False)
    ok("Triển khai xong")


def read_outputs(tf, profile=None):
    raw = json.loads(run_tf(tf, ["output", "-json"], profile) or "{}")
    return {k: v.get("value") for k, v in raw.items()}


def remove_plan_file():
    try:
        (TF_DIR / PLAN_FILE).unlink()
    except FileNotFoundError:
        pass


def deploy(env, profile=None, assume_yes=False, ask=input):
    """Toàn bộ luồng triển khai sau khi đã kiểm tra môi trường (env = kết quả check_environment)."""
    tf = env["terraform"]
    terraform_init(tf, profile)
    try:
        summary = terraform_plan(tf, profile)
        if not has_changes(summary):
            ok("Hệ thống đã đúng như cấu hình – không có gì cần thay đổi.")
            return summary
        if not assume_yes:
            confirm(f"Triển khai {summary['create'] + summary['update'] + summary['replace']} "
                    f"thay đổi vào tài khoản {env['account']}?", ask=ask)
        terraform_apply(tf, profile)
    finally:
        remove_plan_file()

    outputs = read_outputs(tf, profile)
    step("Hoàn tất")
    if outputs.get("topic_arn"):
        info(f"SNS topic: {outputs['topic_arn']}")
    print("\n   VIỆC CẦN LÀM NGAY: mở email, tìm thư 'AWS Notification - Subscription Confirmation'")
    print("   và bấm 'Confirm subscription'. Chưa xác nhận thì hệ thống KHÔNG gửi được thông báo.")
    print("   (Không thấy thư thì xem thư mục Spam.)")
    if backend_mode() == "local":
        print(f"\n   Giữ nguyên thư mục {TF_DIR} – file terraform.tfstate trong đó cần cho lệnh destroy.")
    return summary


def plan_only(env, profile=None):
    """Lệnh `plan`: xem trước, không thay đổi gì."""
    tf = env["terraform"]
    terraform_init(tf, profile)
    try:
        return terraform_plan(tf, profile)
    finally:
        remove_plan_file()


# =======================================================================
# Bước 3 – destroy
# =======================================================================
def terraform_plan_destroy(tf, profile=None):
    """`plan -destroy` lưu ra PLAN_FILE, in danh sách sẽ gỡ theo module. Trả về số liệu tóm tắt."""
    step("Xem trước những gì sẽ bị gỡ (terraform plan -destroy)")
    if not TFVARS_FILE.exists():
        # Terraform vẫn cần giá trị các biến bắt buộc (alert_email) kể cả khi xóa
        raise DeployError(
            f"Thiếu {TFVARS_FILE.name} – Terraform cần file này cả khi gỡ.\n"
            f"  Chạy `config` để tạo lại (giá trị không cần giống lúc triển khai), rồi chạy lại `destroy`.")
    run_tf(tf, ["plan", "-destroy", "-input=false", "-no-color", f"-out={PLAN_FILE}"], profile)
    summary = summarize_plan(json.loads(run_tf(tf, ["show", "-json", PLAN_FILE], profile)))
    if summary["delete"]:
        ok(f"Sẽ gỡ {summary['delete']} tài nguyên:")
        for module, n in sorted(summary["by_module"].items()):
            info(f"{module}: {n}")
    return summary


def destroy(env, profile=None, ask=input):
    """Gỡ toàn bộ hệ thống. Người dùng phải gõ lại đúng số tài khoản AWS mới chạy."""
    tf = env["terraform"]
    account = env["account"]
    terraform_init(tf, profile)
    try:
        summary = terraform_plan_destroy(tf, profile)
        if not summary["delete"]:
            ok("Không có tài nguyên Cost Guardian nào trong state – không có gì để gỡ.")
            if backend_mode() == "local":
                info(f"State đọc từ {TF_DIR / 'terraform.tfstate'}. Nếu đã triển khai từ máy khác hoặc")
                info("thư mục khác, phải chạy destroy ở đúng nơi đó (state nằm ở đó).")
            return summary

        print(f"\n   Tài khoản AWS : {account}")
        if backend_mode() == "s3":
            print("   State        : S3 CHUNG của nhóm – gỡ xong thì mọi thành viên đều mất hệ thống này.")
        print("   Sau khi gỡ: không còn cảnh báo, báo cáo, tự dừng theo TTL; đăng ký email bị hủy.")
        print("   Tài nguyên khác trong tài khoản (EC2, RDS... của bạn) KHÔNG bị đụng tới.")
        confirm(f"Gỡ {summary['delete']} tài nguyên – KHÔNG hoàn tác được. "
                f"Để xác nhận, gõ lại số tài khoản.", expected=account, ask=ask)

        step("Gỡ hệ thống (terraform destroy)")
        # apply một kế hoạch -destroy = xóa đúng những gì người dùng vừa xem
        run_tf(tf, ["apply", "-input=false", "-compact-warnings", PLAN_FILE], profile, capture=False)
    finally:
        remove_plan_file()

    step("Hoàn tất")
    ok(f"Đã gỡ {summary['delete']} tài nguyên khỏi tài khoản {account}.")
    info("Kiểm tra lại: AWS Console → Resource Groups & Tag Editor, lọc tag Project = cost-guardian")
    info("(mọi Region) – danh sách phải trống.")
    info(f"File cấu hình {TFVARS_FILE.name} được giữ lại để triển khai lại khi cần.")
    return summary
