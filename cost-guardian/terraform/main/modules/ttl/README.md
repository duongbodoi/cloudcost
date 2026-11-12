# Module TTL — Trần Mạnh Chiến, hạng mục 27

Module nhận đúng 7 biến và trả đúng 2 output của khung Dương đã tạo.
Không sửa root, module `lambda_function`, SNS, Budget hoặc role Scheduler dùng chung.

## Tài nguyên và tên theo CONVENTIONS

- Gọi `module.function` từ `../lambda_function` để tạo Lambda, IAM và log group.
- Với `name_prefix=cg`: function `cg-ttl`, role `cg-ttl-role`, inline policy `cg-ttl-policy`.
- Python 3.12, timeout 120 giây, memory 128 MB, log giữ 14 ngày theo yêu cầu giai đoạn 2.
- ZIP lấy `lambdas/ttl/lambda_function.py` và `lambdas/common/notify.py`, hai file ngang hàng.
- Biến môi trường chỉ gồm `TOPIC_ARN`, `ALL_REGIONS`; giá trị true/false được chuyển thành chuỗi.
- Scheduler `cg-ttl-check-schedule`, group `default`, flexible window OFF, múi giờ UTC.
- Lịch `rate(<check_interval_minutes> minutes)`, mặc định nhóm truyền 30.
- `enabled=false` chỉ đổi state thành DISABLED; không dùng count để xóa lịch hoặc Lambda.
- Scheduler dùng `scheduler_role_arn` từ root, không tạo role riêng.
- Payload chính xác: `{"source":"cost-guardian.scheduler","job":"ttl-check"}`.

## Hợp đồng đầu vào và đầu ra

Đầu vào: `name_prefix`, `topic_arn`, `lambdas_dir`, `scheduler_role_arn`,
`monitor_all_regions`, `enabled`, `check_interval_minutes`. Mọi biến giữ nguyên tên và kiểu.

Đầu ra: `function_arn` từ module Lambda chung và `schedule_arn` của lịch TTL.
Không có ARN, số tài khoản hoặc Region cố định trong main.tf: lấy từ biến/data source.
SNS phải cùng tài khoản và Region với function vì code TTL dùng cùng Region trung tâm.

IAM tương ứng với `lambdas/ttl/iam_policy.json`: đọc inventory đa Region; stop EC2 có TTL và không thuộc ASG;
stop RDS/Aurora có TTL; chỉ thêm/xóa khóa TTL-Start; publish đúng topic truyền vào.
Không có quyền terminate, xóa DB, xóa snapshot hoặc chỉnh tag TTL/Owner.

## Kiểm chứng offline

Đã chạy trên bản sao mã nguồn giữ nguyên nội dung vì sandbox Windows không cho đặt working directory trên ổ D:

- Terraform 1.17.1, AWS provider 6.72.0, archive 2.8.1 theo lock file hiện tại của root.
- `terraform fmt -check -recursive`: đạt.
- `terraform validate`: đạt cho module và cho root hiện có (không khởi tạo backend).
- `terraform test`: 7 bài test với mock provider, không dùng credentials hoặc gọi AWS:
  tên/payload/role chung; C4 tắt lịch; tag guards; đổi chu kỳ; từ chối chu kỳ phân số;
  từ chối topic sai Region; plan lần dựng mới khi ARN topic còn chưa biết.
- Test không chứng minh IAM, shared backend hay lịch trên AWS thật đã hoạt động.

Khi chạy test module trên máy của bạn (chạy tại thư mục module này):

```powershell
New-Item -ItemType Directory -Path .build -Force | Out-Null
terraform init -backend=false
terraform validate
terraform test
```

Lệnh test dùng mock AWS. Archive provider vẫn đóng gói file ZIP cục bộ.
Không commit `.terraform`, `.build`, ZIP hoặc state. Lock file dùng cho triển khai vẫn là file tại root
`terraform/main/.terraform.lock.hcl`; nếu init module để test sinh thêm lock file riêng thì không cần đưa nó vào PR.

## Trước khi plan/apply môi trường chung

1. Đăng nhập đúng profile `cost-guardian`, kiểm tra tài khoản bằng STS. Không đưa credentials vào repo.
2. Tại `terraform/main`, dùng backend S3 có sẵn của nhóm và `use_lockfile=true`, không tạo state riêng.
3. `module.notification.topic_arn` phải trả ARN thật. Bản root đọc khi viết module này vẫn là TODO và trả null;
   `validate` có thể đạt nhưng `plan` tích hợp không thể tạo TTL với topic null. Dương cần hoàn thiện module
   notification hoặc cung cấp bản cập nhật đã triển khai. Không sửa root để chèn tạm ARN cố định.
4. Kiểm tra tài nguyên tạo tay GĐ1 trùng tên: Lambda, role, inline policy, log group và lịch.
   Báo nhóm trưởng để thống nhất import hoặc xử lý tài nguyên cũ. Không tự xóa hay tạo song song lịch trùng.
5. Chạy `terraform plan` và đọc toàn bộ diff. `-target` có thể kéo theo các dependency của TTL,
   không bảo đảm chỉ có resource bên trong TTL bị tác động. Review cả dependency trước khi đồng ý triển khai.
6. Theo docs/giai-doan-2.md, báo nhóm trước khi chạy thử `apply -target="module.ttl"`.
   Không chạy full apply/destroy hoặc force-unlock. Chưa có apply nào được thực hiện trong bước viết module này.
7. Sau triển khai: dry-run, C4 và C5 (bài T2 của GĐ1), lưu ảnh email cùng plan trong PR.
   Bài Aurora thật C2 vẫn cần xác minh riêng; fixture C3 không tạo cluster thật.

## Địa chỉ tài nguyên khi cần import

Danh sách để nhóm trưởng đối chiếu, không phải hướng dẫn xóa tài nguyên:

- `module.ttl.module.function.aws_lambda_function.this`: function `cg-ttl`.
- `module.ttl.module.function.aws_iam_role.this`: role `cg-ttl-role`.
- `module.ttl.module.function.aws_iam_role_policy.this[0]`: inline policy `cg-ttl-role:cg-ttl-policy`.
- `module.ttl.module.function.aws_iam_role_policy_attachment.logs`: attachment ghi log.
- `module.ttl.module.function.aws_cloudwatch_log_group.this`: `/aws/lambda/cg-ttl`.
- `module.ttl.aws_scheduler_schedule.this`: `default/cg-ttl-check-schedule`.

Role `cg-scheduler-role` nằm ở root (`aws_iam_role.scheduler`) và không thuộc module TTL.
Code dùng chung và backend không được đổi trong PR TTL này.
