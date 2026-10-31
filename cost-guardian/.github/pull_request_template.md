## Thay đổi gì

<!-- Mô tả ngắn: luồng nào, hạng mục số mấy, làm gì -->

## Kiểm tra trước khi tạo PR

- [ ] Đúng `CONVENTIONS.md`: tên tài nguyên, tiêu đề/nội dung email, tag, biến môi trường
- [ ] Dùng `lambdas/common/notify.py` (và `pricing.py` nếu cần), không chép lại hàm dùng chung
- [ ] Có sự kiện mẫu / fixture mới trong `test_events/` cho phần vừa thêm
- [ ] `python -m pytest tests` chạy qua
- [ ] Đã chạy thử `dry_run` (Scan, TTL) hoặc Test trên console (ALERT)
- [ ] Đã cập nhật `iam_policy.json` nếu dùng API mới
- [ ] Không commit state, `tfvars.json`, `*.zip`, khóa truy cập
- [ ] Nếu đổi timeout / bộ nhớ Lambda: ghi lý do ở đây
