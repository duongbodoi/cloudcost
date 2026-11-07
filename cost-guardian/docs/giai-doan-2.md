# Giai đoạn 2 – Phân công, cách làm và tiêu chí hoàn thành

**Mục tiêu:** hiện thực các cải tiến (tag Owner, ước tính chi phí, mở rộng loại tài nguyên, Aurora,
phát hiện EC2 không hoạt động, đa Region) và **chuyển toàn bộ hệ thống sang Terraform**.
**Mốc 2 đạt khi:** một lệnh `terraform apply` dựng đủ hệ thống, mọi bài kiểm tra ở mục 6 có kết quả ✅.

| Người | Hạng mục | Giờ |
|---|---|---|
| Nguyễn Tùng Dương | 12 ✅, 13 ✅, 14 module `notification`, 25 module `event_forwarder`, 28 ghép + chuyển hẳn sang Terraform, 29 kiểm thử đa Region | 31 |
| Ngô Xuân Hậu | 15 tag Owner, 16 thêm LB/EKS/ElastiCache, 17 đa Region + giá trong ALERT, 24 module `alert` | 23 |
| Nguyễn Xuân Hải | 18 kiểm chứng `pricing.py`, 19 giá trong DAILY, 20 LB + VPC Endpoint, 21 snapshot cũ, 26 module `daily_scan` | 19 |
| Trần Mạnh Chiến | 22 EC2 không hoạt động, 23 TTL Aurora, 27 module `ttl` | 15 |

---

## 1. Chuẩn bị (mọi thành viên, ~30 phút)

1. `git pull` nhánh `main`. Cài **Terraform ≥ 1.10**.
2. Đăng nhập AWS, rồi trong PowerShell: `$env:AWS_PROFILE = "cost-guardian"` (đặt lại mỗi lần mở terminal mới).
3. Vào `terraform/main/`, chép hai file mẫu (hai file này **không commit**):
   ```powershell
   Copy-Item backend.hcl.example backend.hcl
   Copy-Item terraform.tfvars.json.example terraform.tfvars.json   # sửa alert_email thành email của bạn
   ```
4. Khởi tạo và xem trạng thái:
   ```powershell
   terraform init -backend-config="backend.hcl"
   terraform plan
   ```
   Phải chạy được và ra `No changes` (hoặc chỉ có thay đổi của người khác đang làm dở).
5. Chạy test offline: `pip install -r requirements-dev.txt` rồi `python -m pytest tests` – phải `passed`.

**Đọc kỹ** `terraform/main/modules/<module của bạn>/variables.tf` và `outputs.tf`: đó là **hợp đồng**
– module của bạn nhận đúng những biến đó và phải trả về đúng những output đó.

---

## 2. Làm theo hai đợt

| Đợt | Làm gì | Kiểm thử bằng |
|---|---|---|
| **A – Tính năng** | Sửa `lambdas/<luồng>/lambda_function.py`: chuyển sang dùng `notify.py`, rồi thêm tính năng mới | `pytest` + fixture (chủ yếu); dán code vào Lambda trên console của Giai đoạn 1 để thử với AWS thật |
| **B – Module** | Điền `terraform/main/modules/<module>/main.tf` + output | `terraform plan` / `apply -target` (mục 4) |

Đợt A không phụ thuộc ai nên làm ngay. Đợt B làm khi code Lambda của luồng đã ổn.

---

## 3. Việc của từng người

### 3.1. Ngô Xuân Hậu – ALERT

**15 – Tự gắn tag `Owner`, `CreatedBy` (3 giờ)**
- Chỉ với sự kiện **tạo mới** (`RunInstances`, `Create*`, `AllocateAddress`), không với `Start*`.
- Giá trị theo CONVENTIONS mục 3: IAM user → tên user; role → tên phiên; root → `root`. `CreatedBy` = phần sau `:` cuối của ARN.
- **Không ghi đè** `Owner` nếu người dùng đã tự gắn lúc tạo (xem tag trong `requestParameters`).
- API gắn tag theo dịch vụ: `ec2:CreateTags` (EC2, NAT, EIP), `rds:AddTagsToResource`, `elasticloadbalancing:AddTags`,
  `eks:TagResource`, `elasticache:AddTagsToResource`.
- IAM: chỉ cho gắn đúng hai khóa – điều kiện `"ForAllValues:StringEquals": {"aws:TagKeys": ["Owner", "CreatedBy"]}`.
- Email thêm dòng `Tag đã gắn`.

