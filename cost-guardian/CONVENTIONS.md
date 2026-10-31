# Quy ước chung – Cloud Cost Guardian

> Mọi thành viên đọc file này **trước khi** tạo tài nguyên hoặc viết code.
> Muốn thay đổi quy ước: tạo PR sửa file này, nhóm trưởng duyệt.

**Trạng thái (31/10/2026):** đã chốt toàn bộ, **trừ** phần đánh dấu ⏳:
- ⏳ Mục 7 – các câu hỏi Budget trên CLI.

Timeout / bộ nhớ Lambda (mục 6) là **giá trị khởi điểm**, được điều chỉnh theo từng giai đoạn.

---

## 1. Môi trường làm việc

| Mục | Quy ước |
|---|---|
| Region trung tâm | **`us-east-1`**. Mọi thành phần của hệ thống đặt ở đây. Chỉ tạo tài nguyên ở Region khác khi thử luồng đa Region. |
| Tài khoản AWS | Một tài khoản dùng chung; mỗi người một IAM user, bật MFA. Không dùng root để làm việc hằng ngày. |
| Budget an toàn | Budget `cost-guardian-lab-safety`, **10 USD/tháng**, ngưỡng 50% / 80% / 100%, tạo **trước** mọi thứ khác. |
| Tài nguyên thử nghiệm | **Bắt buộc** gắn `TTL` (vd `1h`) và `Owner=<tên bạn>` khi tạo. Thử xong thì xóa. |
| Email SNS | **Không bao giờ** bấm link *unsubscribe* ở cuối email. |
| `terraform apply` lên tài khoản chung | Chỉ nhóm trưởng chạy. Thành viên chạy `terraform plan` để xem trước. |

---

## 2. Đặt tên tài nguyên AWS

Tiền tố chung: **`cg-`**. Chữ thường, nối bằng dấu gạch ngang.

| Tài nguyên | Tên |
|---|---|
| SNS Topic | `Cost-Guardian` *(đã chốt – ngoại lệ không theo tiền tố)* |
| Budget của sản phẩm | `cost-guardian-monthly` |
| Lambda | `cg-chargeable-event`, `cg-daily-scan`, `cg-ttl` |
| IAM role của Lambda | `cg-chargeable-event-role`, `cg-daily-scan-role`, `cg-ttl-role` |
| IAM policy | `<tên role bỏ -role>-policy`, vd `cg-ttl-policy` |
| EventBridge rule (us-east-1) | `cg-chargeable-events` |
| EventBridge rule chuyển tiếp (Region khác) | `cg-forward-chargeable-events` |
| IAM role chuyển tiếp | `cg-event-forwarder-role` |
| EventBridge Scheduler | `cg-daily-scan-schedule`, `cg-ttl-check-schedule` |
| IAM role của Scheduler | `cg-scheduler-role` |
| CloudTrail trail | `cg-management-trail` |
| S3 bucket log CloudTrail | `cg-cloudtrail-<account_id>` |
| CloudFormation StackSet | `cg-event-forwarder` |
| S3 bucket Terraform state (chỉ nhóm dùng) | `cost-guardian-tfstate-<account_id>` |
| CloudWatch Log group | `/aws/lambda/<tên Lambda>` (tự sinh), giữ **14 ngày** |

**Tag gắn cho tài nguyên của chính hệ thống** (Terraform `default_tags`):
`Project = cost-guardian`, `ManagedBy = terraform`.

---

## 3. Quy ước tag trên tài nguyên được giám sát

| Khóa | Ai gắn | Định dạng | Ví dụ |
|---|---|---|---|
| `TTL` | Người dùng | `^\s*\d+\s*[mhd]\s*$`, không phân biệt hoa thường. `m` phút, `h` giờ, `d` ngày | `30m`, `4h`, `2d` |
| `TTL-Start` | Lambda TTL (RDS, Aurora) | ISO 8601 UTC `YYYY-MM-DDTHH:MM:SSZ` | `2026-10-31T09:00:00Z` |
| `Owner` | Lambda Chargeable Event (nếu chưa có) | IAM user → tên user; role → tên phiên; root → `root` | `user-b` |
| `CreatedBy` | Lambda Chargeable Event | Phần sau `:` cuối của ARN | `user/user-b` |

- Hết hạn = thời điểm bật + TTL. EC2 dùng `LaunchTime`; RDS/Aurora dùng `TTL-Start`.
- Hệ thống **không bao giờ ghi đè** `Owner` do người dùng tự gắn.
- `Owner`/`CreatedBy` chỉ gắn với sự kiện **tạo mới**, không gắn khi bật lại.

---

## 4. Định dạng thông báo

### 4.1. Tiêu đề (SNS Subject)

```
[Cost-Guardian][<LOẠI>] <nội dung>
```

- `<LOẠI>`: `ALERT` | `DAILY` | `TTL`.
- **Không dấu**: luôn đi qua hàm `to_ascii()`; cắt còn **tối đa 99 ký tự**.
- Mẫu cố định:

