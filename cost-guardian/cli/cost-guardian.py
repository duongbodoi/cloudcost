"""
cost-guardian.py – Điểm vào của CLI Cloud Cost Guardian

    python cli/cost-guardian.py check                 kiểm tra môi trường
    python cli/cost-guardian.py config                chỉ hỏi đáp và lưu cấu hình
    python cli/cost-guardian.py plan                  xem trước sẽ tạo / sửa gì (không thay đổi gì)
    python cli/cost-guardian.py deploy                cấu hình (hỏi đáp) + triển khai
    python cli/cost-guardian.py destroy               gỡ toàn bộ hệ thống

    Tùy chọn chung: --profile <tên>  dùng AWS profile cụ thể

File này chỉ điều phối:
    config.py  (Ngô Xuân Hậu)     – hỏi đáp, ghi terraform.tfvars.json
                                     hợp đồng: config.collect_config(path) -> path đã ghi
    deploy.py  (Nguyễn Tùng Dương) – kiểm tra môi trường, chạy Terraform
"""
import argparse
import sys

import config
import deploy


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cost-guardian",
        description="Triển khai hệ thống giám sát chi phí AWS vào tài khoản của bạn.",
    )
    parser.add_argument("--profile", help="AWS profile sẽ dùng (mặc định: biến AWS_PROFILE hoặc 'default')")
    sub = parser.add_subparsers(dest="command", required=True, metavar="LỆNH")

    sub.add_parser("check", help="chỉ kiểm tra môi trường, không thay đổi gì")
    sub.add_parser("config", help="hỏi đáp và lưu cấu hình (terraform.tfvars.json), không triển khai")
    sub.add_parser("plan", help="xem trước những gì sẽ được tạo / sửa, không thay đổi gì")

    p_deploy = sub.add_parser("deploy", help="cấu hình và triển khai hệ thống")
    p_deploy.add_argument("--yes", action="store_true", help="không hỏi xác nhận trước khi apply")
    p_deploy.add_argument("--skip-config", action="store_true",
                          help="bỏ qua phần hỏi đáp, dùng lại terraform.tfvars.json đang có")

    sub.add_parser("destroy", help="gỡ toàn bộ hệ thống khỏi tài khoản AWS (phải gõ lại số tài khoản)")
    return parser


def prepare_config(skip):
    """Gọi phần hỏi đáp, hoặc dùng lại file cấu hình đang có khi --skip-config."""
    if not skip:
        return config.collect_config(deploy.TFVARS_FILE)
    if not deploy.TFVARS_FILE.exists():
        raise deploy.DeployError(
            f"Chưa có {deploy.TFVARS_FILE.name} nên không thể --skip-config.\n"
            f"  Chạy `config` (hoặc `deploy` không kèm --skip-config) để tạo.")
    deploy.step("Cấu hình")
    deploy.ok(f"Dùng cấu hình có sẵn: {deploy.TFVARS_FILE}")
    return deploy.TFVARS_FILE


def main(argv=None):
    args = build_parser().parse_args(argv)
    print("AWS Cost Guardian CLI")
    try:
        if args.command == "check":
            deploy.check_environment(args.profile)
            print("\nMôi trường sẵn sàng.")
        elif args.command == "config":
            config.collect_config(deploy.TFVARS_FILE)
            print("\nChạy `deploy --skip-config` để triển khai với cấu hình này.")
        elif args.command == "plan":
            env = deploy.check_environment(args.profile)
            deploy.plan_only(env, args.profile)
        elif args.command == "deploy":
            env = deploy.check_environment(args.profile)
            prepare_config(args.skip_config)
            deploy.deploy(env, args.profile, assume_yes=args.yes)
        elif args.command == "destroy":
            env = deploy.check_environment(args.profile)
            deploy.destroy(env, args.profile)
    except deploy.DeployError as e:
        print(f"\n[LỖI] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nĐã hủy.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
