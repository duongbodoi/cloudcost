# Giai đoạn 1 – Phân công, cách làm và tiêu chí hoàn thành

**Mục tiêu:** cả 4 luồng (ALERT, DAILY, TTL, BUDGET) chạy được trên AWS tại **us-east-1**, dựng bằng console,
**đúng `CONVENTIONS.md`**, và code đã nằm trên repo.
**Mốc 1 đạt khi:** toàn bộ bài kiểm tra ở mục 5 có kết quả ✅.

| Người | Luồng | Hạng mục | Trạng thái |
|---|---|---|---|
| Nguyễn Tùng Dương | Quy ước, tài khoản, SNS, Budget | 1, 2, 3, 4 | ✅ Đã xong (còn bài B1, B2) |
| Ngô Xuân Hậu | Cảnh báo tài nguyên mới (ALERT) | 5, 6, 7 | Đang làm |
| Nguyễn Xuân Hải | Quét hằng ngày (DAILY) | 8, 9 | Đang làm |
| Trần Mạnh Chiến | Tự động dừng (TTL) | 10, 11 | Đang làm |

Thông tin chung: tài khoản `242254325470`, Region `us-east-1`,
SNS topic `arn:aws:sns:us-east-1:242254325470:Cost-Guardian`.

---

## 1. Chuẩn bị (mọi thành viên, ~30 phút, làm trước)

1. Clone repo, đọc **`CONVENTIONS.md`** (ít nhất mục 1–6) và `README.md` trong thư mục luồng của mình.
2. Đăng nhập console bằng IAM user của mình (đã bật MFA). Góc trên bên phải luôn là **N. Virginia (us-east-1)**.
3. **Đăng ký email của mình vào topic** `Cost-Guardian`: SNS → Topics → Cost-Guardian → *Create subscription*,
   Protocol `Email`, nhập email của bạn, rồi bấm **Confirm subscription** trong thư AWS gửi tới.
   Nhờ vậy ai cũng nhận được email của luồng mình để kiểm tra.
4. Dọn tài nguyên cũ từ Progress 1 của luồng mình nếu nó **nằm ở Region khác** (vd Singapore): xóa Lambda,
   rule, lịch, topic cũ ở đó để không nhận email trùng và không tốn phí.

---

## 2. Việc của từng người

### 2.1. Ngô Xuân Hậu – ALERT

**Hạng mục 5 – CloudTrail (4 giờ)**
- Trail tên `cg-management-trail` (nếu đã có trail từ Progress 1 thì giữ, không cần đổi tên).
- Bật **Multi-region trail** (*Apply trail to all regions*). Management events: ghi lại **Write** (hoặc All).
- S3 bucket log: `cg-cloudtrail-242254325470`.

**Hạng mục 6 – EventBridge rule (6 giờ)**
- Rule `cg-chargeable-events` trên **default event bus**, loại *Rule with an event pattern*.
- Event pattern bắt **đủ 6 lệnh**: `RunInstances`, `StartInstances`, `CreateNatGateway`, `AllocateAddress`,
  `CreateDBInstance`, `StartDBInstance`; nguồn `aws.ec2`, `aws.rds`;
  `detail-type` = `AWS API Call via CloudTrail`; và **chỉ lấy lệnh thành công** (`"errorCode": [{"exists": false}]`).
- Target: Lambda `cg-chargeable-event`.

**Hạng mục 7 – Lambda (12 giờ)**
- Lambda `cg-chargeable-event`, Python 3.12, timeout 30 giây, 128 MB, biến môi trường `TOPIC_ARN`.
- Role `cg-chargeable-event-role`: chỉ `sns:Publish` lên topic + quyền ghi log mặc định.
- Email **ALERT** đúng thứ tự trường ở mục 4.2 CONVENTIONS (GĐ 1 chưa có dòng *Tag đã gắn* và *Ước tính*).
  Tiêu đề: `[Cost-Guardian][ALERT] Tai nguyen tinh phi moi: <loại>` – **không dấu**.
- Bắt đủ: người thực hiện (ARN), thời điểm (UTC+7), Region, **tất cả ID** khi tạo nhiều máy một lúc.

### 2.2. Nguyễn Xuân Hải – DAILY

**Hạng mục 8 – Lambda Scan (16 giờ)**
- Lambda `cg-daily-scan`, Python 3.12, timeout **5 phút**, **256 MB** (mặc định 3 giây sẽ bị timeout).
- Biến môi trường: `TOPIC_ARN`, `ALL_REGIONS=true`.
- Role `cg-daily-scan-role` **chỉ đọc**: `ec2:DescribeInstances`, `ec2:DescribeNatGateways`,
  `ec2:DescribeAddresses`, `ec2:DescribeVolumes`, `ec2:DescribeRegions`, `rds:DescribeDBInstances`
  (Resource `*`) + `sns:Publish` lên topic.
