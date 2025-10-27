// -- Event Handler
resource "aws_lambda_function" "event_handler" {
  function_name     = "${local.prefix}-event-handler"
  handler           = "podaac.sigevent.event_handler.invoke"
  role              = aws_iam_role.event_handler.arn
  runtime           = "python3.11"
  timeout           = 60

  filename = "${path.module}/../dist/${local.name}-${local.version}.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/${local.name}-${local.version}.zip")
}

resource "aws_lambda_event_source_mapping" "sigevent_event_source_mapping" {
  event_source_arn = aws_sqs_queue.sigevent_input_queue.arn
  enabled          = true
  function_name    = aws_lambda_function.event_handler.function_name
  batch_size       = 1  # TODO: add rejected message return to lambda
}

resource "aws_iam_role" "event_handler" {
  name_prefix          = "event-handler"
  path                 = "${local.service_path}/"
  permissions_boundary = data.aws_iam_policy.permissions_boundary.arn

  assume_role_policy   = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Sid    = ""
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      },
    ]
  })

  inline_policy {
    name   = "EventHandlerPolicy"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Resource = aws_sqs_queue.sigevent_input_queue.arn
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
      }, {
        Effect = "Allow"
        Resource = "${aws_cloudwatch_log_group.sigevent.arn}:log-stream:*"
        Action = [
          "logs:PutLogEvents",
          "logs:CreateLogStream"
        ]
      }, {
        Effect = "Allow"
        Resource = aws_dynamodb_table.notification_count.arn,
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
      }]
    })
  }
}

// -- Daily Report Generator
resource "aws_lambda_function" "daily_report_generator" {
  count = var.muted_mode ? 0 : 1
  function_name     = "${local.prefix}-daily-report-generator"
  handler           = "podaac.sigevent.daily_report_gen.invoke"
  role              = aws_iam_role.daily_report[0].arn
  runtime           = "python3.11"
  timeout           = 60

  filename = "${path.module}/../dist/${local.name}-${local.version}.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/${local.name}-${local.version}.zip")
}

resource "aws_iam_role" "daily_report" {
  count = var.muted_mode ? 0 : 1
  name_prefix          = "daily-report-generator"
  path                 = "${local.service_path}/"
  permissions_boundary = data.aws_iam_policy.permissions_boundary.arn

  assume_role_policy   = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Sid    = ""
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      },
    ]
  })
}

// -- IAM Policy Attachments
resource "aws_iam_role_policy_attachment" "event_handler-allow_ssm_access" {
  role = aws_iam_role.event_handler.name
  policy_arn = aws_iam_policy.allow_ssm_access.arn
}

resource "aws_iam_role_policy_attachment" "event_handler-allow_cloudwatch_logging" {
  role = aws_iam_role.event_handler.name
  policy_arn = aws_iam_policy.allow_cloudwatch_logging.arn
}

resource "aws_iam_role_policy_attachment" "event_handler-allow_ses_send" {
  count = var.muted_mode ? 0 : 1

  role = aws_iam_role.event_handler.name
  policy_arn = aws_iam_policy.allow_ses_send[0].arn
}

resource "aws_iam_role_policy_attachment" "daily_report-allow_ssm_access" {
  count = var.muted_mode ? 0 : 1
  role = aws_iam_role.daily_report[0].name
  policy_arn = aws_iam_policy.allow_ssm_access.arn
}

resource "aws_iam_role_policy_attachment" "daily_report-allow_cloudwatch_logging" {
  count = var.muted_mode ? 0 : 1
  role = aws_iam_role.daily_report[0].name
  policy_arn = aws_iam_policy.allow_cloudwatch_logging.arn
}

resource "aws_iam_role_policy_attachment" "daily_report-allow_ses_send" {
  count = var.muted_mode ? 0 : 1
  role = aws_iam_role.daily_report[0].name
  policy_arn = aws_iam_policy.allow_ses_send[0].arn
}

// -- Role IAM Policies
resource "aws_iam_role_policy" "daily_report" {
  count = var.muted_mode ? 0 : 1
  name_prefix = "DailyReportPolicy"
  role = aws_iam_role.daily_report[0].name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "logs:FilterLogEvents"
      Resource = "${aws_cloudwatch_log_group.sigevent.arn}:log-stream:*"
    }]
  })
}

// -- Shared IAM Policies
resource "aws_iam_policy" "allow_ses_send" {
  count = var.muted_mode ? 0 : 1

  name_prefix = "AllowSESSend"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ses:SendEmail",
        "ses:SendRawEmail"
      ]
      Resource = [
        var.ses_sender_arn,
        aws_ses_configuration_set.default.arn
      ]
    }]
  })
}

resource "aws_iam_policy" "allow_ssm_access" {
  name_prefix = "AllowSSMAccess"
  path = "${local.service_path}/"
  description = "Allow service to access runtime configurations"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${local.service_path}/*"
      Action = [
          "ssm:GetParametersByPath",
          "ssm:GetParameters",
          "ssm:GetParameter"
      ]
    }]
  })
}

resource "aws_iam_policy" "allow_cloudwatch_logging" {
  name_prefix = "AllowCloudWatchLogging"
  path = "${local.service_path}/"
  description = "Allow lambdas to log to Cloudwatch"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Resource = "*"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:DescribeLogStreams",
        "logs:PutLogEvents"
      ]
    }]
  })
}

// -- SES Config
resource "aws_ses_configuration_set" "default" {
  provider = aws.ses_region
  name = "${local.prefix}-default"
  
  reputation_metrics_enabled = true

  delivery_options {
    tls_policy = "Require"
  }
}

// -- Runtime Configuration
resource "aws_ssm_parameter" "ses_sender_arn" {
  name = "${local.service_path}/ses_sender_arn"
  type = "String"
  value = var.ses_sender_arn
}

resource "aws_ssm_parameter" "ses_region" {
  name = "${local.service_path}/ses_region"
  type = "String"
  value = var.ses_region
}

resource "aws_ssm_parameter" "ses_config_set_name" {
  name = "${local.service_path}/ses_config_set_name"
  type = "String"
  value = aws_ses_configuration_set.default.name
}

resource "aws_ssm_parameter" "notification_emails" {
  name = "${local.service_path}/notification_emails"
  value = jsonencode(var.notification_emails)
  type = "String"
}

resource "aws_ssm_parameter" "log_group" {
  name = "${local.service_path}/log_group"
  value = aws_cloudwatch_log_group.sigevent.name
  type = "String"
}

resource "aws_ssm_parameter" "log_level" {
  name = "${local.service_path}/log_level"
  value = var.log_level
  type = "String"
}

resource "aws_ssm_parameter" "notification_table_name" {
  name = "${local.service_path}/notification_table_name"
  value = aws_dynamodb_table.notification_count.name
  type = "String"
}

resource "aws_ssm_parameter" "stage" {
  name = "${local.service_path}/stage"
  value = upper(var.environment)
  type = "String"
}

resource "aws_ssm_parameter" "max_daily_warns" {
  name = "${local.service_path}/max_daily_warns"
  value = tostring(var.max_daily_warns)
  type = "String"
}

resource "aws_ssm_parameter" "muted_mode" {
  name = "${local.service_path}/muted_mode"
  value = tostring(var.muted_mode)
  type = "String"
}
