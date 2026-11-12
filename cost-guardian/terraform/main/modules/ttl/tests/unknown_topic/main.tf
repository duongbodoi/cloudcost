# Mô phỏng lần dựng mới: ARN SNS chưa biết tại thời điểm plan.
variable "name_prefix" { type = string }
variable "topic_arn" { type = string }
variable "lambdas_dir" { type = string }
variable "scheduler_role_arn" { type = string }
variable "monitor_all_regions" { type = bool }
variable "enabled" { type = bool }
variable "check_interval_minutes" { type = number }

resource "aws_sns_topic" "fixture" {
  name = "Cost-Guardian"
}

module "ttl" {
  source                 = "../.."
  name_prefix            = var.name_prefix
  topic_arn              = aws_sns_topic.fixture.arn
  lambdas_dir            = var.lambdas_dir
  scheduler_role_arn     = var.scheduler_role_arn
  monitor_all_regions    = var.monitor_all_regions
  enabled                = var.enabled
  check_interval_minutes = var.check_interval_minutes
}
