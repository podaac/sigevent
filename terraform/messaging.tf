// -- SNS --

resource "aws_sns_topic" "sigevent_input_topic" {
  name = "${var.service_name}-${var.environment}-input-topic"
}

resource "aws_sns_topic_subscription" "sigevent_subscription" {
  topic_arn = aws_sns_topic.sigevent_input_topic.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.sigevent_input_queue.arn
}

resource "aws_sns_topic_policy" "input_topic_policy" {
  arn = aws_sns_topic.sigevent_input_topic.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "SNS:Publish"
      Effect = "Allow"
      Resource = aws_sns_topic.sigevent_input_topic.arn,
      Principal = {
        AWS = concat(
          [data.aws_caller_identity.current.account_id],
          var.authorized_accounts
        )
      }
    }]
  })
}

resource "aws_ssm_parameter" "topic_arn" {
  name = "${local.service_path}/topic_arn"
  value = aws_sns_topic.sigevent_input_topic.arn
  type = "String"
}

// -- SQS --

resource "aws_sqs_queue" "sigevent_input_queue" {
  name = "${local.prefix}-queue"
  visibility_timeout_seconds = 120
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.sigevent_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "sigevent_dlq" {
  name = "${local.prefix}-dlq"
}

data "aws_iam_policy_document" "sigevent_sns_to_sqs_policy_doc" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.sigevent_input_queue.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.sigevent_input_topic.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "sigevent_sns_to_sqs_policy" {
  queue_url = aws_sqs_queue.sigevent_input_queue.url
  policy    = data.aws_iam_policy_document.sigevent_sns_to_sqs_policy_doc.json
}
