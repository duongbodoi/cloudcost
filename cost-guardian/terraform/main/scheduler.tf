# Role cho EventBridge Scheduler gọi Lambda – dùng chung cho daily_scan và ttl
data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    actions = ["lambda:InvokeFunction"]
    resources = [
      "arn:aws:lambda:${var.home_region}:${data.aws_caller_identity.current.account_id}:function:${var.name_prefix}-*"
    ]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${var.name_prefix}-scheduler-policy"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