**16 – Thêm Load Balancer, EKS, ElastiCache (5 giờ)**
- Event pattern (`lambdas/chargeable_event/event_pattern.json`) thêm nguồn `aws.elasticloadbalancing`, `aws.eks`,
  `aws.elasticache` và các lệnh `CreateLoadBalancer`, `CreateCluster`, `CreateNodegroup`, `CreateCacheCluster`,
  `CreateReplicationGroup`, `CreateServerlessCache` (Bảng 2.4 trong báo cáo).
- **Bỏ qua** sự kiện có `userIdentity.invokedBy = "autoscaling.amazonaws.com"`.
- Thêm sự kiện mẫu `test_events/chargeable_event/08_...` trở đi cho từng loại mới.
- ⚠ `event_pattern.json` được **cả module `alert` và module `event_forwarder` đọc** – giữ đúng tên và vị trí file.

**17 – Đa Region + giá trong email (5 giờ)**
- Client gắn tag và tra giá tạo theo **`detail["awsRegion"]`** của sự kiện, không theo Region của Lambda.
- Dùng `pricing.estimate(...)`; email thêm dòng `Ước tính` (`fmt_money`).

**24 – Module `alert` (10 giờ)**
- CloudTrail `cg-management-trail` đa Region + bucket `cg-cloudtrail-<account_id>` (kèm bucket policy cho CloudTrail, `force_destroy = true`).
- Rule `cg-chargeable-events`: `event_pattern = file("${var.lambdas_dir}/chargeable_event/event_pattern.json")`, target là Lambda.
- Lambda qua `module "function" { source = "../lambda_function" ... }`, `common_files` = `notify.py`, `pricing.py`.
- `aws_lambda_permission` cho `events.amazonaws.com` gọi Lambda.

### 3.2. Nguyễn Xuân Hải – DAILY

**18 – Kiểm chứng `pricing.py` (3 giờ):** chạy `python scripts/check_pricing.py`; sửa bộ lọc trong `_query` cho loại nào `FAIL`;
đối chiếu vài giá với trang giá AWS; thêm test cho chỗ đã sửa. Thay đổi trong `lambdas/common/` → **PR riêng**.

**19 – Giá trong DAILY (2 giờ):** mỗi tài nguyên đang chạy có `~... USD/tháng`; phần tổng hợp có dòng `Ước tính tài nguyên đang chạy`.

**20 – Load Balancer + VPC Endpoint (3 giờ)**
- `elbv2.describe_load_balancers` (trạng thái `active`) → `describe_target_groups` → `describe_target_health`; không có target nào → đánh dấu ⚠.
- `ec2.describe_vpc_endpoints`: chỉ loại **Interface**, trạng thái `available` (bỏ Gateway vì miễn phí).

**21 – Snapshot cũ (3 giờ)**
- EBS: `describe_snapshots(OwnerIds=["self"])`, cũ hơn `SNAPSHOT_AGE_DAYS`; **loại trừ** snapshot đang được AMI của tài khoản dùng (`describe_images(Owners=["self"])`).
- RDS: `describe_db_snapshots(SnapshotType="manual")` cũ hơn ngưỡng (bỏ snapshot tự động).

**26 – Module `daily_scan` (8 giờ)**
- Lambda `cg-daily-scan` (timeout 300, 256 MB), biến môi trường theo CONVENTIONS mục 6.
- `common_files`: `notify.py`, `pricing.py` **và `lambdas/daily_scan/idle.py`** (file của Chiến – xem 3.3).
- Policy chỉ đọc: thêm `elasticloadbalancing:Describe*`, `ec2:DescribeVpcEndpoints`, `ec2:DescribeSnapshots`,
  `ec2:DescribeImages`, `rds:DescribeDBSnapshots`, `cloudwatch:GetMetricData`, `pricing:GetProducts`.
- Lịch `cg-daily-scan-schedule`: đổi `daily_report_time` ("07:00") thành `cron(0 7 * * ? *)`, múi giờ `Asia/Ho_Chi_Minh`,
  flexible window OFF, role `var.scheduler_role_arn`, payload `{"source":"cost-guardian.scheduler","job":"daily-scan"}`.

### 3.3. Trần Mạnh Chiến – TTL và EC2 không hoạt động

**22 – Phát hiện EC2 không hoạt động (3 giờ)** – nằm trong email DAILY nhưng **viết thành file riêng** để không sửa đè code của Hải:
- File `lambdas/daily_scan/idle.py`, hàm `find_idle_instances(ec2, cloudwatch, now, cpu_threshold, days) -> list[dict]`
  (mỗi phần tử: `instance_id`, `instance_type`, `avg_cpu`, `tags`).
