# Bộ dữ liệu test – Cloud Cost Guardian

Region: `us-east-1` · Số tài khoản trong file là giả (`123456789012`).
Mọi giờ trong file là **UTC**.

Có 3 loại file:

| Loại | Dùng ở đâu | Mục đích |
|---|---|---|
| **Event** (sự kiện đầu vào) | Lambda → tab **Test**; EventBridge → **Test pattern** | Giả lập thứ kích hoạt Lambda |
| **Fixture** (dữ liệu AWS giả) | Test offline bằng `botocore.Stubber` + `load_fixture.py` | Giả lập kết quả `Describe*` để kiểm tra logic mà không cần tạo tài nguyên thật |
| **Budget** | SNS console / topic policy | Cấu hình và giả lập cảnh báo ngân sách |

---

## 1. `chargeable_event/` – Luồng cảnh báo tài nguyên mới (Ngô Xuân Hậu)

Dùng cho **Lambda `cg-chargeable-event`** (tab Test) và **EventBridge rule** (Test pattern).

| File | Khớp event pattern? | Lambda phải gửi email | Kiểm tra điều gì |
|---|---|---|---|
| `01_run_instances.json` | ✅ | `...: EC2` – `i-0d41f3e7a68c2b915` | Trường hợp cơ bản |
| `02_start_instances.json` | ✅ | `...: EC2 (bat lai)` – `i-07c2e9a1f04b3d882` | Bật lại máy đã dừng |
| `03_create_nat_gateway.json` | ✅ | `...: NAT Gateway` – `nat-09f1ab82c9e4d1a7b` | ID nằm sâu trong `CreateNatGatewayResponse` |
| `04_allocate_address.json` | ✅ | `...: Elastic IP` – `eipalloc-0a3f5c7e9b1d24680` | ID nằm ở `allocationId` |
| `05_create_db_instance.json` | ✅ | `...: RDS` – `db-lab-mysql` | Sự kiện từ `aws.rds` |
| `06_start_db_instance.json` | ✅ | `...: RDS (bat lai)` – `db-lab-mysql` | Bật lại RDS |
| `07_run_instances_by_root.json` | ✅ | `...: EC2` – **2 ID**, người tạo `...:root` | Tạo nhiều máy một lúc; người tạo là root |
| `08_create_load_balancer.json` | ✅ | `...: Load Balancer (ALB)` – `lab-alb` | ELBv2, gắn tag bằng ARN |
| `09_create_eks_cluster.json` | ✅ | `...: EKS cluster` – `lab-eks`; `Owner=cg-tv3`, `CreatedBy=assumed-role/DevRole/cg-tv3` | Người tạo là **role** (Owner = tên phiên) |
| `10_create_eks_nodegroup.json` | ✅ | `...: EKS node group` – `lab-eks/lab-ng` | Giá = `t3.medium` × `desiredSize` 2 |
| `11_create_cache_cluster.json` | ✅ | `...: ElastiCache` – `lab-redis` | |
| `12_create_replication_group.json` | ✅ | `...: ElastiCache` – `lab-valkey` | Giá = node × 2 thành viên |
| `13_create_serverless_cache.json` | ✅ | `...: ElastiCache Serverless` – `lab-serverless` | Ước tính: *không ước tính được* |
| `14_run_instances_other_region.json` | ✅ | `...: EC2` – `i-0c5e7a9b1d3f02468`, `Region: ap-southeast-1` | Client gắn tag theo Region của sự kiện |
| `15_run_instances_owner_preset.json` | ✅ | `...: EC2` – chỉ gắn `CreatedBy`, **giữ nguyên `Owner=nhom-11`** | Không ghi đè Owner người dùng tự gắn (tag `Owner` của volume bị bỏ qua) |
| `neg_01_run_instances_failed.json` | ❌ | Không được gửi | Lệnh lỗi (có `errorCode`) → bị lọc bởi `"errorCode": [{"exists": false}]` |
| `neg_02_terminate_instances.json` | ❌ | Không được gửi | Xóa máy không cần cảnh báo |
| `neg_03_describe_instances.json` | ❌ | Không được gửi | Thao tác chỉ xem |
| `neg_04_run_instances_by_autoscaling.json` | ✅ | **Không** được gửi (Lambda trả `skipped`) | Máy do Auto Scaling tự tạo – lọc trong Lambda, không lọc ở pattern |
| `neg_05_ecs_create_cluster.json` | ❌ | Không được gửi | `CreateCluster` của **ECS** trùng tên API với EKS |

