# CLI `cost-guardian.py`

Giai đoạn 3 – hạng mục 30 (cấu hình `config.py`, Ngô Xuân Hậu) và 31 (triển khai `deploy.py`, Nguyễn Tùng Dương).

## Cần có

- Python 3.10+ và `pip install -r cli/requirements.txt`
- Terraform ≥ 1.10
- Đã đăng nhập AWS (`aws configure` hoặc `aws sso login`)

## Lệnh

Chạy từ thư mục gốc dự án:

| Lệnh | Làm gì | Thay đổi AWS? |
|---|---|---|
| `python cli/cost-guardian.py check` | Kiểm tra Terraform, đăng nhập AWS | Không |
| `python cli/cost-guardian.py config` | Hỏi đáp, lưu `terraform/main/terraform.tfvars.json` | Không |
| `python cli/cost-guardian.py plan` | Xem trước sẽ tạo / sửa gì | Không |
| `python cli/cost-guardian.py deploy` | Hỏi đáp cấu hình → plan → xác nhận `yes` → apply | **Có** |
| `python cli/cost-guardian.py destroy` | plan -destroy → gõ lại số tài khoản → gỡ toàn bộ | **Có** |

Tùy chọn:

- `--profile <tên>` (đặt **trước** tên lệnh): dùng AWS profile cụ thể, vd `--profile cg-b deploy`
- `deploy --skip-config`: không hỏi đáp, dùng file cấu hình đang có
- `deploy --yes`: không hỏi xác nhận (cho script). `destroy` **không** có tùy chọn này.

## Cấu hình

- Câu hỏi **cơ bản**: email, ngân sách/tháng, giám sát mọi Region, bật TTL. Các câu còn lại nằm trong phần **nâng cao**, chỉ hỏi khi người dùng chọn `y`.
- Enter = giữ giá trị trong `[ngoặc]`. Giá trị mặc định lấy từ cấu hình cũ, chưa có thì lấy `default` trong `variables.tf`.
- Mỗi câu trả lời được kiểm tra theo đúng `validation` của Terraform, nhập sai thì hỏi lại.
- Thêm biến mới: thêm vào `variables.tf` **và** một `Question(...)` trong `config.py`.
  `tests/test_cli_config.py` báo lỗi nếu hai nơi lệch tên hoặc lệch mặc định.

## Nơi lưu state

- Có `terraform/main/backend.tf` (chỉ nhóm phát triển, chép từ `backend.tf.example`, không commit): state trên S3 của nhóm.
- Không có (người dùng cuối): state lưu trên máy ở `terraform/main/terraform.tfstate`. **Giữ file này** để còn `destroy` được.
