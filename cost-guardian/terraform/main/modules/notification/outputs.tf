output "topic_arn" {
  description = "ARN của SNS topic Cost-Guardian"
  value       = aws_sns_topic.this.arn
}

output "topic_name" {
  value = aws_sns_topic.this.name
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}