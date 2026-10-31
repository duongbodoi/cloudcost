"""
Tiện ích đọc file fixture JSON thành response giống hệt boto3.

boto3 thật trả các trường thời gian (LaunchTime, CreateTime, InstanceCreateTime…)
dưới dạng datetime; JSON chỉ lưu được chuỗi, nên hàm này đổi lại thành datetime.

Cách dùng với botocore Stubber (test Lambda Scan/TTL không cần gọi AWS thật):

    import boto3
    from botocore.stub import Stubber
    from load_fixture import load_fixture

    ec2 = boto3.client("ec2", region_name="us-east-1")
    stub = Stubber(ec2)
    stub.add_response("describe_instances", load_fixture("ttl/fixtures/describe_instances.json"))
    with stub:
        resp = ec2.describe_instances()   # trả về đúng dữ liệu trong fixture
"""
import json
from datetime import datetime

TIME_KEYS = {"LaunchTime", "CreateTime", "InstanceCreateTime", "AttachTime"}


def _convert(obj):
    if isinstance(obj, dict):
        return {k: (datetime.fromisoformat(v.replace("Z", "+00:00"))
                    if k in TIME_KEYS and isinstance(v, str) else _convert(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert(x) for x in obj]
    return obj


def load_fixture(path):
    with open(path, encoding="utf-8") as f:
        return _convert(json.load(f))