- Quét 5 loại ở **mọi Region**: EC2 `running`, RDS `available`, NAT Gateway `available`,
  Elastic IP không có `AssociationId`, EBS `available`. Dùng **paginator**.
- Email **DAILY** theo cấu trúc mục 4.2 CONVENTIONS; **luôn gửi**, kể cả khi trống.
- Mỗi Region / mỗi loại bọc `try/except` riêng; lỗi in vào mục *Lỗi khi quét*.
- Hỗ trợ `{"dry_run": true}`: trả báo cáo trong kết quả, không gửi email.

**Hạng mục 9 – Lịch chạy (3 giờ)**
- Schedule `cg-daily-scan-schedule`: `cron(0 7 * * ? *)`, múi giờ **Asia/Ho_Chi_Minh**, Flexible time window **Off**,
  payload `{"source": "cost-guardian.scheduler", "job": "daily-scan"}`.
- **Tạo role dùng chung `cg-scheduler-role`** cho cả 2 lịch: quyền `lambda:InvokeFunction` lên
  `arn:aws:lambda:us-east-1:242254325470:function:cg-daily-scan` **và** `...:function:cg-ttl`.
  Báo Chiến khi tạo xong.

### 2.3. Trần Mạnh Chiến – TTL

**Hạng mục 10 – Lambda TTL (18 giờ)**
- Lambda `cg-ttl`, Python 3.12, timeout 2 phút, 128 MB, biến `TOPIC_ARN`, `ALL_REGIONS=true`.
- Quy tắc ở mục 3 CONVENTIONS:
  - Tag `TTL` dạng `30m` / `4h` / `2d`, không phân biệt chữ hoa thường; sai định dạng → **email lỗi**, không dừng.
  - **EC2:** hết hạn = `LaunchTime` + TTL.
  - **RDS:** lần đầu thấy đang chạy → gắn `TTL-Start`; dừng xong → xóa `TTL-Start`;
    RDS đã dừng mà còn sót `TTL-Start` → xóa.
  - **Bỏ qua:** máy có tag `aws:autoscaling:groupName`, read replica, instance thuộc Aurora,
    RDS đang bận (`backing-up`, `modifying`…).
- Chỉ gửi email khi **có tài nguyên bị dừng** hoặc **có lỗi**; gom vào một email mỗi lần chạy.
- Hỗ trợ `"dry_run": true` (chỉ liệt kê *sẽ dừng*) và `"now": "..."` (giả định giờ hiện tại).

**Hạng mục 11 – Quyền và lịch (6 giờ)**
- Role `cg-ttl-role`:
  - `ec2:DescribeInstances`, `ec2:DescribeRegions`, `rds:DescribeDBInstances` – Resource `*`.
  - `ec2:StopInstances` – **chỉ máy có tag TTL**: điều kiện `"Null": {"aws:ResourceTag/TTL": "false"}`.
  - `rds:StopDBInstance`, `rds:AddTagsToResource`, `rds:RemoveTagsFromResource` – trên `arn:aws:rds:*:242254325470:db:*`.
  - `sns:Publish` lên topic.
- Schedule `cg-ttl-check-schedule`: `rate(30 minutes)`, payload
  `{"source": "cost-guardian.scheduler", "job": "ttl-check"}`, role `cg-scheduler-role` (do Hải tạo).

### 2.4. Nguyễn Tùng Dương – nhóm trưởng

- Làm bài **B1, B2** (mục 5), xóa budget test 0.01 USD sau khi nhận email.
- Kiểm tra budget an toàn `cost-guardian-lab-safety` (10 USD) đã có.
- Review và gộp 3 Pull Request; chạy **bài kiểm tra tổng E1** để chốt Mốc 1.

---

## 3. Làm việc với repo trong Giai đoạn 1

Giai đoạn 1 làm **chính trên console**. Repo dùng để: đọc quy ước, lấy dữ liệu thử trong `test_events/`,
và **lưu kết quả cuối** khi luồng đã chạy ổn.

**Khi luồng chạy ổn → tạo một Pull Request gồm:**