> Các file `08`–`15`, `neg_04`, `neg_05` được dựng theo cấu trúc tài liệu API của AWS (chưa lấy từ CloudTrail thật).
> Khi có sự kiện thật cùng loại, thay vào để kiểm tra lại.
>
> Test trên Lambda bằng các file này sẽ **gắn tag thất bại** (ID giả) – email vẫn được gửi, dòng `Tag đã gắn` ghi lỗi.
> Muốn xem nội dung email mà không gửi, thêm `"dry_run": true` vào cấp ngoài cùng của sự kiện.
>
> File `neg_01`–`neg_03`, `neg_05` dùng để thử **event pattern** trên EventBridge (phải báo *không khớp*).
> Nếu đem `neg_*` test thẳng vào Lambda thì Lambda vẫn gửi email – vì việc lọc là của EventBridge, không phải của Lambda.

---

## 2. `scan/` – Luồng quét hằng ngày (Nguyễn Xuân Hải)

**Event:** `event_daily_scan.json` – đây cũng là nội dung **Payload** cần điền khi tạo lịch EventBridge Scheduler:
```json
{"source": "cost-guardian.scheduler", "job": "daily-scan"}
```

**Fixtures** (`scan/fixtures/`) và kết quả báo cáo mong đợi:

| Fixture | Có trong fixture | Báo cáo phải liệt kê |
|---|---|---|
| `describe_instances.json` | 2 máy `running`, 1 máy `stopped` | **EC2 đang chạy (2):** `i-0d41f3e7a68c2b915` (t3.small, Owner=cg-tv2, không TTL), `i-07c2e9a1f04b3d882` (t3.micro, Owner=cg-tv3, TTL=4h) |
| `describe_nat_gateways.json` | 1 `available`, 1 `deleted` | **NAT Gateway (1):** `nat-09f1ab82c9e4d1a7b` – bỏ qua cái đã `deleted` |
| `describe_addresses.json` | 1 EIP chưa gắn, 1 EIP đã gắn | **Elastic IP chưa gắn (1):** `eipalloc-0a3f5c7e9b1d24680` (54.210.11.23) – EIP chưa gắn là EIP **không có** `AssociationId` |
| `describe_volumes.json` | 1 ổ `available`, 1 ổ `in-use` | **EBS không gắn máy (1):** `vol-0e91aa22bb33cc44d` (30 GiB) – chỉ lấy `State = available` |
| `describe_db_instances.json` | 1 `available`, 1 `stopped` | **RDS đang chạy (1):** `db-lab-mysql` (db.t3.micro, TTL=1d) |

---

## 3. `ttl/` – Luồng tự động dừng theo TTL (Trần Mạnh Chiến)

**Events:**
- `event_ttl_check.json` – payload cho Scheduler (chạy thật).
- `event_ttl_check_dry_run.json` – dùng khi test:
  ```json
  {"job": "ttl-check", "now": "2026-10-18T10:00:00Z", "dry_run": true}
  ```
  **Quy ước cho Lambda TTL:**
  - `now` (không bắt buộc): nếu có thì dùng làm "giờ hiện tại" thay cho đồng hồ thật → kết quả test luôn giống nhau.
  - `dry_run` (không bắt buộc): nếu `true` thì **chỉ in ra** sẽ dừng máy nào, **không gọi** `StopInstances`/`StopDBInstance`.

**Quy tắc TTL:** giá trị `<số><đơn vị>`, đơn vị `m` / `h` / `d` (chấp nhận cả chữ hoa).
Hết hạn = thời điểm bật (`LaunchTime` với EC2; tag `TTL-Start` với RDS) + TTL.

