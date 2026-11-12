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
    error_message = "topic_arn phải là ARN SNS topic hợp lệ; module notification cần trả ARN thực tế."
  }
}

variable "lambdas_dir" {
  description = "Đường dẫn thư mục lambdas, chứa ttl/lambda_function.py và common/notify.py"
  type        = string
  nullable    = false
  validation {
    condition = (
      fileexists("${var.lambdas_dir}/ttl/lambda_function.py") &&
      fileexists("${var.lambdas_dir}/common/notify.py")
    )
    error_message = "lambdas_dir phải chứa code TTL và common/notify.py để đóng gói."
  }
}

variable "scheduler_role_arn" {
  description = "ARN role cg-scheduler-role dùng chung do root quản lý; module TTL không tạo role này"
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^arn:[^:]+:iam::[0-9]{12}:role/.+$", var.scheduler_role_arn))
    error_message = "scheduler_role_arn phải là ARN IAM role hợp lệ."
  }
}

variable "monitor_all_regions" {
  description = "true: khám phá mọi Region đang bật; false: chỉ quét Region của Lambda"
  type        = bool
  nullable    = false
}

variable "enabled" {
  description = "Bật hoặc tắt lịch TTL; false giữ lịch ở DISABLED, không xóa lịch hay function"
  type        = bool
  nullable    = false
}

variable "check_interval_minutes" {
  description = "Chu kỳ quét TTL bằng phút, theo cấu hình nhóm mặc định là 30"
  type        = number
  nullable    = false
  validation {
    condition = (
      var.check_interval_minutes >= 5 && var.check_interval_minutes <= 1440 &&
      floor(var.check_interval_minutes) == var.check_interval_minutes
    )
    error_message = "check_interval_minutes phải là số nguyên từ 5 đến 1440."
  }
}
