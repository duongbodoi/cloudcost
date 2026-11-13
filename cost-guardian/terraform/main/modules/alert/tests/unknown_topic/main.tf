# Mô phỏng lần dựng mới: ARN SNS chưa biết tại thời điểm plan.
variable "name_prefix" { type = string }
variable "topic_arn" { type = string }
variable "lambdas_dir" { type = string }

resource "aws_sns_topic" "fixture" {
  name = "Cost-Guardian"
}

module "alert" {
  source      = "../.."
  name_prefix = var.name_prefix
  topic_arn   = aws_sns_topic.fixture.arn
  lambdas_dir = var.lambdas_dir
}
