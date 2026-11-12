# Hạng mục 27: chỉ quản lý luồng TTL, dùng role Scheduler chung do root truyền vào.
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  function_name = "${var.name_prefix}-ttl"
  schedule_name = "${var.name_prefix}-ttl-check-schedule"
  partition     = data.aws_partition.current.partition
  account_id    = data.aws_caller_identity.current.account_id

  # Tên Sid và điều kiện tương ứng với lambdas/ttl/iam_policy.json.
  policy_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadAllEnabledRegions"
        Effect   = "Allow"
        Action   = ["ec2:DescribeRegions", "ec2:DescribeInstances", "rds:DescribeDBInstances", "rds:DescribeDBClusters"]
        Resource = "*"
      },
      {
        Sid      = "StopTaggedEC2OutsideASG"
        Effect   = "Allow"
        Action   = "ec2:StopInstances"
        Resource = "arn:${local.partition}:ec2:*:${local.account_id}:instance/*"
        Condition = { Null = {
          "aws:ResourceTag/TTL"                       = "false"
          "ec2:ResourceTag/aws:autoscaling:groupName" = "true"
        } }
      },
      {
        Sid       = "StopTaggedRDS"
        Effect    = "Allow"
        Action    = "rds:StopDBInstance"
        Resource  = "arn:${local.partition}:rds:*:${local.account_id}:db:*"
        Condition = { Null = { "rds:db-tag/TTL" = "false" } }
      },
      {
        Sid       = "StopTaggedAurora"
        Effect    = "Allow"
        Action    = "rds:StopDBCluster"
        Resource  = "arn:${local.partition}:rds:*:${local.account_id}:cluster:*"
        Condition = { Null = { "rds:cluster-tag/TTL" = "false" } }
      },
      {
        Sid      = "MaintainOnlyTTLStart"
        Effect   = "Allow"
        Action   = ["rds:AddTagsToResource", "rds:RemoveTagsFromResource"]
        Resource = ["arn:${local.partition}:rds:*:${local.account_id}:db:*", "arn:${local.partition}:rds:*:${local.account_id}:cluster:*"]
        Condition = {
          Null                        = { "aws:ResourceTag/TTL" = "false", "aws:TagKeys" = "false" }
          "ForAllValues:StringEquals" = { "aws:TagKeys" = ["TTL-Start"] }
        }
      },
      {
        Sid      = "SendTTLNotifications"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = var.topic_arn
      }
    ]
  })
}

module "function" {
  source        = "../lambda_function"
  function_name = local.function_name
  source_file   = "${var.lambdas_dir}/ttl/lambda_function.py"
  common_files  = ["${var.lambdas_dir}/common/notify.py"]
  policy_json   = local.policy_json
  timeout       = 120
  memory_size   = 128
  environment = {
    TOPIC_ARN   = var.topic_arn
    ALL_REGIONS = tostring(var.monitor_all_regions)
  }
}

resource "aws_scheduler_schedule" "this" {
  name                         = local.schedule_name
  group_name                   = "default"
  description                  = "Kiểm tra TTL của EC2, RDS và Aurora"
  schedule_expression          = "rate(${var.check_interval_minutes} minutes)"
  schedule_expression_timezone = "UTC"
  state                        = var.enabled ? "ENABLED" : "DISABLED"
  action_after_completion      = "NONE"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.function.function_arn
    role_arn = var.scheduler_role_arn
    input = jsonencode({
      source = "cost-guardian.scheduler"
      job    = "ttl-check"
    })
    retry_policy {
      maximum_event_age_in_seconds = 1800
      maximum_retry_attempts       = 0
    }
  }

  # Đợi cả policy của Lambda được tạo, không chỉ đợi có ARN function.
  depends_on = [module.function]

  lifecycle {
    precondition {
      condition = (
        split(":", var.topic_arn)[1] == local.partition &&
        split(":", var.topic_arn)[3] == data.aws_region.current.region &&
        split(":", var.topic_arn)[4] == local.account_id
      )
      error_message = "topic_arn phải thuộc cùng partition, tài khoản và Region với Lambda TTL."
    }
  }
}