- Điều kiện: máy `running` đủ `days` ngày **và** CPU trung bình **mỗi ngày** trong `days` ngày đều dưới ngưỡng.
- `GetMetricData`, chu kỳ 86400 giây, gom tối đa 500 máy mỗi lần gọi.
- Hải gọi hàm này và in mục **"Có thể đang bị bỏ quên"**. Hai người thống nhất chữ ký hàm trước khi code.

**23 – TTL cho Aurora cluster (4 giờ)**
- `rds.describe_db_clusters`: engine bắt đầu bằng `aurora`, trạng thái `available`; **bỏ qua** `EngineMode = "serverless"` (v1).
- Tag `TTL-Start` gắn trên **cluster ARN**; hết hạn → `stop_db_cluster` + xóa `TTL-Start`; cluster đã dừng mà còn sót tag → xóa.
- IAM thêm `rds:DescribeDBClusters`, `rds:StopDBCluster`, tag trên `arn:aws:rds:*:<account>:cluster:*`.
- Fixture `test_events/ttl/fixtures/describe_db_clusters.json`.

**27 – Module `ttl` (8 giờ)**
- Lambda `cg-ttl` (timeout 120, 128 MB), `common_files` = `notify.py`.
- Policy đọc + dừng, `ec2:StopInstances` có điều kiện `"Null": {"aws:ResourceTag/TTL": "false"}`.
- Lịch `cg-ttl-check-schedule`: `rate(<check_interval_minutes> minutes)`; `var.enabled = false` → lịch `state = "DISABLED"`.

### 3.4. Nguyễn Tùng Dương – nhóm trưởng

- **14 – `notification`:** `import` topic `Cost-Guardian` và budget `cost-guardian-monthly` đang có; viết topic policy cho Budgets.
- **25 – `event_forwarder`:** StackSet đặt rule chuyển tiếp ở mọi Region trừ `home_region`, đọc chung `event_pattern.json`.
- **28 – Ghép + chuyển hẳn:** tắt rồi xóa tài nguyên tạo tay của Giai đoạn 1; `apply` toàn hệ thống; thử `destroy` → `apply`.
- **29 – Kiểm thử đa Region**; review và gộp PR.

---

## 4. Quy tắc làm việc với Terraform

1. **Chỉ sửa trong phạm vi của mình:** `lambdas/<luồng>/` và `terraform/main/modules/<module>/`.
   Cần đổi `main.tf`, `variables.tf`, `scheduler.tf` hay module `lambda_function` → nhắn nhóm trưởng.
2. **`terraform plan` chạy thoải mái** – không thay đổi gì trên AWS.
3. **Thử module của mình trên AWS:** chỉ dùng
   ```powershell
   terraform apply -target="module.<tên module của bạn>"
   ```
   và **báo vào nhóm trước khi chạy**. `-target` giới hạn Terraform chỉ đụng tới module đó.
   Không bao giờ chạy `apply` không có `-target` hay `destroy` – việc đó của nhóm trưởng (hạng mục 28).
4. **Trước lần `apply` đầu tiên của module:** tài nguyên tạo tay ở Giai đoạn 1 của luồng mình nếu **trùng tên**
   (vd Lambda `cg-ttl`) sẽ gây lỗi `already exists` → báo nhóm trưởng để xóa hoặc `import`. Nếu **khác tên**
   thì tắt rule/lịch cũ để không nhận email trùng.
5. **Không sửa trên console** tài nguyên do Terraform tạo – sửa code rồi `apply` lại.
6. Gặp `Error acquiring the state lock` → có người đang chạy, **chờ**. Không dùng `force-unlock` khi chưa hỏi nhóm.
7. PR phải kèm kết quả `terraform plan` (đoạn `Plan: ...`) và ảnh chụp email của bài kiểm tra.

---

## 5. Lưu ý chi phí

| Tài nguyên | Giá tham khảo | Cách thử |
|---|---|---|
| EKS | ~0,10 USD/giờ cho riêng cluster, chưa kể node | **Dùng sự kiện mẫu**, không tạo cluster thật |
| ElastiCache | theo node, vài cent/giờ | Sự kiện mẫu; nếu tạo thật: `cache.t3.micro`, xóa ngay |
| Load Balancer | ~0,02 USD/giờ + phí lưu lượng | Tạo ALB không target để thử ⚠, xóa trong vòng 1 giờ |
| Aurora | `db.t3.medium` ~0,08 USD/giờ + lưu trữ | Tạo, thử TTL ngắn (`30m`), xóa ngay sau khi kiểm tra |
| NAT Gateway | ~0,045 USD/giờ | **Không tạo**, dùng sự kiện mẫu |
| CloudWatch `GetMetricData` | ~0,01 USD / 1.000 metric | Không đáng kể |

