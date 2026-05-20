# DECISION-0015: DynamoDB schema
#  Main PK: analysis_id (ULID)
#  GSI1:    user_id (固定 "taka") + created_at (ISO 8601 Z)
#  on-demand / TTL なし

resource "aws_dynamodb_table" "analyses" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "analysis_id"

  attribute {
    name = "analysis_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi1"
    hash_key        = "user_id"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }
}
