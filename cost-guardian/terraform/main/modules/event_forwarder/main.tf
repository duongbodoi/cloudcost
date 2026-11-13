# TODO (Nguyễn Tùng Dương – hạng mục 25): template rule chuyển tiếp + IAM role PutEvents, triển khai bằng StackSet tới mọi Region trừ home_region
data "aws_caller_identity" "current" {}

# Các Region đang bật trong tài khoản (bỏ những Region opt-in chưa bật)
data "aws_regions" "enabled" {
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required", "opted-in"]
  }
}

locals {
  account_id      = data.aws_caller_identity.current.account_id
  target_regions  = var.enabled ? setsubtract(data.aws_regions.enabled.names, [var.home_region]) : []
  home_bus_arn    = "arn:aws:events:${var.home_region}:${local.account_id}:event-bus/default"
  admin_role_name = "${var.name_prefix}-stackset-admin-role"
  exec_role_name  = "${var.name_prefix}-stackset-exec-role"
}

# ---------------------------------------------------------------------------
# 1. Role chuyển tiếp – tạo MỘT lần (IAM là toàn cục), mọi Region dùng chung
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "forwarder_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "forwarder" {
  name               = "${var.name_prefix}-event-forwarder-role"
  assume_role_policy = data.aws_iam_policy_document.forwarder_assume.json
}

resource "aws_iam_role_policy" "forwarder" {
  name = "${var.name_prefix}-event-forwarder-policy"
  role = aws_iam_role.forwarder.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "events:PutEvents", Resource = local.home_bus_arn }]
  })
}

# ---------------------------------------------------------------------------
# 2. Hai role StackSet cần (mô hình SELF_MANAGED trong cùng một tài khoản)
#    admin role: CloudFormation dùng để "điều phối"
#    exec role : được admin role nhập vai để tạo stack ở từng Region
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "admin_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["cloudformation.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "admin" {
  name               = local.admin_role_name
  assume_role_policy = data.aws_iam_policy_document.admin_assume.json
}

resource "aws_iam_role_policy" "admin" {
  name = "${local.admin_role_name}-policy"
  role = aws_iam_role.admin.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = "arn:aws:iam::${local.account_id}:role/${local.exec_role_name}"
    }]
  })
}

data "aws_iam_policy_document" "exec_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.admin.arn]
    }
  }
}

resource "aws_iam_role" "exec" {
  name               = local.exec_role_name
  assume_role_policy = data.aws_iam_policy_document.exec_assume.json
}

resource "aws_iam_role_policy" "exec" {
  name = "${local.exec_role_name}-policy"
  role = aws_iam_role.exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["cloudformation:*", "events:*"], Resource = "*" },
      { Effect = "Allow", Action = "iam:PassRole", Resource = aws_iam_role.forwarder.arn },
    ]
  })
}

# ---------------------------------------------------------------------------
# 3. StackSet: template chỉ chứa MỘT rule chuyển tiếp
# ---------------------------------------------------------------------------
resource "aws_cloudformation_stack_set" "this" {
  name                    = "${var.name_prefix}-event-forwarder"
  description             = "Chuyen su kien tao tai nguyen tinh phi ve ${var.home_region}"
  permission_model        = "SELF_MANAGED"
  administration_role_arn = aws_iam_role.admin.arn
  execution_role_name     = aws_iam_role.exec.name

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Resources = {
      ForwardRule = {
        Type = "AWS::Events::Rule"
        Properties = {
          Name         = "${var.name_prefix}-forward-chargeable-events"
          EventBusName = "default"
          State        = "ENABLED"
          EventPattern = jsondecode(file(var.event_pattern_file))
          Targets = [{
            Id      = "home-region-default-bus"
            Arn     = local.home_bus_arn
            RoleArn = aws_iam_role.forwarder.arn
          }]
        }
      }
    }
  })

  operation_preferences {
    region_concurrency_type = "PARALLEL"
    max_concurrent_count    = 10
    failure_tolerance_count = 2
  }

  depends_on = [aws_iam_role_policy.admin, aws_iam_role_policy.exec, aws_iam_role_policy.forwarder]
}

# Một "bản sao" của stack cho mỗi Region đích
resource "aws_cloudformation_stack_set_instance" "this" {
  for_each = toset(local.target_regions)

  stack_set_name            = aws_cloudformation_stack_set.this.name
  stack_set_instance_region = each.key
}