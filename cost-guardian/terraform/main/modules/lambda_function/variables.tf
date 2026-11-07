variable "function_name" {
  description = "Tên Lambda, vd cg-daily-scan (role = <tên>-role, policy = <tên>-policy)"
  type        = string
}

variable "source_file" {
  description = "Đường dẫn file lambda_function.py của luồng"
  type        = string
}

variable "common_files" {
  description = "Các file dùng chung chép kèm vào zip, vd notify.py, pricing.py"
  type        = list(string)
  default     = []
}

variable "policy_json" {
  description = "IAM policy (JSON) riêng của luồng. null = chỉ có quyền ghi log"
  type        = string
  default     = null
}

variable "environment" {
  description = "Biến môi trường của Lambda"
  type        = map(string)
  default     = {}
}

variable "timeout" {
  description = "Timeout (giây)"
  type        = number
  default     = 30
}

variable "memory_size" {
  description = "Bộ nhớ (MB)"
  type        = number
  default     = 128
}

variable "runtime" {
  type    = string
  default = "python3.12"
}

variable "handler" {
  type    = string
  default = "lambda_function.lambda_handler"
}

variable "log_retention_days" {
  type    = number
  default = 14
}