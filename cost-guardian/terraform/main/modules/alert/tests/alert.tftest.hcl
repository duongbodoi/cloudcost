# Mock provider: không gọi AWS, không dùng credentials hay state S3 của nhóm.
# Chạy tại thư mục modules/alert:  terraform init -backend=false ; terraform test
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
  name_prefix = "cg"
  topic_arn   = "arn:aws:sns:us-east-1:123456789012:Cost-Guardian"
  lambdas_dir = "../../../../lambdas"
}

run "names_follow_conventions" {
  command = plan
  assert {
    condition     = module.function.function_name == "cg-chargeable-event"
    error_message = "Lambda phải mang tên cg-chargeable-event."
  }
  assert {
    condition     = aws_cloudwatch_event_rule.this.name == "cg-chargeable-events"
    error_message = "Rule phải mang tên cg-chargeable-events."
  }
  assert {
    condition     = aws_cloudtrail.this.name == "cg-management-trail"
    error_message = "Trail phải mang tên cg-management-trail."
  }
  assert {
    condition     = aws_s3_bucket.trail.bucket == "cg-cloudtrail-123456789012"
    error_message = "Bucket log phải là cg-cloudtrail-<account_id>."
  }
}

run "rule_reads_shared_pattern_file" {
  command = plan
  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.this.event_pattern) == jsondecode(file("${var.lambdas_dir}/chargeable_event/event_pattern.json"))
    error_message = "Rule phải dùng đúng lambdas/chargeable_event/event_pattern.json (file dùng chung với event_forwarder)."
  }
  assert {
    condition     = aws_cloudwatch_event_rule.this.event_bus_name == "default" && aws_cloudwatch_event_rule.this.state == "ENABLED"
    error_message = "Rule phải nằm trên default bus và ở trạng thái ENABLED."
  }
}

run "trail_multi_region_and_clean_destroy" {
  command = plan
  assert {
    condition     = aws_cloudtrail.this.is_multi_region_trail
    error_message = "Trail phải đa Region."
  }
  assert {
    condition     = aws_cloudtrail.this.event_selector[0].include_management_events && aws_cloudtrail.this.event_selector[0].read_write_type == "WriteOnly"
    error_message = "Trail phải ghi management event loại Write."
  }
  assert {
    condition     = aws_s3_bucket.trail.force_destroy
    error_message = "Bucket log cần force_destroy = true để terraform destroy gỡ sạch."
  }
  assert {
    condition     = local.trail_arn == "arn:aws:cloudtrail:us-east-1:123456789012:trail/cg-management-trail"
    error_message = "Bucket policy phải giới hạn theo đúng ARN của trail."
  }
}

run "eventbridge_can_invoke_lambda" {
  command = plan
  assert {
    condition     = aws_lambda_permission.eventbridge.principal == "events.amazonaws.com" && aws_lambda_permission.eventbridge.action == "lambda:InvokeFunction"
    error_message = "Phải có aws_lambda_permission cho events.amazonaws.com."
  }
}

run "iam_matches_policy_file_and_limits_tags" {
  command = plan
  assert {
    # iam_policy.json có thêm WriteOwnLogs; ở Terraform quyền log đến từ AWSLambdaBasicExecutionRole
    condition = (
      [for s in jsondecode(local.policy_json).Statement : s.Sid] ==
      [for s in jsondecode(file("${var.lambdas_dir}/chargeable_event/iam_policy.json")).Statement : s.Sid if s.Sid != "WriteOwnLogs"]
    )
    error_message = "Các câu lệnh IAM phải khớp lambdas/chargeable_event/iam_policy.json."
  }
  assert {
    condition     = length([for s in jsondecode(local.policy_json).Statement : s if startswith(s.Sid, "Tag")]) == 5
    error_message = "Phải có đủ 5 câu lệnh gắn tag (EC2, RDS, ELB, EKS, ElastiCache)."
  }
  assert {
    condition = alltrue([
      for s in jsondecode(local.policy_json).Statement :
      s.Condition["ForAllValues:StringEquals"]["aws:TagKeys"] == ["Owner", "CreatedBy"] && s.Condition.Null["aws:TagKeys"] == "false"
      if startswith(s.Sid, "Tag")
    ])
    error_message = "Mọi quyền gắn tag phải giới hạn đúng hai khóa Owner, CreatedBy."
  }
  assert {
    condition     = [for s in jsondecode(local.policy_json).Statement : s.Resource if s.Sid == "PublishAlertToTopic"][0] == var.topic_arn
    error_message = "SNS chỉ được publish đúng topic đầu vào."
  }
}

run "reject_wrong_topic_region" {
  command = plan
  variables {
    topic_arn = "arn:aws:sns:ap-southeast-1:123456789012:Cost-Guardian"
  }
  expect_failures = [aws_cloudwatch_event_rule.this]
}

run "new_topic_arn_unknown_until_apply" {
  command = plan
  module {
    source = "./tests/unknown_topic"
  }
}
