# Lambda `cg-ttl` – Tự động dừng theo TTL

**Phụ trách:** Trần Mạnh Chiến  ·  **Hạng mục:** 10, 11 (GĐ 1) – 22, 23, 27 (GĐ 2)

> Hạng mục 22 (phát hiện EC2 không hoạt động) nằm trong báo cáo DAILY: thống nhất với
> Nguyễn Xuân Hải cách gộp vào `lambdas/daily_scan/` (một hàm riêng trong Lambda Scan).

## File trong thư mục này (do người phụ trách tạo)

| File | Nội dung |
|---|---|
| `lambda_function.py` | Code Lambda, hàm `lambda_handler(event, context)` |
| `iam_policy.json` | Quyền của role `cg-ttl-role` (đọc + dừng) |

## Hợp đồng

- **Đầu vào:** `{"source": "cost-guardian.scheduler", "job": "ttl-check"}`; khi test thêm `"dry_run": true`, `"now": "..."`.
- **Biến môi trường:** `TOPIC_ARN`, `ALL_REGIONS`.
- **Dùng chung:** `notify.py`. (Không cần `pricing.py`.)
- **Quy tắc TTL:** mục 3 CONVENTIONS – EC2 tính từ `LaunchTime`, RDS/Aurora dùng tag `TTL-Start`.
- **Email:** loại `TTL`, chỉ gửi khi có tài nguyên bị dừng hoặc có lỗi, gom vào một email.
- **Quyền:** `ec2:StopInstances` chỉ với máy có tag `TTL` (điều kiện `aws:ResourceTag/TTL`).

## Kiểm thử

- Fixture và bảng kết quả mong đợi: `test_events/ttl/` và `test_events/README.md` mục 3.
- Thêm fixture cho Aurora cluster (`describe_db_clusters`).
