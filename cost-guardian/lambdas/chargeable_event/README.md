# Lambda `cg-chargeable-event` – Cảnh báo tài nguyên mới

**Phụ trách:** Ngô Xuân Hậu  ·  **Hạng mục:** 5, 6, 7 (GĐ 1) – 15, 16, 17, 24 (GĐ 2)

## File trong thư mục này

| File | Nội dung |
|---|---|
| `lambda_function.py` | Code Lambda, hàm `lambda_handler(event, context)` |
| `event_pattern.json` | Event pattern của rule `cg-chargeable-events` |
| `iam_policy.json` | Quyền của role `cg-chargeable-event-role` (dùng `<ACCOUNT_ID>`) |

Khi deploy, `lambda_function.py` cần **`notify.py` và `pricing.py`** (lấy từ `lambdas/common/`) nằm ngang hàng.

## Hợp đồng

- **Đầu vào:** sự kiện CloudTrail nguyên bản do rule EventBridge chuyển tới (mục 5 CONVENTIONS).
  Khi test có thể thêm `"dry_run": true` ở cấp ngoài cùng: không gắn tag, không gửi email, trả nội dung email trong kết quả.
- **Biến môi trường:** `TOPIC_ARN`.
- **Dùng chung:** `notify.py` (định dạng + gửi email), `pricing.py` (ước tính chi phí).
- **Email:** loại `ALERT`, đúng thứ tự trường ở mục 4.2 CONVENTIONS.
  Tiêu đề `Tai nguyen tinh phi moi: <loại>`; sự kiện bật lại thêm `(bat lai)`.
- **Trả về:** dict tóm tắt (`status` = `sent` | `skipped` | `dry_run`, `ids`, `tags`, `tag_errors`, `hourly_usd`, `subject`…).
  Log có dòng đầu `EVENT:` và dòng cuối `RESULT:`.

## Phạm vi cảnh báo (báo cáo tiến độ 2, mục 2.5.3)

| Tài nguyên | eventSource / eventName | Gắn tag | Ước tính chi phí |
|---|---|---|---|
| EC2 | `ec2` `RunInstances` | ✅ (gom mọi ID vào một lần gắn) | loại máy × số máy |
| EC2 bật lại | `ec2` `StartInstances` | – | tra loại máy bằng `DescribeInstances` |
| NAT Gateway | `ec2` `CreateNatGateway` | ✅ | giá NAT theo giờ |
| Elastic IP | `ec2` `AllocateAddress` | ✅ | giá IP công khai theo giờ |
| RDS | `rds` `CreateDBInstance` | ✅ | loại máy + engine + Multi-AZ |
| RDS bật lại | `rds` `StartDBInstance` | – | tra bằng `DescribeDBInstances` nếu phản hồi thiếu loại máy |
| Load Balancer | `elasticloadbalancing` `CreateLoadBalancer` | ✅ (ELBv2 và Classic) | ALB, NLB; GWLB và Classic: *không ước tính được* |
| EKS | `eks` `CreateCluster`, `CreateNodegroup` | ✅ | cluster: phí control plane; node group: loại máy × `desiredSize` |
| ElastiCache | `elasticache` `CreateCacheCluster`, `CreateReplicationGroup`, `CreateServerlessCache` | ✅ | loại node × số node; Serverless: *không ước tính được* |

**Quy tắc đặc biệt**
- **Bỏ qua** sự kiện do Auto Scaling tự gọi (`userIdentity.invokedBy` hoặc `sourceIPAddress` = `autoscaling.amazonaws.com`).
  Nhờ vậy ASG / node group EKS mở rộng không sinh một email cho mỗi máy.
- **Tag `Owner`/`CreatedBy`** chỉ gắn với sự kiện tạo mới. Nếu người tạo đã khai báo `Owner` khi tạo (đọc từ `requestParameters`), **giữ nguyên**, chỉ gắn `CreatedBy`.
- **Đa Region:** client gắn tag / Describe được tạo theo `detail.awsRegion` của sự kiện.
- **Gắn tag thất bại** (tài nguyên đã bị xóa, ID giả khi test, thiếu quyền) không chặn email: dòng `Tag đã gắn` ghi `không gắn được (<mã lỗi>)`. Hàm không ném lỗi ra ngoài để Lambda không tự chạy lại và gửi email trùng.
- **Không tra được giá** → `không ước tính được` (quy tắc của `pricing.py`). Bộ lọc giá của EIP, LB, EKS, ElastiCache trong `pricing.py` đang chờ kiểm chứng (hạng mục 18).

## Cấu hình trên console (Giai đoạn 2)

