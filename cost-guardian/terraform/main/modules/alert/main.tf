# Hạng mục 24 (Ngô Xuân Hậu): luồng cảnh báo tài nguyên mới (ALERT)
#   CloudTrail đa Region -> S3 log
#   EventBridge rule (default bus, Region trung tâm) -> Lambda cg-chargeable-event -> SNS
# Rule ở các Region khác (chuyển tiếp về đây) thuộc module event_forwarder (hạng mục 25).
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  partition  = data.aws_partition.current.partition
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # Tên theo CONVENTIONS mục 2
  function_name = "${var.name_prefix}-chargeable-event"
  rule_name     = "${var.name_prefix}-chargeable-events"
  trail_name    = "${var.name_prefix}-management-trail"
  bucket_name   = "${var.name_prefix}-cloudtrail-${local.account_id}"

  # ARN dựng sẵn để bucket policy không phải chờ trail (tránh vòng phụ thuộc)
  trail_arn = "arn:${local.partition}:cloudtrail:${local.region}:${local.account_id}:trail/${local.trail_name}"

  # Log CloudTrail chỉ để truy vết; EventBridge nhận sự kiện trực tiếp, không đọc từ S3
  trail_log_expiration_days = 90

  # Điều kiện dùng chung: Lambda chỉ được gắn đúng hai khóa Owner, CreatedBy
  only_owner_tags = {
    "ForAllValues:StringEquals" = { "aws:TagKeys" = ["Owner", "CreatedBy"] }
    Null                        = { "aws:TagKeys" = "false" }
  }

  # Tương ứng lambdas/chargeable_event/iam_policy.json.
  # Quyền ghi log (WriteOwnLogs) đã có qua AWSLambdaBasicExecutionRole trong module lambda_function.
  policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PublishAlertToTopic"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.topic_arn
      },
      {
        Sid    = "TagEc2OwnerCreatedByOnly"
        Effect = "Allow"
        Action = "ec2:CreateTags"
        Resource = [
          "arn:${local.partition}:ec2:*:${local.account_id}:instance/*",
          "arn:${local.partition}:ec2:*:${local.account_id}:natgateway/*",
          "arn:${local.partition}:ec2:*:${local.account_id}:elastic-ip/*",
        ]
        Condition = local.only_owner_tags
      },
      {
        Sid       = "TagRdsOwnerCreatedByOnly"
        Effect    = "Allow"
        Action    = "rds:AddTagsToResource"
        Resource  = "arn:${local.partition}:rds:*:${local.account_id}:db:*"
        Condition = local.only_owner_tags
      },
      {
        Sid       = "TagLoadBalancerOwnerCreatedByOnly"
        Effect    = "Allow"
        Action    = "elasticloadbalancing:AddTags"
        Resource  = "arn:${local.partition}:elasticloadbalancing:*:${local.account_id}:loadbalancer/*"
        Condition = local.only_owner_tags
      },
      {
        Sid    = "TagEksOwnerCreatedByOnly"
        Effect = "Allow"
        Action = "eks:TagResource"
        Resource = [
          "arn:${local.partition}:eks:*:${local.account_id}:cluster/*",
          "arn:${local.partition}:eks:*:${local.account_id}:nodegroup/*",
        ]
        Condition = local.only_owner_tags
      },
      {
        Sid    = "TagElastiCacheOwnerCreatedByOnly"
        Effect = "Allow"
        Action = "elasticache:AddTagsToResource"
        Resource = [
          "arn:${local.partition}:elasticache:*:${local.account_id}:cluster:*",
          "arn:${local.partition}:elasticache:*:${local.account_id}:replicationgroup:*",
          "arn:${local.partition}:elasticache:*:${local.account_id}:serverlesscache:*",
        ]
        Condition = local.only_owner_tags
      },
      {
        Sid      = "LookupPrice"
        Effect   = "Allow"
        Action   = "pricing:GetProducts"
        Resource = "*"
      },
      {
        Sid      = "ReadTypeForRestartEvents"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "rds:DescribeDBInstances"]
        Resource = "*"
      },
    ]
  })
}

# ---------------------------------------------------------------- 1. Lambda
module "function" {
  source        = "../lambda_function"
  function_name = local.function_name
  source_file   = "${var.lambdas_dir}/chargeable_event/lambda_function.py"
  common_files = [
    "${var.lambdas_dir}/common/notify.py",
    "${var.lambdas_dir}/common/pricing.py",
  ]
  policy_json = local.policy_json
  timeout     = 30 # CONVENTIONS mục 6; gắn tag + tra giá thường dưới 5 giây
  memory_size = 128
  environment = {
    TOPIC_ARN = var.topic_arn
  }
}

# ---------------------------------------------------------------- 2. S3 lưu log CloudTrail
resource "aws_s3_bucket" "trail" {
  bucket        = local.bucket_name
  force_destroy = true # để terraform destroy gỡ sạch dù bucket còn file log
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket                  = aws_s3_bucket.trail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id
  rule {
    id     = "expire-cloudtrail-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = local.trail_log_expiration_days
    }
  }
}

data "aws_iam_policy_document" "trail_bucket" {
  statement {
    sid       = "AWSCloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.trail.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }

  statement {
    sid       = "AWSCloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.trail.arn}/AWSLogs/${local.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = data.aws_iam_policy_document.trail_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.trail]
}

# ---------------------------------------------------------------- 3. CloudTrail đa Region
resource "aws_cloudtrail" "this" {
  name                          = local.trail_name
  s3_bucket_name                = aws_s3_bucket.trail.id
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true

  # Chỉ cần lệnh ghi (tạo/bật tài nguyên); bỏ KMS và RDS Data API cho bớt log
  event_selector {
    read_write_type                  = "WriteOnly"
    include_management_events        = true
    exclude_management_event_sources = ["kms.amazonaws.com", "rdsdata.amazonaws.com"]
  }

  # CloudTrail kiểm tra quyền ghi bucket ngay khi tạo trail
  depends_on = [aws_s3_bucket_policy.trail]
}

# ---------------------------------------------------------------- 4. EventBridge rule -> Lambda
resource "aws_cloudwatch_event_rule" "this" {
  name           = local.rule_name
  description    = "Lệnh tạo/bật tài nguyên tính phí thành công (CloudTrail) -> Lambda ${local.function_name}"
  event_bus_name = "default"
  # File dùng chung với module event_forwarder: giữ đúng tên và vị trí
  event_pattern = file("${var.lambdas_dir}/chargeable_event/event_pattern.json")
  state         = "ENABLED"

  lifecycle {
    precondition {
      condition = (
        split(":", var.topic_arn)[1] == local.partition &&
        split(":", var.topic_arn)[3] == local.region &&
        split(":", var.topic_arn)[4] == local.account_id
      )
      error_message = "topic_arn phải thuộc cùng partition, tài khoản và Region với Lambda ALERT."
    }
  }
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.function.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.this.arn
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule           = aws_cloudwatch_event_rule.this.name
  event_bus_name = "default"
  target_id      = local.function_name
  arn            = module.function.function_arn

  # Rule chỉ bắt đầu gọi Lambda khi quyền gọi đã có
  depends_on = [aws_lambda_permission.eventbridge]
}