Mọi tài nguyên thử vẫn phải gắn `TTL` + `Owner` như Giai đoạn 1.

---

## 6. Bài kiểm tra – tiêu chí hoàn thành Giai đoạn 2

### ALERT – Ngô Xuân Hậu

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| H1 | Test pattern với sự kiện mẫu mới (LB, EKS, ElastiCache) | Khớp; các sự kiện `neg_*` vẫn không khớp |
| H2 | Lambda Test với từng sự kiện mẫu mới | Email đúng loại tài nguyên và ID |
| H3 | Tạo EC2 thật (không tự gắn `Owner`) | Trên console máy có tag `Owner`, `CreatedBy`; email có dòng *Tag đã gắn* và *Ước tính* |
| H4 | Tạo EC2 có sẵn `Owner=abc` | Tag `Owner` vẫn là `abc` |
| H5 | Sự kiện mẫu có `invokedBy = autoscaling.amazonaws.com` | Không có email |
| H6 | Policy Simulator: role ALERT gắn tag khóa `Foo` | **denied** |

### DAILY – Nguyễn Xuân Hải

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| S1 | `python scripts/check_pricing.py` | Tất cả `OK`, hoặc loại nào chưa hỗ trợ được ghi rõ lý do trong PR |
| S2 | `dry_run` với vài EC2 đang chạy | Mỗi máy có `~... USD/tháng`, có dòng tổng |
| S3 | ALB không target | Có trong báo cáo, đánh dấu ⚠ |
| S4 | Fixture VPC Endpoint (1 Interface, 1 Gateway) | Chỉ Interface được liệt kê |
| S5 | Fixture snapshot (1 cũ, 1 mới, 1 cũ nhưng thuộc AMI, 1 RDS tự động) | Chỉ snapshot cũ không thuộc AMI và snapshot RDS thủ công cũ được liệt kê |
| S6 | Đổi `daily_report_time` thành `"07:30"`, `terraform plan` | Lịch đổi thành `cron(30 7 * * ? *)` |

### TTL và EC2 không hoạt động – Trần Mạnh Chiến

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| C1 | Fixture CPU: máy A 7 ngày đều < 5%; máy B mới chạy 3 ngày; máy C có 1 ngày 6% | Chỉ máy A nằm trong mục *Có thể đang bị bỏ quên* |
| C2 | Aurora cluster `TTL=30m` | Lần chạy đầu gắn `TTL-Start` lên cluster; hết hạn thì cluster bị dừng, tag bị xóa, có email |
| C3 | Fixture Aurora Serverless v1 | Bỏ qua, không lỗi |
| C4 | `enable_ttl = false`, `terraform plan` | Lịch chuyển sang `DISABLED`, không bị xóa |
| C5 | Sau khi `apply` module: chạy lại bài T2 của Giai đoạn 1 | EC2 `TTL=30m` vẫn bị dừng đúng hạn |

### Hệ thống – Nguyễn Tùng Dương

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| L1 | Sau khi `import` topic và budget, `terraform plan` | Không có thay đổi phá hủy nào với topic và budget |
| L2 | Console CloudFormation → StackSets → `cg-event-forwarder` | Có stack instance ở mọi Region (trừ us-east-1), trạng thái *Succeeded* |
| L3 | Tạo EC2 `TTL=30m` ở **ap-southeast-1** | Email ALERT (Region `ap-southeast-1`, có Owner và giá) → máy xuất hiện trong DAILY → bị TTL dừng |
| L4 | `terraform apply` (không `-target`) trên `main` | `No changes` – toàn bộ hệ thống nằm trong Terraform |
| L5 | Tag Editor, tìm tài nguyên **không** có tag `ManagedBy=terraform` thuộc hệ thống | Không còn tài nguyên tạo tay của Giai đoạn 1 |
| L6 | `terraform destroy` rồi `apply` (báo cả nhóm trước; email phải xác nhận lại) | Cả hai chạy sạch, nhận lại đủ 4 loại email |

**Tất cả H, S, C, L đạt → hoàn thành Giai đoạn 2 (Mốc 2)**, chuyển sang Giai đoạn 3 (CLI).