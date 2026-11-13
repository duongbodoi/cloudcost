output "function_name" {
  description = "Tên Lambda daily scan"
  value       = module.lambda.function_name
}

output "function_arn" {
  description = "ARN Lambda daily scan do module Lambda dùng chung tạo"
  value       = module.lambda.function_arn
}

output "schedule_name" {
  description = "Tên lịch báo cáo hằng ngày trong group default"
  value       = aws_scheduler_schedule.this.name
}

output "schedule_arn" {
  description = "ARN lịch báo cáo hằng ngày"
  value       = aws_scheduler_schedule.this.arn
}

output "schedule_state" {
  description = "ENABLED hoặc DISABLED"
  value       = aws_scheduler_schedule.this.state
}
