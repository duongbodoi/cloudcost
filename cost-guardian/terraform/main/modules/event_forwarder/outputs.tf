output "stack_set_name" {
  value = aws_cloudformation_stack_set.this.name
}

output "target_regions" {
  description = "Các Region đã đặt rule chuyển tiếp"
  value       = sort(tolist(local.target_regions))
}