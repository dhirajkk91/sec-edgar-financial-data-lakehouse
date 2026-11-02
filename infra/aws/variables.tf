variable "region" {
  description = "AWS region for the bucket, Glue and Athena"
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name, e.g. sec-edgar-lake-<your-name>"
  type        = string
}

variable "prefix" {
  description = "Folder inside the bucket that holds the lake"
  type        = string
  default     = "sec-edgar"
}

variable "budget_email" {
  description = "Where the monthly budget alarm is sent"
  type        = string
}

variable "monthly_budget_usd" {
  description = "Monthly spend that triggers the alarm, excluding credits"
  type        = number
  default     = 5
}

variable "athena_scan_limit_bytes" {
  description = "Athena cancels any query that would scan more than this"
  type        = number
  default     = 1073741824 # 1 GiB, about half a cent per query
}
