# Lambda `cg-chargeable-event` – Cảnh báo tài nguyên mới

**Phụ trách:** Ngô Xuân Hậu  ·  **Hạng mục:** 5, 6, 7 (GĐ 1) – 15, 16, 17, 24 (GĐ 2)

## File trong thư mục này (do người phụ trách tạo)

| File | Nội dung |
|---|---|
| `lambda_function.py` | Code Lambda, hàm `lambda_handler(event, context)` |
| `event_pattern.json` | Event pattern của rule `cg-chargeable-events` |
| `iam_policy.json` | Quyền của role `cg-chargeable-event-role` |

## Hợp đồng

- **Đầu vào:** sự kiện CloudTrail nguyên bản do rule EventBridge chuyển tới (mục 5 CONVENTIONS).
- **Biến môi trường:** `TOPIC_ARN`.
- **Dùng chung:** `notify.py` (gửi email), `pricing.py` (ước tính chi phí – từ hạng mục 17).
- **Email:** loại `ALERT`, đúng thứ tự trường ở mục 4.2 CONVENTIONS.
- **Quyền tối thiểu:** `sns:Publish` lên topic; từ GĐ 2 thêm API gắn tag (chỉ khóa `Owner`, `CreatedBy`) và `pricing:GetProducts`.

## Kiểm thử

- Sự kiện mẫu: `test_events/chargeable_event/` (`01–07` phải khớp pattern, `neg_*` không khớp).
- Mỗi loại tài nguyên mới (LB, EKS, ElastiCache) phải thêm sự kiện mẫu tương ứng.
