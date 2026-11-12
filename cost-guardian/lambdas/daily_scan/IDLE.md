# Hạng mục 22 — EC2 có thể đang bị bỏ quên (Chiến)

Hợp đồng theo `docs/giai-doan-2.md` mục 3.3:

```python
from idle import find_idle_instances

rows = find_idle_instances(ec2, cloudwatch, now, cpu_threshold, days)
```

- Hai client `ec2`, `cloudwatch` thuộc cùng Region.
- `now` là datetime có múi giờ hoặc chuỗi ISO 8601 có múi giờ; hàm chuẩn hóa sang UTC.
- `cpu_threshold` mặc định của bên gọi là 5; `days` mặc định của bên gọi là 7.
- Kết quả là `list[dict]`, mỗi phần tử có đúng `instance_id`, `instance_type`, `avg_cpu`, `tags`.
- `avg_cpu` tính theo phần trăm, là trung bình các giá trị trung bình ngày; `tags` là list tag gốc của EC2.
- Không thay đổi tài nguyên, không dừng máy, không tự gửi email.

## Cách quyết định

1. Dùng paginator đọc EC2 `running`, kiểm tra lại trạng thái trong response.
2. Xét `days` ngày UTC đã hoàn tất, không dùng ngày hiện tại đang dở.
3. Máy phải bật trước đầu cửa sổ quan sát. Máy mới chạy đủ 7 ngày nhưng thiếu đầu ngày UTC đầu tiên
   có thể phải chờ kỳ báo cáo sau; đây là lựa chọn bảo thủ để tránh dùng trung bình của ngày chạy chưa đủ.
4. `GetMetricData`: namespace `AWS/EC2`, metric `CPUUtilization`, stat `Average`, period 86400,
   gom tối đa 500 máy mỗi request và đọc hết NextToken.
5. Chỉ trả máy có đủ các datapoint ngày và mọi giá trị đều nhỏ hơn ngưỡng. Một ngày bằng 5% cũng bị loại.
6. Thiếu ngày/PartialData chưa hoàn tất/giá trị bất hợp lệ: bỏ qua và ghi log, không điền CPU=0.
   Dữ liệu Average ngày không chứng minh tất cả các mẫu trong ngày đều có mặt; CPU thấp chỉ là gợi ý.
7. Lỗi API/Forbidden/InternalError được ném cho caller để đưa vào mục “Lỗi khi quét” của DAILY.

## Phối hợp tích hợp với Hải

File này mới triển khai helper; chưa sửa `lambda_function.py` của Hải.
Code DAILY hiện có đang dùng `cpu_idle_text()` gọi GetMetricStatistics cho từng máy.
Khi Hải nhận phần này, thay nhánh kiểm tra CPU cũ bằng một lần gọi helper cho mỗi Region,
tránh chạy đồng thời hai cách kiểm tra và báo trùng mục IDLE.

Ví dụ cách chuyển kết quả thành mục DAILY theo cấu trúc code hiện tại của Hải:

```python
try:
    rows = find_idle_instances(ec2, cw, now_utc, IDLE_CPU_THRESHOLD, IDLE_DAYS)
    for row in rows:
        text = (
            f"{row['instance_id']}  {row['instance_type']}  "
            f"CPU trung bình {row['avg_cpu']:.1f}% trong {IDLE_DAYS} ngày  "
            f"{owner_ttl(row['tags'])}"
        )
        out.append(item("IDLE", text))
except Exception as exc:
    errors.append(f"{region} / EC2 (kiểm tra CPU): {type(exc).__name__}: {exc}")
```

Đoạn ví dụ dùng các tên `out`, `item`, `owner_ttl`, `errors` đang có trong code của Hải.
Hải cần tự chọn vị trí ghép sau vòng lặp inventory. Khi `IDLE_DAYS <= 0` được dùng làm công tắc tắt
như code DAILY hiện tại, caller bỏ qua lời gọi helper; helper chỉ nhận days từ 1 đến 365.
IAM role của DAILY cần `cloudwatch:GetMetricData`, không cần quyền stop.
Module `daily_scan` đưa `lambdas/daily_scan/idle.py` vào `common_files` của module Lambda chung,
để file `idle.py` nằm ngang hàng `lambda_function.py` trong ZIP.

## Kiểm thử C1

- Fixture: `test_events/scan/fixtures/idle_cpu_c1.json`.
- Mốc now: 12/11/2026 00:00 UTC; cửa sổ từ 05/11 đến trước 12/11.
- Máy A: 7 ngày đều dưới 5% → được chọn.
- Máy B: mới chạy 3 ngày → không gửi query CloudWatch cho máy này.
- Máy C: một ngày CPU 6% → bị loại.

Tại thư mục `cost-guardian` với dependencies trong requirements-dev.txt đã cài:

```powershell
python -m pytest tests/test_idle.py -q
python -m pytest tests -q
```

Các test dùng botocore.Stubber và dữ liệu giả; không gọi AWS thật.
Fixture này dùng cho pytest; không dán thẳng vào Lambda Test để thay cho metrics trên AWS.