| File | Nội dung |
|---|---|
| `lambdas/<luồng>/lambda_function.py` | Chép từ console |
| `lambdas/<luồng>/iam_policy.json` | JSON policy của role. **Thay số tài khoản bằng `<ACCOUNT_ID>`** |
| `lambdas/chargeable_event/event_pattern.json` | Chỉ luồng ALERT |
| `lambdas/<luồng>/README.md` | **Thêm mục "Cấu hình trên console"** (mẫu bên dưới) |
| `test_events/<luồng>/...` | Sự kiện mẫu / fixture mới nếu có |

Mẫu mục cấu hình cần ghi vào README (Giai đoạn 2 Terraform cần đúng các giá trị này):

```markdown
## Cấu hình trên console (Giai đoạn 1)
| Mục | Giá trị |
|---|---|
| Runtime / timeout / bộ nhớ | Python 3.12 / 5 phút / 256 MB |
| Biến môi trường | TOPIC_ARN=..., ALL_REGIONS=true |
| Lịch chạy | cron(0 7 * * ? *), Asia/Ho_Chi_Minh, flexible window Off |
| Payload | {"source": "cost-guardian.scheduler", "job": "daily-scan"} |
| Khác | (vd trail đa Region, rule trên default bus...) |
```

**Trong mô tả PR:** đánh dấu checklist có sẵn và **dán ảnh chụp các email** của bài kiểm tra luồng mình
(mục 5). Ảnh này là bằng chứng Mốc 1 và dùng luôn cho báo cáo.

- Tên nhánh, nội dung commit: tự đặt. Không đẩy thẳng lên `main`.
- **Không commit:** khóa truy cập, file zip, `tfvars.json`, state.
- `notify.py` **không bắt buộc** ở Giai đoạn 1. Code một file vẫn được; đầu Giai đoạn 2 mới chuyển sang dùng `notify.py`.

---

## 4. Lưu ý khi làm

**Chi phí và an toàn tài khoản chung**
- Mọi tài nguyên thử **phải** gắn `TTL` (vd `1h`) và `Owner=<tên bạn>`. Dùng loại nhỏ nhất: `t3.micro`,
  `db.t3.micro`, ổ `gp3` 1 GiB. Thử xong **xóa ngay**.
- **Không tạo NAT Gateway thật để thử** (~0,045 USD/giờ, quên là mất tiền) – dùng sự kiện mẫu `03_create_nat_gateway.json`.
- Không đụng vào tài nguyên có `Owner` là người khác.
- Xóa RDS thử nghiệm: bỏ chọn *final snapshot*, để không sinh snapshot tốn phí.
- **Không bao giờ bấm *unsubscribe*** ở cuối email SNS – bấm là cả nhóm mất thông báo.

**Lỗi hay gặp**

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| Không thấy tài nguyên vừa tạo | Đang xem nhầm Region – kiểm tra góc trên bên phải |
| Lambda trả `Hello from Lambda!` | Sửa code nhưng **chưa bấm Deploy** |
| `Task timed out after 3.00 seconds` | Chưa tăng timeout mặc định |
| `AuthorizationError` khi publish SNS | Sai ARN topic trong policy, gắn nhầm role, hoặc policy chưa lưu |
| Email ALERT đến chậm | CloudTrail cần vài phút (có khi tới ~15 phút) mới đẩy sự kiện – bình thường |
| Tiêu đề email lỗi / bị cắt | Tiêu đề có dấu hoặc dài hơn 99 ký tự – phải đi qua `to_ascii` |
| Lịch chạy nhưng Lambda không chạy | Role của Scheduler thiếu `lambda:InvokeFunction` cho đúng ARN |

**Khi thử lịch chạy** có thể tạm đặt lịch dày hơn (vd 5 phút) cho nhanh, **nhớ trả về** lịch chuẩn
(7h00 / 30 phút) và ghi đúng giá trị chuẩn vào README.

**Gặp lỗi** → gửi vào nhóm: ảnh chụp lỗi + đoạn log trong **CloudWatch Logs** (`/aws/lambda/<tên Lambda>`).

---

## 5. Bài kiểm tra – tiêu chí hoàn thành Giai đoạn 1

Mỗi bài phải đạt **đúng kết quả mong đợi**. Người phụ trách dán ảnh email làm bằng chứng vào PR.

### ALERT – Ngô Xuân Hậu

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| A1 | EventBridge → rule → *Test pattern* với `test_events/chargeable_event/01`–`07` và `neg_01`–`neg_03` | `01–07` **khớp**; `neg_01–03` **không khớp** |
| A2 | Lambda → Test với `03_create_nat_gateway.json` và `07_run_instances_by_root.json` | 2 email; `03` ghi `nat-09f1ab82c9e4d1a7b`; `07` ghi **đủ 2 ID**, người thực hiện là root |
| A3 | Tạo EC2 `t3.micro` thật ở us-east-1 (tag `TTL=30m`, `Owner`) | Email ALERT trong ~15 phút, đúng ID, đúng IAM user, Region `us-east-1`, tiêu đề không dấu |
| A4 | Stop rồi Start lại máy ở A3 | Email `...: EC2 (bat lai)` |
| A5 | Kiểm tra trail | *Multi-region trail: Yes* |

