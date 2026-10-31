"""
Kiểm tra pricing.py trên AWS THẬT (hạng mục 18).

Chạy trên máy đã đăng nhập AWS CLI, user có quyền pricing:GetProducts:

    python scripts/check_pricing.py

Mỗi dòng in ra giá tra được. Dòng nào ra "KHÔNG TRA ĐƯỢC" là bộ lọc của loại đó cần sửa
trong lambdas/common/pricing.py (hàm _query). Đối chiếu với trang giá AWS để chắc giá đúng.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lambdas", "common"))

from notify import fmt_money  # noqa: E402
from pricing import estimate  # noqa: E402

CASES = [
    ("ec2", "us-east-1", {"instance_type": "t3.micro"}),
    ("ec2", "ap-southeast-1", {"instance_type": "t3.small"}),
    ("rds", "us-east-1", {"instance_class": "db.t3.micro", "engine": "mysql"}),
    ("rds", "us-east-1", {"instance_class": "db.t3.medium", "engine": "aurora-mysql"}),
    ("nat", "us-east-1", {}),
    ("nat", "ap-southeast-1", {}),
    ("eip", "us-east-1", {}),
    ("alb", "us-east-1", {}),
    ("nlb", "us-east-1", {}),
    ("eks", "us-east-1", {}),
    ("elasticache", "us-east-1", {"node_type": "cache.t3.micro", "engine": "redis"}),
    ("ebs", "us-east-1", {"size_gb": 30, "volume_type": "gp3"}),
    ("snapshot", "us-east-1", {"size_gb": 50}),
]

ok = 0
for rt, region, attrs in CASES:
    r = estimate(rt, region, **attrs)
    label = f"{rt:12s} {region:15s} {attrs}"
    if r:
        ok += 1
        print(f"OK   {label}\n       {r['unit_price']} USD/{r['unit']}  ->  {fmt_money(r['hourly'])}")
    else:
        print(f"FAIL {label}\n       KHÔNG TRA ĐƯỢC")
print(f"\n{ok}/{len(CASES)} loại tra được giá.")
