output "function_arn" {
  description = "ARN Lambda TTL do module Lambda dùng chung tạo"
  value       = module.function.function_arn
}
output "schedule_arn" {
  description = "ARN lịch kiểm tra TTL trong group default"
  value       = aws_scheduler_schedule.this.arn
}
