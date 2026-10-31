# Terraform

| Thư mục | Nội dung | Hạng mục |
|---|---|---|
| `bootstrap/` | Bucket S3 lưu Terraform state dùng chung của nhóm (chạy một lần, state lưu local) | 12 – Nguyễn Tùng Dương |
| `main/` | Toàn bộ hệ thống: provider, biến, ghép module | 13, 28 – Nguyễn Tùng Dương |
| `main/modules/notification/` | SNS topic, subscription, topic policy, Budgets | 14 – Nguyễn Tùng Dương |
| `main/modules/alert/` | CloudTrail, S3 log, EventBridge rule, Lambda, IAM, StackSet chuyển tiếp | 24 – Ngô Xuân Hậu, 25 – Nguyễn Tùng Dương |
| `main/modules/daily_scan/` | Lambda Scan, IAM chỉ đọc, Scheduler | 26 – Nguyễn Xuân Hải |
| `main/modules/ttl/` | Lambda TTL, IAM đọc + dừng, Scheduler | 27 – Trần Mạnh Chiến |

Yêu cầu: Terraform ≥ 1.10. Khung `main/` và đầu vào/đầu ra của từng module do nhóm trưởng tạo trước.
Zip Lambda: gom `lambdas/<luồng>/lambda_function.py` + các file trong `lambdas/common/` cần dùng.
