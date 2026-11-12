data "aws_caller_identity" "current" {}

output "account_id" {
  description = "Tài khoản AWS đang triển khai"
  value       = data.aws_caller_identity.current.account_id
}
output "topic_arn" {
  description = "SNS topic nhận mọi thông báo"
  value       = module.notification.topic_arn
}