// -- Log Groups
resource "aws_cloudwatch_log_group" "sigevent" {
  name = local.service_path
}

// -- Daily Report Trigger
resource "aws_cloudwatch_event_rule" "every_24_hours" {
  count = var.muted_mode ? 0 : 1
  name = "${local.prefix}-every-24-hours"
  description = "Trigger rule every 24 hours at 23:50 UTC (16:50 PST)"
  schedule_expression = "cron(50 23 * * ? *)"
}

resource "aws_cloudwatch_event_target" "trigger_report_every_day" {
  count = var.muted_mode ? 0 : 1
  rule = aws_cloudwatch_event_rule.every_24_hours[0].name
  target_id = "sigevent_daily_report_lambda"
  arn = aws_lambda_function.daily_report_generator[0].arn
}

resource "aws_lambda_permission" "allow_cloudwatch_to_call_daily_sigevent_event_lambda" {
  count = var.muted_mode ? 0 : 1
  statement_id = "AllowExecutionFromCloudWatch"
  action = "lambda:InvokeFunction"
  function_name = aws_lambda_function.daily_report_generator[0].function_name
  principal = "events.amazonaws.com"
  source_arn = aws_cloudwatch_event_rule.every_24_hours[0].arn
}
