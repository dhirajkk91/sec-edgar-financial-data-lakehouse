# The AWS side of the lakehouse: storage, catalog, SQL and guard rails.
#
#   S3       one private bucket: Bronze archive, Iceberg warehouse, dashboard
#            snapshot, run summaries and Athena results
#   Glue     the Iceberg catalog: databases sec_silver and sec_gold
#   Athena   a workgroup with a per-query scan cap, plus saved example queries
#   IAM      a writer for the Oracle VM and a one-object reader for the dashboard
#   Budgets  an email alarm on real spend, credits excluded
#
# Compute runs on the free Oracle VM, so nothing here bills by the hour. At the
# S&P 500 scale the lake is a few GB, which is cents a month in S3; Athena bills
# per TB scanned and the cap stops a runaway query.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project = "sec-edgar-lakehouse"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  prefix       = trim(var.prefix, "/")
  lake_root    = "s3://${aws_s3_bucket.lake.bucket}/${local.prefix}"
  warehouse    = "${local.lake_root}/warehouse"
  catalog_arn  = "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:catalog"
  databases    = ["sec_silver", "sec_gold"]
  snapshot_key = "${local.prefix}/serving/sec_edgar_gold.duckdb"
}

# ------------------------------------------------------------------ S3

resource "aws_s3_bucket" "lake" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Iceberg keeps its own history, so S3 versioning would only double the bill.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }
    expiration {
      days = 7
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# ------------------------------------------------------------------ Glue

resource "aws_glue_catalog_database" "lake" {
  for_each     = toset(local.databases)
  name         = each.value
  location_uri = "${local.warehouse}/${each.value}"
  description  = "Iceberg tables published by sec-edgar publish-lake"
}

# ------------------------------------------------------------------ Athena

resource "aws_athena_workgroup" "lake" {
  name          = "sec-edgar"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false
    bytes_scanned_cutoff_per_query     = var.athena_scan_limit_bytes

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

resource "aws_athena_named_query" "examples" {
  for_each  = fileset("${path.module}/queries", "*.sql")
  name      = trimsuffix(each.value, ".sql")
  workgroup = aws_athena_workgroup.lake.id
  database  = "sec_gold"
  query     = file("${path.module}/queries/${each.value}")
}

# ------------------------------------------------------------------ IAM

# The Oracle VM runs outside AWS, so it signs in with an access key, scoped to
# this bucket prefix and these two Glue databases.
resource "aws_iam_user" "pipeline" {
  name = "sec-edgar-pipeline"
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "ListLake"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    # The bucket holds only this project; PyArrow's S3 client may list without a prefix.
    resources = [aws_s3_bucket.lake.arn]
  }

  statement {
    sid = "ReadWriteLake"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${aws_s3_bucket.lake.arn}/${local.prefix}/*"]
  }

  statement {
    sid = "IcebergCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
    ]
    resources = concat(
      [local.catalog_arn],
      [for db in local.databases : "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:database/${db}"],
      [for db in local.databases : "arn:aws:glue:${var.region}:${data.aws_caller_identity.current.account_id}:table/${db}/*"],
    )
  }
}

resource "aws_iam_user_policy" "pipeline" {
  name   = "lake-writer"
  user   = aws_iam_user.pipeline.name
  policy = data.aws_iam_policy_document.pipeline.json
}

resource "aws_iam_access_key" "pipeline" {
  user = aws_iam_user.pipeline.name
}

# Streamlit Cloud reads exactly two objects: the snapshot and its manifest.
resource "aws_iam_user" "dashboard" {
  name = "sec-edgar-dashboard"
}

data "aws_iam_policy_document" "dashboard" {
  statement {
    sid     = "ReadSnapshot"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.lake.arn}/${local.snapshot_key}",
      "${aws_s3_bucket.lake.arn}/${trimsuffix(local.snapshot_key, ".duckdb")}.json",
    ]
  }
}

resource "aws_iam_user_policy" "dashboard" {
  name   = "snapshot-reader"
  user   = aws_iam_user.dashboard.name
  policy = data.aws_iam_policy_document.dashboard.json
}

resource "aws_iam_access_key" "dashboard" {
  user = aws_iam_user.dashboard.name
}

# ------------------------------------------------------------------ Budget

resource "aws_budgets_budget" "monthly" {
  name         = "sec-edgar-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Credits would hide real usage until they run out; alarm on what it costs.
  cost_types {
    include_credit = false
    include_refund = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
