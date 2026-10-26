################################################################################
# Adopted RDS instance — an existing database brought under Terraform
#
# For apps that move onto the platform with a database that already exists
# and must not move. The generated root module imports the instance into
# aws_db_instance.this with an `import {}` block; nothing is created.
#
# What this module manages:  tags (via provider default_tags + var.tags),
#                            deletion_protection, backup_retention_period
#                            (only when set), and the final-snapshot settings
#                            Terraform uses if the instance is ever deleted.
# What it never touches:     everything else. Engine, class, storage,
#                            network, security groups, parameter group,
#                            password, monitoring... stay exactly as they are
#                            in AWS and are changed outside this module (or
#                            the app moves to service-rds later).
#
# instance_class is required by the provider, so it is read from the live
# instance. All other unmanaged arguments are in ignore_changes, so an import
# never plans an in-place change or a replacement for them.
#
# To stop managing the instance without deleting it, add a hand-written file
# next to the generated ones (e.g. terraform/release-db.tf):
#
#   removed {
#     from = module.rds_<name>.aws_db_instance.this
#     lifecycle { destroy = false }
#   }
#
# and remove the database from app.yaml in the same change.
################################################################################

data "aws_db_instance" "live" {
  db_instance_identifier = var.identifier
}

# Suffix for the final snapshot name, fixed for the life of this state, so a
# later delete/restore/re-adopt cycle does not collide with an old snapshot.
resource "random_id" "final_snapshot" {
  byte_length = 4
  keepers = {
    identifier = var.identifier
  }
}

resource "aws_db_instance" "this" {
  identifier     = var.identifier
  instance_class = data.aws_db_instance.live.db_instance_class

  deletion_protection     = var.deletion_protection
  backup_retention_period = coalesce(var.backup_retention_period, data.aws_db_instance.live.backup_retention_period)

  # Terraform-only settings (no API call): keep a final snapshot and the
  # automated backups if the instance is ever deleted through Terraform.
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.identifier}-final-${random_id.final_snapshot.hex}"
  delete_automated_backups  = false

  tags = var.tags

  lifecycle {
    prevent_destroy = true

    # Mirrored from AWS, never changed by this module.
    ignore_changes = [
      instance_class,
      engine,
      engine_version,
      allocated_storage,
      max_allocated_storage,
      storage_type,
      iops,
      storage_throughput,
      storage_encrypted,
      kms_key_id,
      username,
      password,
      manage_master_user_password,
      master_user_secret_kms_key_id,
      db_name,
      port,
      publicly_accessible,
      multi_az,
      availability_zone,
      db_subnet_group_name,
      vpc_security_group_ids,
      network_type,
      parameter_group_name,
      option_group_name,
      ca_cert_identifier,
      iam_database_authentication_enabled,
      performance_insights_enabled,
      performance_insights_kms_key_id,
      performance_insights_retention_period,
      monitoring_interval,
      monitoring_role_arn,
      enabled_cloudwatch_logs_exports,
      copy_tags_to_snapshot,
      auto_minor_version_upgrade,
      allow_major_version_upgrade,
      backup_window,
      maintenance_window,
      apply_immediately,
      license_model,
      customer_owned_ip_enabled,
      dedicated_log_volume,
    ]
  }
}
