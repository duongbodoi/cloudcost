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
  description = "Thư mục lambdas, chứa daily_scan/lambda_function.py, common/notify.py và common/pricing.py"
  type        = string
  nullable    = false
  validation {
    condition = (
      fileexists("${var.lambdas_dir}/daily_scan/lambda_function.py") &&
      fileexists("${var.lambdas_dir}/common/notify.py") &&
      fileexists("${var.lambdas_dir}/common/pricing.py")
    )
    error_message = "lambdas_dir phải chứa daily_scan/lambda_function.py, common/notify.py và common/pricing.py để đóng gói."
  }
}

variable "scheduler_role_arn" {
  description = "ARN role cg-scheduler-role dùng chung do root quản lý; module này không tạo role đó"
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^arn:[^:]+:iam::[0-9]{12}:role/.+$", var.scheduler_role_arn))
    error_message = "scheduler_role_arn phải là ARN IAM role hợp lệ."
  }
}

variable "monitor_all_regions" {
  description = "true: quét mọi Region đang bật; false: chỉ quét Region của Lambda"
  type        = bool
  nullable    = false
}

variable "daily_report_time" {
  description = "Giờ gửi báo cáo hằng ngày theo giờ Việt Nam, dạng HH:MM"
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]$", var.daily_report_time))
    error_message = "daily_report_time phải có dạng HH:MM, vd 07:00."
  }
}

variable "snapshot_age_days" {
  description = "Snapshot cũ hơn số ngày này được đưa vào báo cáo"
  type        = number
  nullable    = false
  validation {
    condition     = var.snapshot_age_days >= 1 && floor(var.snapshot_age_days) == var.snapshot_age_days
    error_message = "snapshot_age_days phải là số nguyên >= 1."
  }
}

variable "idle_cpu_threshold" {
  description = "Ngưỡng CPU (%) coi là không hoạt động"
  type        = number
  nullable    = false
  validation {
    condition     = var.idle_cpu_threshold > 0 && var.idle_cpu_threshold < 100
    error_message = "idle_cpu_threshold phải trong khoảng (0, 100)."
  }
}

variable "idle_days" {
  description = "Số ngày liên tiếp CPU thấp để coi là không hoạt động"
  type        = number
  nullable    = false
  validation {
    condition     = var.idle_days >= 1 && floor(var.idle_days) == var.idle_days
    error_message = "idle_days phải là số nguyên >= 1."
  }
}

variable "enabled" {
  description = "Bật hoặc tắt lịch báo cáo; false giữ lịch ở DISABLED, không xóa lịch hay function"
  type        = bool
  nullable    = false
  default     = true
}
