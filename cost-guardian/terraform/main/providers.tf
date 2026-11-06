provider "aws" {
  region = var.home_region

  default_tags {
    tags = {
      Project   = "cost-guardian"
      ManagedBy = "terraform"
    }
  }
}