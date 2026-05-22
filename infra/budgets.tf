# Cost guardrails — Budgets only
#
# 個人ツールで「うっかり認証なし公開」+「Bedrock の per-token 課金」の組み合わせは
# 気付かないうちに月数万円コースになり得るため、Budgets で多段 alert を張る。
# auto-stop 機能はないので、alert を受けて手で API key 失効 / Lambda 殺し
# まで持っていく前提の早期警報。
#
# Cost Anomaly Detection は AWS の Default-Services-Monitor (アカウント作成時に
# 自動付与される DIMENSIONAL monitor) を console 側でそのまま使う方針 (DECISION-0021)。
# Terraform から扱うとアカウント固有 ARN を public repo に持つ必要が出るため除外。

resource "aws_budgets_budget" "monthly" {
  name         = "${var.project_name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # actual 50% で「半月時点で予算半分使った」軽い注意
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  # actual 100% で「予算到達」、要対応
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  # forecast 200% で「現ペースで月末に予算 2 倍到達」= abuse の早期検知
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 200
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
