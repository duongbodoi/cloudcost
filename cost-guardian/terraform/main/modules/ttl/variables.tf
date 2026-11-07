variable "name_prefix" { type = string }
variable "topic_arn" { type = string }
variable "lambdas_dir" { type = string }
variable "scheduler_role_arn" { type = string }
variable "monitor_all_regions" { type = bool }
variable "enabled" { type = bool }
variable "check_interval_minutes" { type = number }