**Kết quả mong đợi với `now = 2026-10-18T10:00:00Z`** (`ttl/fixtures/`):

| Tài nguyên | Tag TTL | Bật lúc (UTC) | Hết hạn (UTC) | Kết quả |
|---|---|---|---|---|
| `i-ttl0000expired4h` | `4h` | 18/10 04:00 | 08:00 | **STOP** |
| `i-ttl0000valid4h00` | `4h` | 18/10 08:30 | 12:30 | Giữ nguyên |
| `i-ttl0000expired30` | `30m` | 18/10 09:20 | 09:50 | **STOP** |
| `i-ttl0000valid2d00` | `2d` | 17/10 10:00 | 19/10 10:00 | Giữ nguyên |
| `i-ttl0000uppercase` | `4H` | 18/10 03:00 | 07:00 | **STOP** (chữ hoa vẫn hợp lệ) |
| `i-ttl0000nottltag0` | – | 01/10 | – | Bỏ qua (không có tag TTL) |
| `i-ttl0000invalid00` | `4 gio` | 18/10 01:00 | – | **Email lỗi** (sai định dạng), không dừng |
| `i-ttl0000stopped00` | `1h` | 15/10 | – | Bỏ qua (máy đã dừng) |
| `i-ttl0000inasg0000` | `1h` | 18/10 01:00 | 02:00 | Bỏ qua, ghi vào `skipped` (thuộc Auto Scaling Group – stop sẽ bị ASG tạo máy thay thế) |
| `db-ttl-expired` | `2h`, `TTL-Start=07:00` | 18/10 07:00 | 09:00 | **STOP** + xóa tag `TTL-Start` |
| `db-ttl-first-seen` | `4h` (chưa có `TTL-Start`) | – | – | Gắn `TTL-Start = now`, giữ nguyên |
| `db-no-ttl` | – | – | – | Bỏ qua |
| `db-ttl-stopped` | `1h`, còn sót `TTL-Start` | – | – | **Xóa tag `TTL-Start`** (để lần bật sau đếm lại), không dừng |
| `db-aurora-writer` | `1h` | – | – | Bỏ qua, ghi vào `skipped` (instance thuộc Aurora cluster – được xử lý ở mức cluster) |
| `db-replica` | `1h` | – | – | Bỏ qua, ghi vào `skipped` (read replica không dừng được) |
| `db-ttl-backing-up` | `1h` | – | – | Bỏ qua lần này (đang `backing-up`), lần chạy sau xử lý |

→ Một lần chạy phải: dừng **3 EC2 + 1 RDS**, gửi **1 email [TTL] tổng hợp** (liệt kê các tài nguyên đã dừng) và **1 email lỗi** cho `i-ttl0000invalid00`.

---

## 4. `budget/` – Luồng cảnh báo ngân sách (Nguyễn Tùng Dương)

| File | Dùng làm gì |
|---|---|
| `sns_topic_policy_statement.json` | Đoạn cần **thêm vào** Access policy của topic `Cost-Guardian` (SNS → topic → **Edit → Access policy**, thêm vào mảng `"Statement"`), thay `<ACCOUNT_ID>`. Thiếu đoạn này, Budgets không gửi được vào topic. |
| `budget_alert_sample.txt` | Giả lập email của AWS Budgets: SNS → topic → **Publish message**, Subject `AWS Budgets: cost-guardian-monthly has exceeded your alert threshold`, dán nội dung file vào Message body. Dùng để demo khi chưa đợi được cảnh báo thật (Budgets chỉ cập nhật vài lần/ngày). |

---

## 5. Test offline (không cần AWS)

```python
import boto3
from botocore.stub import Stubber
from load_fixture import load_fixture

ec2 = boto3.client("ec2", region_name="us-east-1")
stub = Stubber(ec2)
stub.add_response("describe_instances", load_fixture("ttl/fixtures/describe_instances.json"))
with stub:
    resp = ec2.describe_instances()
```
Tất cả fixture đã được kiểm tra khớp đúng cấu trúc response của boto3.