### DAILY – Nguyễn Xuân Hải

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| D1 | Lambda → Test `{"dry_run": true}` | Trả về báo cáo, **không** có email; số lượng khớp với những gì đang có trên console |
| D2 | Tạo 1 ổ EBS `gp3` 1 GiB **không gắn máy** ở `ap-southeast-1` và 1 Elastic IP **không gắn** ở `us-east-1`; chạy Test thật | Email DAILY liệt kê **ổ EBS dưới Region ap-southeast-1**, **EIP dưới us-east-1**; tiêu đề `[Cost-Guardian][DAILY] Bao cao tai nguyen ngay dd/mm/yyyy (N muc)` |
| D3 | Xóa hết tài nguyên ở D2, chạy lại | Vẫn có email, ghi *Không có tài nguyên nào đang phát sinh chi phí…* (nếu tài khoản không còn gì khác chạy) |
| D4 | Tạm gỡ quyền `rds:DescribeDBInstances` khỏi role, chạy Test; xong **gắn lại** | Email vẫn được gửi, có mục *Lỗi khi quét* ghi lỗi RDS; các loại khác vẫn đầy đủ |
| D5 | Tạm đặt lịch chạy sau vài phút | Email đến đúng giờ đã đặt; sau đó trả về `cron(0 7 * * ? *)` |

### TTL – Trần Mạnh Chiến

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| T1 | EC2 `t3.micro` tag `TTL=1m`, chờ 2 phút, Test `{"job": "ttl-check", "dry_run": true}` | Máy nằm trong `would_stop`; **máy vẫn chạy**; không có email |
| T2 | EC2 tag `TTL=30m`, để lịch tự chạy | Máy **bị dừng trong vòng ~60 phút** (30 phút TTL + tối đa 30 phút chờ lịch); email `[Cost-Guardian][TTL] Da dung 1 tai nguyen het han` |
| T3 | EC2 tag `TTL=4 gio` (sai định dạng) | Email `[Cost-Guardian][TTL] Loi xu ly TTL (1)`; **máy không bị dừng** |
| T4 | EC2 **không** có tag TTL | Không bị dừng, không có email về máy này |
| T5 | Start lại máy đã bị dừng ở T2 | **Không** bị dừng ngay ở lần chạy kế tiếp; đếm lại 30 phút từ lúc bật |
| T6 | RDS `db.t3.micro` tag `TTL=1h` | Lần chạy đầu: RDS được gắn `TTL-Start`. Sau khi hết hạn: RDS bị dừng, tag `TTL-Start` **bị xóa**, có email |
| T7 | IAM → Policy Simulator: role `cg-ttl-role`, `ec2:StopInstances` lên một máy **không có** tag TTL | Kết quả **denied** |

### BUDGET – Nguyễn Tùng Dương

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| B1 | Budget test 0.01 USD, ngưỡng 100% Actual → SNS | Email `AWS Budgets: cg-test-budget has exceeded your alert threshold`; xong thì xóa budget test |
| B2 | Kiểm tra `cost-guardian-monthly` | Có 3 ngưỡng (80% Actual, 100% Actual, 100% Forecasted) gửi vào topic; đã bỏ Credits |

### Tổng kết – nhóm trưởng chạy sau khi 3 PR đã gộp

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| E1 | Tạo 1 EC2 `t3.micro` tag `TTL=30m` ở us-east-1; bấm Test Lambda Scan; chờ | Trong khoảng 1 giờ nhận đủ **ALERT → DAILY (có máy này) → TTL (đã dừng)**, cùng email BUDGET từ B1 = **4 loại email** |
| E2 | Repo | 3 PR đã gộp; mỗi thư mục Lambda có code, `iam_policy.json` (dùng `<ACCOUNT_ID>`), mục *Cấu hình trên console* |
| E3 | Dọn dẹp | Không còn tài nguyên thử nghiệm nào đang chạy (Resource Groups → **Tag Editor**, tìm theo tag `Owner` ở mọi Region); không còn hệ thống cũ ở Region khác |

**Tất cả A, D, T, B, E đạt → hoàn thành Giai đoạn 1 (Mốc 1)**, chuyển sang Giai đoạn 2.
