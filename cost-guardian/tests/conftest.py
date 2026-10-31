"""Cho phép test import trực tiếp các file trong lambdas/common (giống cách Lambda chạy)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "common"))
sys.path.insert(0, os.path.join(ROOT, "test_events"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")       # khóa giả: test không gọi AWS thật
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
