variable "name_prefix" { type = string }
variable "home_region" { type = string }
variable "event_pattern_file" {
  description = "File event pattern dùng chung với rule ở Region trung tâm"
  type        = string
}
variable "enabled" {
  description = "false = không triển khai sang các Region khác"
  type        = bool
}