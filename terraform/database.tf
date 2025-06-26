resource "aws_dynamodb_table" "notification_count" {
  name = "${local.prefix}-notification-count"
  hash_key = "message_hash"

  billing_mode = "PAY_PER_REQUEST"

  attribute {
    name = "message_hash"
    type = "S"
  }

  ttl {
    attribute_name = "expiration"
    enabled = true
  }
}
