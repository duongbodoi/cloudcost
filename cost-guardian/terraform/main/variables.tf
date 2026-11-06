# Danh sách biến: CONVENTIONS.md mục 7.
# Phần budget_* và monthly_budget_usd là BẢN NHÁP (câu hỏi CLI chưa chốt).

variable "name_prefix" {
  description = "Tiền tố tên tài nguyên AWS (CONVENTIONS mục 2)"
  type        = string
  default     = "cg"
}

variable "home_region" {
  description = "Region trung tâm đặt hệ thống"
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.home_region))
    error_message = "home_region phải là mã Region hợp lệ, vd us-east-1."
  }
}

variable "alert_email" {
  description = "Email nhận mọi thông báo"
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email không đúng định dạng email."
  }
}

variable "monitor_all_regions" {
  description = "Giám sát mọi Region đang bật (true) hay chỉ Region trung tâm (false)"
  type        = bool
  default     = true
}

# ---------------- Ngân sách (bản nháp) ----------------
variable "monthly_budget_usd" {
  description = "Ngân sách hằng tháng (USD)"
  type        = number
  default     = 100

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd phải lớn hơn 0."
  }
}

variable "budget_actual_thresholds" {
  description = "Các ngưỡng cảnh báo theo chi phí thực tế (%), tối đa 4"
  type        = list(number)
  default     = [80, 100]

  validation {
    condition     = length(var.budget_actual_thresholds) >= 1 && length(var.budget_actual_thresholds) <= 4
    error_message = "Cần từ 1 đến 4 ngưỡng (AWS Budgets giới hạn 5 thông báo, 1 dành cho dự báo)."
  }

  validation {
    condition     = alltrue([for t in var.budget_actual_thresholds : t > 0 && t <= 1000])
    error_message = "Mỗi ngưỡng phải nằm trong khoảng (0, 1000] %."
  }
}

variable "budget_forecast_alert" {
  description = "Cảnh báo khi chi phí dự báo vượt 100%"
  type        = bool
  default     = true
}

variable "budget_exclude_credits" {
  description = "Bỏ qua credits khi tính chi phí"
  type        = bool
  default     = true
}

# ---------------- Báo cáo hằng ngày ----------------
variable "daily_report_time" {
  description = "Giờ gửi báo cáo hằng ngày theo giờ Việt Nam, dạng HH:MM"
  type        = string
  default     = "07:00"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]$", var.daily_report_time))
    error_message = "daily_report_time phải có dạng HH:MM, vd 07:00."
  }
}

variable "snapshot_age_days" {
  description = "Snapshot cũ hơn số ngày này được đưa vào báo cáo"
  type        = number
  default     = 90
}

variable "idle_cpu_threshold" {
  description = "Ngưỡng CPU (%) coi là không hoạt động"
  type        = number
  default     = 5

  validation {
    condition     = var.idle_cpu_threshold > 0 && var.idle_cpu_threshold < 100
    error_message = "idle_cpu_threshold phải trong khoảng (0, 100)."
  }
}

variable "idle_days" {
  description = "Số ngày liên tiếp CPU thấp để coi là không hoạt động"
  type        = number
  default     = 7
}

# ---------------- TTL ----------------
variable "enable_ttl" {
  description = "Bật tự động dừng tài nguyên theo tag TTL"
  type        = bool
  default     = true
}

variable "ttl_check_interval_minutes" {
  description = "Chu kỳ kiểm tra TTL (phút)"
  type        = number
  default     = 30

  validation {
    condition     = var.ttl_check_interval_minutes >= 5 && var.ttl_check_interval_minutes <= 1440
    error_message = "ttl_check_interval_minutes phải từ 5 đến 1440."
  }
}