| Mục | Giá trị |
|---|---|
| Lambda | `cg-chargeable-event`, Python 3.12, handler `lambda_function.lambda_handler` |
| Timeout / bộ nhớ | 30 giây / 128 MB |
| File code | `lambda_function.py`, `notify.py`, `pricing.py` (cùng thư mục gốc) |
| Biến môi trường | `TOPIC_ARN=arn:aws:sns:us-east-1:<ACCOUNT_ID>:Cost-Guardian` |
| Role | `cg-chargeable-event-role`, inline policy `cg-chargeable-event-policy` = `iam_policy.json` |
| EventBridge rule | `cg-chargeable-events`, **default** event bus, us-east-1, pattern = `event_pattern.json`, target = Lambda `cg-chargeable-event` (quyền gọi bằng resource-based policy cho `events.amazonaws.com`) |
| CloudTrail | Trail đa Region (*Multi-region trail: Yes*), ghi management events (Write hoặc All), log vào S3 |
| Khác | Sự kiện từ Region khác **chưa** tự về us-east-1 cho đến khi có StackSet chuyển tiếp (hạng mục 25) |

## Kiểm thử

**Offline:** `python -m pytest tests` (file `tests/test_chargeable_event.py`, giả lập AWS bằng `botocore.Stubber`).

**Sự kiện mẫu:** `test_events/chargeable_event/` – bảng kết quả mong đợi ở `test_events/README.md` mục 1.

### Bài kiểm tra Giai đoạn 2 – luồng ALERT

| # | Cách làm | Kết quả mong đợi |
|---|---|---|
| A6 | EventBridge → rule `cg-chargeable-events` → *Edit* → *Test pattern* với từng file `01`–`15` và `neg_01`–`neg_05` | `01`–`15` và `neg_04` **khớp**; `neg_01`, `neg_02`, `neg_03`, `neg_05` **không khớp** |
| A7 | Lambda → Test với `08`–`13`, **thêm `"dry_run": true`** ở dòng đầu | `status: dry_run`, không có email; `ids` đúng (`lab-alb`, `lab-eks`, `lab-eks/lab-ng`, `lab-redis`, `lab-valkey`, `lab-serverless`); `13` ghi *không ước tính được* |
| A8 | Lambda → Test với `neg_04_run_instances_by_autoscaling.json` | `status: skipped`, lý do *do Auto Scaling tự tạo*, không có email |
| A9 | Lambda → Test với `01_run_instances.json` (không dry_run, ID giả) | Có email; dòng `Tag đã gắn: không gắn được (InvalidInstanceID.NotFound)`; Lambda **không** báo lỗi |
| A10 | Tạo EC2 `t3.micro` thật ở us-east-1, tag `TTL=30m`, `Owner=<tên bạn>` | Email ALERT: `Tag đã gắn: CreatedBy=user/<IAM user> (tự động); giữ nguyên Owner=<tên bạn>…`; `Ước tính: ~0,010 USD/giờ ≈ 7,6 USD/tháng (giá On-Demand)`; tab *Tags* của máy có thêm `CreatedBy`, `Owner` không đổi |
| A11 | Tạo EC2 `t3.micro` chỉ gắn `TTL=30m` (**không** gắn Owner – ngoại lệ có chủ đích của bài này), xóa ngay sau khi có email | `Tag đã gắn: Owner=<IAM user>, CreatedBy=user/<IAM user> (tự động)`; tab *Tags* có đủ 2 tag |
| A12 | Stop rồi Start máy ở A10 | Email `...: EC2 (bat lai)`; **không** có dòng `Tag đã gắn`; có dòng `Ước tính` |
| A13 | Tạo EC2 `t3.micro` ở **ap-southeast-1** (tag `TTL=30m`, `Owner`). Sửa `14_run_instances_other_region.json`: thay `i-0c5e7a9b1d3f02468` bằng ID thật; Lambda → Test | Email ghi `Region: ap-southeast-1`; máy ở Singapore có tag `CreatedBy` |
| A14 | IAM → Policy Simulator: role `cg-chargeable-event-role`, `ec2:CreateTags` lên một instance, context key `aws:TagKeys` = `TTL` rồi = `Owner` | `TTL` → **denied**; `Owner` → **allowed** |
| A15 | CloudWatch Logs `/aws/lambda/cg-chargeable-event` của A10 | Dòng đầu `EVENT: {...}`, dòng cuối `RESULT: {...}` có `"status": "sent"`, `tag_errors: []` |

Dọn dẹp sau A10–A13: terminate mọi máy thử ở **cả hai Region**.
