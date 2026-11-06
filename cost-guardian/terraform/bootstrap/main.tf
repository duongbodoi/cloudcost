# ---------------------------------------------------------------
# Bootstrap: tạo bucket S3 lưu Terraform state dùng chung của nhóm.
# Chạy MỘT lần. State của chính thư mục này lưu trên máy (local).
# ---------------------------------------------------------------

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "cost-guardian"
      ManagedBy = "terraform"
    }
  }
}

# Lấy số tài khoản đang đăng nhập, để không phải ghi cứng trong code
data "aws_caller_identity" "current" {}

locals {
  bucket_name = "cost-guardian-tfstate-${data.aws_caller_identity.current.account_id}"
}

# 1. Bucket
resource "aws_s3_bucket" "tfstate" {
  bucket = local.bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

# 2. Bật versioning: state bị ghi hỏng thì lấy lại được bản trước
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

# 3. Mã hóa mọi file trong bucket
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 4. Chặn mọi đường công khai bucket ra internet
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 5. Tự xóa các phiên bản cũ sau 90 ngày, để bucket không phình mãi
resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

output "bucket_name" {
  description = "Tên bucket – dùng cho backend.hcl của terraform/main"
  value       = aws_s3_bucket.tfstate.bucket
}