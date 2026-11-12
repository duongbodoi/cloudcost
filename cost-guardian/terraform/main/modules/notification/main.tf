# TODO (Nguyễn Tùng Dương – hạng mục 14): SNS topic, subscription email, topic policy cho Budgets, budget hằng tháng
data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}

# 1. Topic nhận mọi thông báo
resource "aws_sns_topic" "this" {
  name = var.topic_name
}

# 2. Đăng ký email – sẽ ở trạng thái "PendingConfirmation" cho tới khi người nhận bấm xác nhận
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.this.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# 3. Access policy của topic: chủ tài khoản + dịch vụ Budgets
data "aws_iam_policy_document" "topic" {
  statement {
    sid = "AllowAccountOwner"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
    actions = [
      "SNS:Publish",
      "SNS:Subscribe",
      "SNS:GetTopicAttributes",
      "SNS:SetTopicAttributes",
      "SNS:ListSubscriptionsByTopic",
    ]
    resources = [aws_sns_topic.this.arn]
  }

  statement {
    sid = "AllowAWSBudgetsPublish"
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.this.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:budgets::${local.account_id}:*"]
    }
  }
}

resource "aws_sns_topic_policy" "this" {
  arn    = aws_sns_topic.this.arn
  policy = data.aws_iam_policy_document.topic.json
}

# 4. Budget hằng tháng
resource "aws_budgets_budget" "monthly" {
  name         = var.budget_name
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = !var.budget_exclude_credits
    include_refund = !var.budget_exclude_credits
  }

  # Mỗi ngưỡng thực tế -> một thông báo
  dynamic "notification" {
    for_each = var.budget_actual_thresholds
    content {
      notification_type         = "ACTUAL"
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value
      threshold_type            = "PERCENTAGE"
      subscriber_sns_topic_arns = [aws_sns_topic.this.arn]
    }
  }

  # Ngưỡng dự báo 100% – chỉ khi bật
  dynamic "notification" {
    for_each = var.budget_forecast_alert ? [100] : []
    content {
      notification_type         = "FORECASTED"
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value
      threshold_type            = "PERCENTAGE"
      subscriber_sns_topic_arns = [aws_sns_topic.this.arn]
    }
  }

  # Budgets phải được phép gửi vào topic TRƯỚC khi gắn thông báo
  depends_on = [aws_sns_topic_policy.this]
}