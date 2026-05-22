# Cost guardrails — Budgets + Cost Anomaly Detection
#
# 個人ツールで「うっかり認証なし公開」+「Bedrock の per-token 課金」の組み合わせは
# 気付かないうちに月数万円コースになり得るため、二段で保険を張る:
#   1. Budgets: 月額の絶対上限。actual 50%/100% と forecast 200% で email
#   2. Cost Anomaly Detection: サービス単位の "通常時より N USD 以上多い" を検知
#
# どちらも auto-stop 機能はないので、alert を受けて手で API key 失効 / Lambda 殺し
# まで持っていく前提の早期警報。

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

resource "aws_ce_anomaly_monitor" "service" {
  name              = "${var.project_name}-service-anomaly"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "service" {
  name             = "${var.project_name}-service-anomaly-sub"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.service.arn]

  subscriber {
    type    = "EMAIL"
    address = var.budget_notification_email
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = [tostring(var.cost_anomaly_threshold_usd)]
    }
  }
}
