locals {
  lambdas_dir = "${path.root}/../../lambdas"
}

module "notification" {
  source                   = "./modules/notification"
  alert_email              = var.alert_email
  monthly_budget_usd       = var.monthly_budget_usd
  budget_actual_thresholds = var.budget_actual_thresholds
  budget_forecast_alert    = var.budget_forecast_alert
  budget_exclude_credits   = var.budget_exclude_credits
}

module "alert" {
  source      = "./modules/alert"
  name_prefix = var.name_prefix
  topic_arn   = module.notification.topic_arn
  lambdas_dir = local.lambdas_dir
}

module "event_forwarder" {
  source             = "./modules/event_forwarder"
  name_prefix        = var.name_prefix
  home_region        = var.home_region
  event_pattern_file = "${local.lambdas_dir}/chargeable_event/event_pattern.json"
  enabled            = var.monitor_all_regions
}

module "daily_scan" {
  source              = "./modules/daily_scan"
  name_prefix         = var.name_prefix
  topic_arn           = module.notification.topic_arn
  lambdas_dir         = local.lambdas_dir
  scheduler_role_arn  = aws_iam_role.scheduler.arn
  monitor_all_regions = var.monitor_all_regions
  daily_report_time   = var.daily_report_time
  snapshot_age_days   = var.snapshot_age_days
  idle_cpu_threshold  = var.idle_cpu_threshold
  idle_days           = var.idle_days
}

module "ttl" {
  source                 = "./modules/ttl"
  name_prefix            = var.name_prefix
  topic_arn              = module.notification.topic_arn
  lambdas_dir            = local.lambdas_dir
  scheduler_role_arn     = aws_iam_role.scheduler.arn
  monitor_all_regions    = var.monitor_all_regions
  enabled                = var.enable_ttl
  check_interval_minutes = var.ttl_check_interval_minutes
}