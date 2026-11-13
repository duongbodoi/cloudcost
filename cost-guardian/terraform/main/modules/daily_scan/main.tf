data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  function_name = "${var.name_prefix}-daily-scan"
  schedule_name = "${local.function_name}-schedule"
  partition     = data.aws_partition.current.partition
  account_id    = data.aws_caller_identity.current.account_id

  # "HH:MM" (giờ VN) -> cron(phút giờ * * ? *); parseint(..., 10) để "08", "09" không bị hiểu sai
  time_parts      = split(":", var.daily_report_time)
  report_hour     = parseint(local.time_parts[0], 10)
  report_minute   = parseint(local.time_parts[1], 10)
  cron_expression = "cron(${local.report_minute} ${local.report_hour} * * ? *)"
}

locals {
  policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadAWSResources"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeRegions",
          "ec2:DescribeNatGateways",
          "ec2:DescribeAddresses",
          "ec2:DescribeVolumes",
          "ec2:DescribeVpcEndpoints",
          "ec2:DescribeSnapshots",
          "ec2:DescribeImages",

          "rds:DescribeDBInstances",
          "rds:DescribeDBSnapshots",

          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",

          "cloudwatch:GetMetricData",

          "pricing:GetProducts",
        ]
        Resource = "*"
      },
      {
        Sid      = "PublishDailyReport"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.topic_arn
      }
    ]
  })
}

module "lambda" {
  source = "../lambda_function"

  function_name = local.function_name
  source_file   = "${var.lambdas_dir}/daily_scan/lambda_function.py"
  common_files = [
    "${var.lambdas_dir}/common/notify.py",
    "${var.lambdas_dir}/common/pricing.py",
  ]
  policy_json = local.policy_json

  timeout     = 300 # quét mọi Region + CloudWatch 7 ngày, 120s dễ thiếu
  memory_size = 512

  environment = {
    TOPIC_ARN           = var.topic_arn
    MONITOR_ALL_REGIONS = tostring(var.monitor_all_regions)
    SNAPSHOT_AGE_DAYS   = tostring(var.snapshot_age_days)
    IDLE_CPU_THRESHOLD  = tostring(var.idle_cpu_threshold)
    IDLE_DAYS           = tostring(var.idle_days)
  }
}

resource "aws_scheduler_schedule" "this" {
  name                         = local.schedule_name
  group_name                   = "default"
  description                  = "Báo cáo chi phí và tài nguyên hằng ngày"
  schedule_expression          = local.cron_expression
  schedule_expression_timezone = "Asia/Ho_Chi_Minh"
  state                        = var.enabled ? "ENABLED" : "DISABLED"
  action_after_completion      = "NONE"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.lambda.function_arn
    role_arn = var.scheduler_role_arn

    input = jsonencode({
      source  = "cost-guardian.scheduler"
      job     = "daily-scan"
      dry_run = false
    })

    retry_policy {
      maximum_event_age_in_seconds = 1800
      maximum_retry_attempts       = 0
    }
  }

  lifecycle {
    precondition {
      condition = (
        split(":", var.topic_arn)[1] == local.partition &&
        split(":", var.topic_arn)[3] == data.aws_region.current.region &&
        split(":", var.topic_arn)[4] == local.account_id
      )
      error_message = "topic_arn phải thuộc cùng partition, tài khoản và Region với Lambda daily scan."
    }
  }
}
