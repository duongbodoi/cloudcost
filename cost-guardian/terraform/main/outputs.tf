data "aws_caller_identity" "current" {}

output "account_id" {
  description = "Tài khoản AWS đang triển khai"
  value       = data.aws_caller_identity.current.account_id
}