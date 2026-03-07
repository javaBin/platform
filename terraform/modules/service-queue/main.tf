################################################################################
# SQS Queue with Dead-Letter Queue
################################################################################

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.project}-${var.name}-dlq"
  message_retention_seconds = var.dlq_retention_seconds

  tags = {
    Name    = "${var.project}-${var.name}-dlq"
    service = var.service
  }
}

resource "aws_sqs_queue" "this" {
  name                       = "${var.project}-${var.name}"
  visibility_timeout_seconds = var.visibility_timeout_seconds
  message_retention_seconds  = var.retention_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = {
    Name    = "${var.project}-${var.name}"
    service = var.service
  }
}

################################################################################
# IAM Policy Document — for attaching to task roles
################################################################################

data "aws_iam_policy_document" "access" {
  statement {
    sid    = "QueueAccess"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [
      aws_sqs_queue.this.arn,
      aws_sqs_queue.dlq.arn,
    ]
  }
}
