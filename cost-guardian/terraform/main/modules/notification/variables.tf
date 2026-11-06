variable "alert_email" { type = string }
variable "topic_name" {
  type    = string
  default = "Cost-Guardian"
}
variable "budget_name" {
  type    = string
  default = "cost-guardian-monthly"
}
variable "monthly_budget_usd" { type = number }
variable "budget_actual_thresholds" { type = list(number) }
variable "budget_forecast_alert" { type = bool }
variable "budget_exclude_credits" { type = bool }