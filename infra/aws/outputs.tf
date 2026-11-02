output "lake_uri" {
  description = "SEC_EDGAR_LAKE for the Oracle VM"
  value       = local.lake_root
}

output "snapshot_uri" {
  description = "SEC_EDGAR_SNAPSHOT_S3_URI for the Streamlit secrets"
  value       = "s3://${aws_s3_bucket.lake.bucket}/${local.snapshot_key}"
}

output "athena_workgroup" {
  value = aws_athena_workgroup.lake.name
}

output "pipeline_access_key_id" {
  value = aws_iam_access_key.pipeline.id
}

output "pipeline_secret_access_key" {
  value     = aws_iam_access_key.pipeline.secret
  sensitive = true
}

output "dashboard_access_key_id" {
  value = aws_iam_access_key.dashboard.id
}

output "dashboard_secret_access_key" {
  value     = aws_iam_access_key.dashboard.secret
  sensitive = true
}
