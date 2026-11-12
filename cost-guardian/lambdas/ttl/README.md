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

## Giai đoạn 2 — hạng mục 23

- Đã dùng nguyên `lambdas/common/notify.py` để tạo/gửi email: `send`, `fmt_time`, `field_lines`, `bullet`.
- Chủ đề đúng mẫu `Da dung N tai nguyen het han` hoặc `Loi xu ly TTL (N)`; có cả hành động và lỗi
  thì gom trong một email, ưu tiên chủ đề số tài nguyên được yêu cầu dừng.
- `stop_requested` nghĩa là AWS đã chấp nhận yêu cầu stop, chưa bảo đảm trạng thái cuối đã là stopped.
- Aurora được xử lý ở cấp cluster bằng `StopDBCluster`; không stop riêng instance thành viên.
- Gắn/xóa `TTL-Start` trên cluster ARN; cluster stopped còn sót tag sẽ được dọn.
- Bỏ qua Aurora Serverless v1 (`EngineMode=serverless`), trạng thái đang bận và topology global/cross-Region.
  Trường hợp global được bỏ qua bảo thủ, kể cả một số topology có thể được AWS cho phép stop.
- `dry_run=true` không stop, không gắn/xóa tag, không publish SNS; `notification` có thể chứa bản xem trước
  email với `sent=false`. Nếu không có hành động/lỗi thì `notification=null`.
- Tham số `now` chỉ dùng cho dry-run trên Lambda thật; các test gọi worker với đồng hồ cố định để mô phỏng.
- SNS lỗi sẽ làm invocation lỗi. Chưa có cơ chế lưu thông báo bền vững để bảo đảm retry gửi lại mail
  nếu tài nguyên đã chuyển sang stopping/stopped. Khi thiếu mail cần kiểm tra log.

### Các file kiểm thử

- `test_events/ttl/fixtures/describe_db_clusters.json`: Aurora hết hạn/chưa hết hạn, lần đầu quét,
  đã dừng còn tag, Serverless v1, đang bận và không có TTL.
- `tests/test_ttl.py`: Stubber kiểm tra các API stop/tag đúng cluster ARN, dry-run không ghi,
  lỗi stop giữ đồng hồ, email dùng chung, phân trang, hồi quy EC2/RDS và hợp đồng biến môi trường.

Từ thư mục `cost-guardian`:

```powershell
python -m pytest tests/test_ttl.py -q
python -m pytest tests -q
```

Fixture chạy trong pytest, không dán response Describe vào ô Event trên Lambda console.
Khi test console, dùng event `{"source":"cost-guardian.scheduler","job":"ttl-check","dry_run":true}`;
Lambda sẽ đọc inventory AWS thật.

### Đóng gói và cấu hình cần có trước khi Deploy

- ZIP chứa `lambda_function.py` của luồng TTL và `notify.py` từ common, ngang hàng ở gốc ZIP.
- Function `cg-ttl`, role `cg-ttl-role`, policy `cg-ttl-policy`.
- Biến môi trường `TOPIC_ARN` là ARN thực tế, `ALL_REGIONS=true` (hoặc false để test một Region).
  Không dùng các tên biến cũ `SNS_TOPIC_ARN`, `REGIONS`.
- `iam_policy.json` đã có quyền cluster, dùng `<ACCOUNT_ID>` khi lưu Git; điền tài khoản thật khi dùng console.
- `ec2:StopInstances` yêu cầu `aws:ResourceTag/TTL`, đồng thời loại trừ tag ASG.
- Module TTL phải lấy Scheduler role chung qua `scheduler_role_arn`, không tạo role Scheduler riêng.
- Kiểm thử offline C2/C3 chưa thay thế bằng chứng dừng Aurora thật trong bài C2.
  Chưa có Terraform apply hay Deploy AWS nào được thực hiện trong bước viết code này.
