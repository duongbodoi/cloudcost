variable "name_prefix" { type = string }
variable "topic_arn" { type = string }
variable "lambdas_dir" {
  description = "Thư mục lambdas/ của repo"
  type        = string
}
