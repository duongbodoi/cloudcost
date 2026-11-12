# Mock provider bảo đảm không gọi AWS hoặc dùng state S3 của nhóm.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  name_prefix            = "cg"
  topic_arn              = "arn:aws:sns:us-east-1:123456789012:Cost-Guardian"
  lambdas_dir            = "../../../../lambdas"
  scheduler_role_arn     = "arn:aws:iam::123456789012:role/cg-scheduler-role"
  monitor_all_regions    = true
  enabled                = true
  check_interval_minutes = 30
}

run "names_and_payload" {
  command = plan
  assert {
    condition     = module.function.function_name == "cg-ttl"
    error_message = "Lambda phải mang tên cg-ttl."
  }
  assert {
    condition     = aws_scheduler_schedule.this.name == "cg-ttl-check-schedule"
    error_message = "Lịch phải theo đúng CONVENTIONS."
  }
  assert {
    condition     = aws_scheduler_schedule.this.target[0].role_arn == var.scheduler_role_arn
    error_message = "Phải dùng role Scheduler chung được truyền vào."
  }
  assert {
    condition     = jsondecode(aws_scheduler_schedule.this.target[0].input) == { source = "cost-guardian.scheduler", job = "ttl-check" }
    error_message = "Payload phải đúng hợp đồng Scheduler TTL."
  }
}

run "c4_disable_without_deleting_schedule" {
  command = plan
  variables {
    enabled = false
  }
  assert {
    condition     = aws_scheduler_schedule.this.state == "DISABLED"
    error_message = "enabled=false phải giữ resource và đổi lịch thành DISABLED."
  }
  assert {
    condition     = module.function.function_name == "cg-ttl"
    error_message = "Tắt lịch không được xóa Lambda."
  }
}

run "iam_tag_guards" {
  command = plan
  assert {
    condition     = jsondecode(local.policy_json).Statement[1].Condition.Null["aws:ResourceTag/TTL"] == "false"
    error_message = "StopInstances phải yêu cầu tag TTL."
  }
  assert {
    condition     = jsondecode(local.policy_json).Statement[1].Condition.Null["ec2:ResourceTag/aws:autoscaling:groupName"] == "true"
    error_message = "Không được cho phép dừng EC2 thuộc ASG."
  }
  assert {
    condition     = jsondecode(local.policy_json).Statement[3].Condition.Null["rds:cluster-tag/TTL"] == "false"
    error_message = "Aurora phải được giới hạn theo tag TTL."
  }
  assert {
    condition     = jsondecode(local.policy_json).Statement[4].Condition["ForAllValues:StringEquals"]["aws:TagKeys"] == ["TTL-Start"]
    error_message = "Chỉ được sửa tag TTL-Start."
  }
  assert {
    condition     = jsondecode(local.policy_json).Statement[5].Resource == var.topic_arn
    error_message = "SNS chỉ được publish đúng topic đầu vào."
  }
}

run "configurable_interval" {
  command = plan
  variables {
    check_interval_minutes = 5
    monitor_all_regions    = false
  }
  assert {
    condition     = aws_scheduler_schedule.this.schedule_expression == "rate(5 minutes)"
    error_message = "Lịch phải dùng chu kỳ được truyền vào."
  }
}

run "reject_fractional_interval" {
  command = plan
  variables {
    check_interval_minutes = 5.5
  }
  expect_failures = [var.check_interval_minutes]
}

run "reject_wrong_topic_region" {
  command = plan
  variables {
    topic_arn = "arn:aws:sns:ap-southeast-1:123456789012:Cost-Guardian"
  }
  expect_failures = [aws_scheduler_schedule.this]
}

run "new_topic_arn_unknown_until_apply" {
  command = plan
  module {
    source = "./tests/unknown_topic"
  }
}
