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
  description = "Max number of WARN notifications to send per collection, per day"
}
