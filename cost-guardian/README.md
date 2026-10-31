# Cloud Cost Guardian

Công cụ tự động giám sát và tối ưu chi phí AWS – Nhóm 11, môn Điện toán đám mây (2627I_INT3319_1).

> **Đọc [`CONVENTIONS.md`](CONVENTIONS.md) trước khi bắt đầu.**
> Việc đang làm: **[Giai đoạn 1 – phân công và tiêu chí hoàn thành](docs/giai-doan-1.md)**.

## Phân công

| Luồng | Thư mục | Phụ trách |
|---|---|---|
| Cảnh báo tài nguyên mới (ALERT) | `lambdas/chargeable_event/` | Ngô Xuân Hậu |
| Quét hằng ngày (DAILY) | `lambdas/daily_scan/` | Nguyễn Xuân Hải |
| Tự động dừng (TTL) | `lambdas/ttl/` | Trần Mạnh Chiến |
| SNS, Budgets, Terraform, đa Region, review | `terraform/`, `lambdas/common/` | Nguyễn Tùng Dương |

Mỗi thư mục Lambda có `README.md` ghi rõ hạng mục, đầu vào, biến môi trường và cách kiểm thử.

## Cài môi trường

1. **Python 3.12**, **Terraform ≥ 1.10**, **AWS CLI v2**.
2. Đăng nhập AWS bằng IAM user của mình (đã bật MFA):
   ```bash
   aws configure --profile cost-guardian      # Region: us-east-1
   aws sts get-caller-identity --profile cost-guardian
   ```
3. Cài thư viện để chạy test:
   ```bash
   python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements-dev.txt
   python -m pytest tests                                  # phải ra "passed"
   ```

## Code dùng chung – `lambdas/common/`

| File | Dùng để | Ai dùng |
|---|---|---|
| `notify.py` | Bỏ dấu tiêu đề, định dạng giờ / tiền / dòng nội dung, gửi email qua SNS | Cả 3 Lambda |
| `pricing.py` | Ước tính chi phí qua Price List API | ALERT, DAILY |

```python
from notify import send, field_lines, fmt_time, fmt_money
from pricing import estimate

gia = estimate("ec2", "us-east-1", instance_type="t3.small")
lines = field_lines([
    ("Tài nguyên", "EC2  i-0123456789abcdef0"),
    ("Thời điểm", fmt_time("2026-10-31T08:02:41Z")),
    ("Ước tính", fmt_money(gia["hourly"] if gia else None)),
])
send("ALERT", "Tai nguyen tinh phi moi: EC2", lines)
```

**Khi thử trên console Lambda (Giai đoạn 1):** trong trình soạn code của Lambda chọn
**File → New File**, đặt tên `notify.py` (và `pricing.py` nếu cần) **ngang hàng** `lambda_function.py`,
dán nội dung từ repo, bấm **Deploy**. Dùng `pricing.py` thì thêm quyền `pricing:GetProducts` cho role.

Không sửa file trong `common/` cho riêng luồng mình. Cần thêm hoặc đổi hàm thì tạo PR riêng,
vì thay đổi ảnh hưởng cả 3 Lambda.

## Kiểm thử

| Loại | Ở đâu | Cách chạy |
|---|---|---|
| Unit test code dùng chung | `tests/` | `python -m pytest tests` |
| Sự kiện mẫu, fixture từng luồng | `test_events/` | Xem `test_events/README.md` |
| Giá thật từ AWS | `scripts/check_pricing.py` | `python scripts/check_pricing.py` (cần đăng nhập AWS) |

## Quy trình làm việc

1. Tạo nhánh từ `main`, tên tự đặt.
2. Code + thêm sự kiện mẫu / fixture + chạy test.
3. Tạo Pull Request, điền checklist trong mẫu PR.
4. Nhóm trưởng review rồi gộp vào `main`.