| Loại | Tiêu đề |
|---|---|
| ALERT | `[Cost-Guardian][ALERT] Tai nguyen tinh phi moi: <loại tài nguyên>` |
| DAILY | `[Cost-Guardian][DAILY] Bao cao tai nguyen ngay <dd/mm/yyyy> (<N> muc)` |
| TTL – đã dừng | `[Cost-Guardian][TTL] Da dung <N> tai nguyen het han` |
| TTL – lỗi | `[Cost-Guardian][TTL] Loi xu ly TTL (<N>)` |

### 4.2. Nội dung

- Văn bản thuần, **tiếng Việt có dấu**, mỗi trường một dòng, nhãn căn thẳng hàng bằng dấu cách.
- Giờ: `YYYY-MM-DD HH:MM:SS (UTC+7)`; ngày trong tiêu đề: `dd/mm/yyyy`.
- Tiền: `~<giờ> USD/giờ ≈ <tháng> USD/tháng`, tháng = giờ × 730. Không tra được giá → `không ước tính được`.
- Danh sách: mỗi mục bắt đầu bằng `  - `.

**ALERT** – đúng thứ tự các trường:

```
Loại cảnh báo  : Tài nguyên tính phí mới được tạo/bật
Tài nguyên     : <loại>  <ID, ID…>
Sự kiện        : <eventName>
Người thực hiện: <ARN>
Thời điểm      : <giờ UTC+7>
Region         : <region>
Tag đã gắn     : Owner=…, CreatedBy=… (tự động)      ← chỉ với sự kiện tạo mới
Ước tính       : ~… USD/giờ ≈ … USD/tháng (giá On-Demand)
Gợi ý          : Xóa hoặc dừng tài nguyên nếu không còn dùng; gắn tag TTL (vd: 4h) cho tài nguyên thử nghiệm.
```

**DAILY** – thứ tự các phần:
1. Dòng đầu `Báo cáo tài nguyên AWS ngày <dd/mm/yyyy> (quét <N> Region)`.
2. Bảng tổng hợp số lượng từng loại + dòng `Ước tính tài nguyên đang chạy`.
3. Chi tiết theo Region, khối `==== Region <tên> ====`; chỉ in loại có ít nhất 1 mục.
4. Mục `Có thể đang bị bỏ quên` trong từng Region.
5. `Lỗi khi quét` (nếu có).
6. Dòng `Gợi ý` cuối.
7. Không có tài nguyên nào → vẫn gửi, ghi `Không có tài nguyên nào đang phát sinh chi phí. Hệ thống vẫn hoạt động bình thường.`

**TTL** – chỉ gửi khi có tài nguyên bị dừng hoặc có lỗi; gom cả lần chạy vào **một** email.

---

## 5. Hợp đồng sự kiện đầu vào của Lambda

| Lambda | Nguồn | Payload |
|---|---|---|
| `cg-daily-scan` | Scheduler | `{"source": "cost-guardian.scheduler", "job": "daily-scan"}` |
| `cg-ttl` | Scheduler | `{"source": "cost-guardian.scheduler", "job": "ttl-check"}` |
| `cg-chargeable-event` | EventBridge rule | Sự kiện CloudTrail nguyên bản |

Tham số dùng khi **kiểm thử** (không bắt buộc):
- `"dry_run": true` – chỉ trả kết quả, không dừng, không gắn tag, không gửi email (Scan, TTL).
- `"now": "2026-10-31T10:00:00Z"` – giả định giờ hiện tại (TTL).

Mọi Lambda **in sự kiện đầu vào** ra log ở dòng đầu (`print("EVENT:", json.dumps(event))`) và **trả về** một dict tóm tắt kết quả.

---

## 6. Cấu hình Lambda

| Lambda | Runtime | Timeout | Bộ nhớ |
|---|---|---|---|
| `cg-chargeable-event` | Python 3.12 | 30 giây | 128 MB |
| `cg-daily-scan` | Python 3.12 | 5 phút | 256 MB |
| `cg-ttl` | Python 3.12 | 2 phút | 128 MB |

Timeout và bộ nhớ là **giá trị khởi điểm**: được tăng/giảm linh hoạt theo từng giai đoạn
(vd tăng timeout của `cg-daily-scan` khi bật quét mọi Region và thêm loại tài nguyên).
Ai đổi thì ghi lại trong mô tả PR. Runtime Python 3.12 là cố định.

Handler: `lambda_function.lambda_handler`.

**Biến môi trường** (chữ HOA, gạch dưới):

| Biến | Dùng ở | Mặc định |
|---|---|---|
| `TOPIC_ARN` | cả 3 | *(bắt buộc)* |
| `ALL_REGIONS` | Scan, TTL | `true` |
| `SNAPSHOT_AGE_DAYS` | Scan | `90` |
| `IDLE_CPU_THRESHOLD` | Scan | `5` |
| `IDLE_DAYS` | Scan | `7` |

