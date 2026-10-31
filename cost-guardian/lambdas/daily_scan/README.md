# Lambda `cg-daily-scan` – Báo cáo quét hằng ngày

**Phụ trách:** Nguyễn Xuân Hải  ·  **Hạng mục:** 8, 9 (GĐ 1) – 18, 19, 20, 21, 26 (GĐ 2)

## File trong thư mục này (do người phụ trách tạo)

| File | Nội dung |
|---|---|
| `lambda_function.py` | Code Lambda, hàm `lambda_handler(event, context)` |
| `iam_policy.json` | Quyền của role `cg-daily-scan-role` (chỉ đọc) |

## Hợp đồng

- **Đầu vào:** `{"source": "cost-guardian.scheduler", "job": "daily-scan"}`; khi test thêm `"dry_run": true`.
- **Biến môi trường:** `TOPIC_ARN`, `ALL_REGIONS`, `SNAPSHOT_AGE_DAYS`, `IDLE_CPU_THRESHOLD`, `IDLE_DAYS`.
- **Dùng chung:** `notify.py`, `pricing.py` (Hải là người viết bản hoàn chỉnh – hạng mục 18).
- **Email:** loại `DAILY`, cấu trúc ở mục 4.2 CONVENTIONS; **luôn gửi** kể cả khi không có tài nguyên.
- **Mỗi Region / mỗi loại** bọc `try/except` riêng; lỗi ghi vào mục "Lỗi khi quét".
- **Timeout khởi điểm:** 5 phút, 256 MB.

## Kiểm thử

- Fixture: `test_events/scan/fixtures/` (+ thêm fixture cho LB, VPC Endpoint, snapshot).
- Ước tính chi phí: chạy `python scripts/check_pricing.py` trên AWS thật, sửa bộ lọc loại nào còn `FAIL`.
