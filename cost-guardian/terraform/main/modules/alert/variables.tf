variable "name_prefix" {
  description = "Tiền tố tên tài nguyên, theo quy ước nhóm là cg"
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,39}$", var.name_prefix))
    error_message = "name_prefix gồm chữ thường, số hoặc dấu gạch ngang, dài từ 1 đến 40 ký tự."
  }
}

variable "topic_arn" {
  description = "ARN SNS topic từ module notification; cùng tài khoản và Region trung tâm"
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^arn:[^:]+:sns:[^:]+:[0-9]{12}:[A-Za-z0-9_-]+$", var.topic_arn))
    error_message = "topic_arn phải là ARN SNS topic hợp lệ."
  }
}

variable "lambdas_dir" {
  description = "Thư mục lambdas/ của repo, chứa chargeable_event/ và common/"
  type        = string
  nullable    = false
  validation {
    condition = (
      fileexists("${var.lambdas_dir}/chargeable_event/lambda_function.py") &&
      fileexists("${var.lambdas_dir}/chargeable_event/event_pattern.json") &&
      fileexists("${var.lambdas_dir}/common/notify.py") &&
      fileexists("${var.lambdas_dir}/common/pricing.py")
    )
    error_message = "lambdas_dir phải chứa chargeable_event/lambda_function.py, chargeable_event/event_pattern.json, common/notify.py và common/pricing.py."
  }
}