**Không** hard-code ARN, số tài khoản hay email trong code.

---

## 7. Biến cấu hình (`tfvars.json`)

> ⏳ **Chưa chốt** các câu hỏi về Budget (4 dòng `budget_*` / `monthly_budget_usd` bên dưới là bản nháp). Các biến còn lại đã chốt.

CLI hỏi người dùng rồi sinh file này; Terraform đọc. Tên biến `snake_case`.

| Biến | Kiểu | Mặc định | Câu hỏi CLI |
|---|---|---|---|
| `alert_email` | string | – | Email nhận cảnh báo |
| `home_region` | string | `us-east-1` | Region đặt hệ thống |
| `monitor_all_regions` | bool | `true` | Giám sát tất cả Region đang bật? |
| `monthly_budget_usd` | number | `100` | Ngân sách hằng tháng (USD) |
| `budget_actual_thresholds` | list(number) | `[80, 100]` | Ngưỡng cảnh báo theo chi phí thực tế (%), tối đa 4 |
| `budget_forecast_alert` | bool | `true` | Cảnh báo khi chi phí **dự báo** vượt 100%? |
| `budget_exclude_credits` | bool | `true` | Bỏ qua credits khi tính chi phí? |
| `daily_report_time` | string `HH:MM` | `07:00` | Giờ gửi báo cáo hằng ngày |
| `enable_ttl` | bool | `true` | Bật tự động dừng theo tag TTL? |
| `ttl_check_interval_minutes` | number | `30` | *(không hỏi, chỉ sửa tay)* |
| `snapshot_age_days` | number | `90` | *(không hỏi)* |
| `idle_cpu_threshold` | number | `5` | *(không hỏi)* |
| `idle_days` | number | `7` | *(không hỏi)* |

AWS Budgets cho tối đa **5 thông báo** mỗi budget → tối đa 4 ngưỡng thực tế + 1 ngưỡng dự báo.

---

## 8. Cấu trúc repo

```
cost-guardian/
├── CONVENTIONS.md
├── README.md
├── requirements-dev.txt     # boto3, pytest – để chạy test trên máy
├── .github/pull_request_template.md
├── cli/
│   └── cost-guardian.py
├── lambdas/
│   ├── common/              # notify.py (to_ascii, send), pricing.py
│   ├── chargeable_event/    # lambda_function.py, iam_policy.json, event_pattern.json
│   ├── daily_scan/
│   └── ttl/
├── terraform/
│   ├── bootstrap/           # bucket state (chỉ nhóm dùng)
│   └── main/
│       ├── modules/
│       │   ├── notification/   # SNS + Budgets
│       │   ├── alert/          # CloudTrail, rule, Lambda, StackSet
│       │   ├── daily_scan/
│       │   └── ttl/
│       ├── main.tf  variables.tf  outputs.tf  backend.tf
├── test_events/
│   ├── chargeable_event/  scan/  ttl/  budget/
│   └── load_fixture.py
├── tests/                   # pytest, chạy offline bằng botocore Stubber
└── scripts/                 # công cụ kiểm tra trên AWS thật (vd check_pricing.py)
```

---

## 9. Quy ước viết code

**Python**
- Tên biến, hàm: tiếng Anh, `snake_case`. **Chú thích và nội dung email: tiếng Việt.**
- Dùng **paginator** cho mọi API có phân trang.
- Mỗi Region / mỗi loại tài nguyên bọc trong `try/except` riêng; lỗi ghi vào danh sách `errors`, không làm dừng cả lần chạy.
- Hàm dùng chung (`to_ascii`, gửi SNS, tra giá) để trong `lambdas/common/`, không chép mỗi nơi một bản.
- Mỗi tính năng mới phải kèm **sự kiện test hoặc fixture** trong `test_events/`.

**Terraform**
- Terraform **≥ 1.10**; khai báo phiên bản AWS provider trong `required_providers` và **commit** `.terraform.lock.hcl`.
- Tên resource Terraform `snake_case` (vd `aws_lambda_function.daily_scan`); tên trên AWS theo mục 2.
- Mọi biến có `description`; biến nhập từ người dùng có `validation`.
- Không dùng giá trị cứng cho Region, số tài khoản, email – lấy từ biến hoặc data source.
- Lambda Chargeable Event cần `aws_lambda_permission` cho EventBridge; zip code dùng `source_code_hash` để Terraform nhận ra khi code đổi.

---

## 10. Git

- Nhánh `main` được bảo vệ: chỉ gộp qua Pull Request, nhóm trưởng duyệt.
- Tên nhánh và nội dung commit: **mỗi người tự đặt**, không bắt buộc theo mẫu nào.
- **Không commit**: `*.tfstate*`, `.terraform/`, `tfvars.json`, `backend.hcl`, `*.zip`, `__pycache__/`, file chứa khóa truy cập (đã có sẵn trong `.gitignore`).
