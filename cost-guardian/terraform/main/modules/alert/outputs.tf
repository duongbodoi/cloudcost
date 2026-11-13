output "function_arn" {
  description = "ARN Lambda cg-chargeable-event do module Lambda dùng chung tạo"
  value       = module.function.function_arn
}

output "rule_arn" {
  description = "ARN EventBridge rule cg-chargeable-events trên default bus của Region trung tâm"
  value       = aws_cloudwatch_event_rule.this.arn
}
