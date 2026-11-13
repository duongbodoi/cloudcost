"""
cost-guardian.py – Điểm vào của CLI Cloud Cost Guardian

    python cli/cost-guardian.py check                 kiểm tra môi trường
    python cli/cost-guardian.py deploy                cấu hình (hỏi đáp) + triển khai
    python cli/cost-guardian.py destroy               gỡ toàn bộ hệ thống

    Tùy chọn chung: --profile <tên>  dùng AWS profile cụ thể

File này chỉ điều phối:
    config.py  (Ngô Xuân Hậu)     – hỏi đáp, sinh terraform.tfvars.json
    deploy.py  (Nguyễn Tùng Dương) – kiểm tra môi trường, chạy Terraform
"""
import argparse
import sys

import deploy


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cost-guardian",
        description="Triển khai hệ thống giám sát chi phí AWS vào tài khoản của bạn.",
    )
    parser.add_argument("--profile", help="AWS profile sẽ dùng (mặc định: biến AWS_PROFILE hoặc 'default')")
    sub = parser.add_subparsers(dest="command", required=True, metavar="LỆNH")

    sub.add_parser("check", help="chỉ kiểm tra môi trường, không thay đổi gì")

    p_deploy = sub.add_parser("deploy", help="cấu hình và triển khai hệ thống")
    p_deploy.add_argument("--yes", action="store_true", help="không hỏi xác nhận trước khi apply")

    sub.add_parser("destroy", help="gỡ toàn bộ hệ thống khỏi tài khoản AWS")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    print("AWS Cost Guardian CLI")
    try:
        if args.command == "check":
            deploy.check_environment(args.profile)
            print("\nMôi trường sẵn sàng.")
        elif args.command == "deploy":
            deploy.check_environment(args.profile)
            raise deploy.DeployError("Lệnh deploy chưa hoàn thiện (Bước 2).")
        elif args.command == "destroy":
            deploy.check_environment(args.profile)
            raise deploy.DeployError("Lệnh destroy chưa hoàn thiện (Bước 3).")
    except deploy.DeployError as e:
        print(f"\n[LỖI] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nĐã hủy.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
