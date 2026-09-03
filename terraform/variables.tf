variable "service_name" {
  type = string
  default = "sigevent"
}

variable "environment" {
    type = string
}

variable "region" {
    type = string
}

variable "default_tags" {
  type    = map(string)
  default = {}
}

variable "ses_sender_arn" {
  type = string
}

variable "ses_region" {
  type = string
}

variable "notification_emails" {
  type = list(string)
}

variable "authorized_accounts" {
  type = list(string)
  default = []
}

variable "log_level" {
  type = string
  default = "INFO"
}

variable "muted_mode" {
  type = bool
  default = false
  description = "Disables sending of notifications; useful for SIT/UAT"
}

variable "max_daily_warns" {
  type = number
  default = 3
  description = "Max number of SigEvent WARN notifications to send per collection, per day"
}

variable "max_daily_errors" {
  type = number
  default = 20
  description = "Max number of SigEvent ERROR notifications to send per collection, per day. Beyond this the operator gets one 'limit reached' notice and further errors are logged to CloudWatch only. Resets at midnight UTC."
}

variable "storm_threshold" {
  type = number
  default = 10
  description = "SigEvents from one collection within storm_window_minutes above which the collection is considered to be storming. Individual notifications stop and a single summary is sent per window instead."
}

variable "storm_window_minutes" {
  type = number
  default = 5
  description = "Length of the initial SigEvent's storm detection window, in minutes"
}

variable "storm_summary_max_interval_minutes" {
  type = number
  default = 60
  description = "Upper bound on SigEvent's storm summary interval. The window doubles on each roll while a storm continues, so an unattended storm does not produce a summary every few minutes for hours."
}
