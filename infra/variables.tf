variable "aws_region" {
  description = "AWS region (Bedrock + DynamoDB + Lambda)。APAC CRIS なので ap-northeast-1 想定"
  type        = string
  default     = "ap-northeast-1"
}

variable "project_name" {
  description = "リソース命名に使う prefix"
  type        = string
  default     = "news-prism"
}

variable "bedrock_model_id" {
  description = "Bedrock Converse API に渡すモデル ID (CRIS inference profile ID も可)"
  type        = string
}

variable "context_url" {
  description = "個人化 context.md を fetch する URL (例: private repo の raw URL)。空文字なら context なしで動作 (3 視点解析として動く)"
  type        = string
  default     = ""
}

variable "lambda_timeout_seconds" {
  description = "Lambda timeout。4 並列 Bedrock 呼び出しが最遅 ~27s なので margin 含めて 60s"
  type        = number
  default     = 60
}

variable "lambda_memory_mb" {
  description = "Lambda memory。trafilatura/lxml の peak と Bedrock streaming buffer を考慮"
  type        = number
  default     = 1024
}

variable "lambda_log_retention_days" {
  description = "CloudWatch Logs の保持日数"
  type        = number
  default     = 30
}

variable "api_stage_name" {
  description = "API Gateway の stage 名"
  type        = string
  default     = "v1"
}

variable "api_throttle_burst" {
  description = "Usage plan の burst limit (短期 spike)"
  type        = number
  default     = 5
}

variable "api_throttle_rate" {
  description = "Usage plan の rate limit (rps)"
  type        = number
  default     = 2
}

variable "api_quota_limit" {
  description = "Usage plan の日次 quota"
  type        = number
  default     = 100
}

# --- Web UI (Phase 3) ---

variable "web_domain" {
  description = "Web UI を公開するドメイン (例: news-prism.example.com)。必須 — local tfvars に実値を設定する"
  type        = string
}

# --- Cost guardrails (Budgets) ---

variable "budget_notification_email" {
  description = "AWS Budgets の alert 通知先 email"
  type        = string
}

variable "budget_monthly_limit_usd" {
  description = "AWS Budgets の月額上限 (USD)。50%/100%/予測 200% で email 通知"
  type        = number
  default     = 50
}
