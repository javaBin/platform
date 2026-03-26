output "glue_database_name" {
  description = "Glue catalog database name for CUR data"
  value       = aws_glue_catalog_database.cur.name
}

output "glue_table_name" {
  description = "Glue catalog table name (discovered by crawler)"
  value       = "${var.project}_cur"
}

output "athena_workgroup_name" {
  description = "Athena workgroup name for cost queries"
  value       = aws_athena_workgroup.cur.name
}

output "athena_results_bucket_arn" {
  description = "ARN of the S3 bucket for Athena query results"
  value       = aws_s3_bucket.athena_results.arn
}

output "cur_data_bucket_arn" {
  description = "ARN of the S3 bucket containing CUR Parquet data"
  value       = aws_s3_bucket.cur_data.arn
}
