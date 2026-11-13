# Module `alert` — Ngô Xuân Hậu, hạng mục 24

Luồng cảnh báo tài nguyên mới (ALERT): **CloudTrail đa Region → EventBridge rule → Lambda → SNS**.
Module nhận đúng 3 biến và trả đúng 2 output của khung nhóm trưởng tạo; không sửa root,
module `lambda_function` hay `lambdas/common/`.

## Tài nguyên tạo ra (với `name_prefix = "cg"`)

| Tài nguyên | Tên | Ghi chú |
|---|---|---|
| Lambda (qua `../lambda_function`) | `cg-chargeable-event` | Python 3.12, 30 giây, 128 MB; zip gồm `chargeable_event/lambda_function.py`, `common/notify.py`, `common/pricing.py` |
| IAM role + inline policy | `cg-chargeable-event-role`, `cg-chargeable-event-policy` | Khớp `lambdas/chargeable_event/iam_policy.json` (trừ `WriteOwnLogs`: đã có qua `AWSLambdaBasicExecutionRole`) |
| Log group | `/aws/lambda/cg-chargeable-event` | Giữ 14 ngày |
| S3 bucket log CloudTrail | `cg-cloudtrail-<account_id>` | `force_destroy = true`, chặn public, mã hóa SSE-S3, xóa log sau 90 ngày, bucket policy chỉ cho đúng trail ghi |
| CloudTrail | `cg-management-trail` | Đa Region, management event **WriteOnly**, bỏ KMS và RDS Data API |
| EventBridge rule | `cg-chargeable-events` | Default bus, `event_pattern = file(".../chargeable_event/event_pattern.json")` |
| Target + `aws_lambda_permission` | – | `events.amazonaws.com` được gọi Lambda, giới hạn theo ARN rule |

Biến môi trường Lambda: chỉ `TOPIC_ARN`. Không có số tài khoản, Region hay ARN cố định: lấy từ biến và data source.

## Hợp đồng

- **Đầu vào:** `name_prefix`, `topic_arn`, `lambdas_dir` (giữ nguyên tên và kiểu, thêm `description` + `validation`).
- **Đầu ra:** `function_arn`, `rule_arn`.
- `event_pattern.json` được **cả module này và `event_forwarder` đọc** – không đổi tên, không chuyển chỗ.

## Kiểm chứng offline (không cần AWS)

Chạy tại thư mục `terraform/main/modules/alert`:

```powershell
New-Item -ItemType Directory -Path .build -Force | Out-Null
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```

`terraform test` dùng mock provider, gồm 7 bài: tên theo CONVENTIONS; rule đọc đúng file pattern dùng chung;
trail đa Region + WriteOnly + bucket `force_destroy`; quyền EventBridge gọi Lambda; IAM khớp `iam_policy.json`
và mọi quyền gắn tag chỉ cho `Owner`, `CreatedBy`; từ chối topic sai Region; plan được khi ARN topic chưa biết
(lần dựng mới).
Không commit `.terraform/`, `.build/`, zip, lock file sinh ra ở thư mục module.

## Trước khi `apply -target="module.alert"` (báo nhóm trước)

1. `$env:AWS_PROFILE = "cost-guardian"`, kiểm tra đúng tài khoản: `aws sts get-caller-identity`.
2. Tại `terraform/main`: `terraform plan -target="module.alert"`, đọc toàn bộ diff.
   `-target` kéo theo `module.notification` (vì cần `topic_arn`).
3. **Tài nguyên tạo tay ở Giai đoạn 1 trên tài khoản cũ** dùng tên khác (`cost-guardian-chargeable-event`,
   rule `cost-guardian-chargeable-events`, trail `cost-guardian-trail`) nên **không** gây lỗi `already exists`,
   nhưng nếu triển khai vào cùng tài khoản đó thì phải:
   - **Tắt rule cũ** (*Disable*), nếu không mỗi sự kiện sẽ có 2 email.
   - **Xóa trail cũ**: trail thứ hai ghi cùng management event sẽ **bị tính phí**.
   - Lambda và role cũ có thể xóa sau khi luồng mới chạy ổn.
4. Sau khi apply: chạy lại bài H1–H6 (`docs/giai-doan-2.md` mục 6), dán đoạn `Plan: ...` và ảnh email vào PR.

## Địa chỉ tài nguyên (khi cần import)

- `module.alert.module.function.aws_lambda_function.this` – `cg-chargeable-event`
- `module.alert.module.function.aws_iam_role.this` – `cg-chargeable-event-role`
- `module.alert.aws_s3_bucket.trail` – `cg-cloudtrail-<account_id>`
- `module.alert.aws_cloudtrail.this` – `cg-management-trail`
- `module.alert.aws_cloudwatch_event_rule.this` – `default/cg-chargeable-events`
