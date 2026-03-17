################################################################################
# CloudWatch Alarms — ECS service health monitoring
################################################################################

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "${var.team}-${var.name}-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = var.cpu_threshold
  alarm_description   = "CPU utilization above ${var.cpu_threshold}% for ${var.name}"
  alarm_actions       = var.sns_topic_arns
  ok_actions          = var.sns_topic_arns

  dimensions = {
    ClusterName = var.cluster_name
    ServiceName = var.name
  }

  tags = {
    Name    = "${var.team}-${var.name}-cpu-high"
    service = var.service
  }
}

resource "aws_cloudwatch_metric_alarm" "memory_high" {
  alarm_name          = "${var.team}-${var.name}-memory-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 60
  statistic           = "Average"
  threshold           = var.memory_threshold
  alarm_description   = "Memory utilization above ${var.memory_threshold}% for ${var.name}"
  alarm_actions       = var.sns_topic_arns
  ok_actions          = var.sns_topic_arns

  dimensions = {
    ClusterName = var.cluster_name
    ServiceName = var.name
  }

  tags = {
    Name    = "${var.team}-${var.name}-memory-high"
    service = var.service
  }
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_targets" {
  count               = var.enable_alb_alarms ? 1 : 0
  alarm_name          = "${var.team}-${var.name}-unhealthy-targets"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Maximum"
  threshold           = 0
  alarm_description   = "Unhealthy targets detected for ${var.name}"
  alarm_actions       = var.sns_topic_arns
  ok_actions          = var.sns_topic_arns

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  tags = {
    Name    = "${var.team}-${var.name}-unhealthy-targets"
    service = var.service
  }
}

resource "aws_cloudwatch_metric_alarm" "target_5xx" {
  count               = var.enable_alb_alarms ? 1 : 0
  alarm_name          = "${var.team}-${var.name}-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.error_5xx_threshold
  alarm_description   = "5xx errors above ${var.error_5xx_threshold} in 5min for ${var.name}"
  alarm_actions       = var.sns_topic_arns
  ok_actions          = var.sns_topic_arns
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  tags = {
    Name    = "${var.team}-${var.name}-5xx"
    service = var.service
  }
}
