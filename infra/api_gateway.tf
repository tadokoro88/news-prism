# REST API + API Key + Usage Plan
# (HTTP API より高いが、API Key 認証が native サポート / DECISION-0016)

resource "aws_api_gateway_rest_api" "this" {
  name        = local.api_name
  description = "News Prism analyze endpoint"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

resource "aws_api_gateway_resource" "analyze" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "analyze"
}

resource "aws_api_gateway_method" "analyze_post" {
  rest_api_id      = aws_api_gateway_rest_api.this.id
  resource_id      = aws_api_gateway_resource.analyze.id
  http_method      = "POST"
  authorization    = "NONE"
  api_key_required = true
}

resource "aws_api_gateway_integration" "analyze_post" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.analyze.id
  http_method             = aws_api_gateway_method.analyze_post.http_method
  type                    = "AWS_PROXY"
  integration_http_method = "POST"
  uri                     = aws_lambda_function.analyze.invoke_arn
}

resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.analyze.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/*"
}

# --- Deployment / Stage ---

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id

  # method / integration が変わったら deploy し直す
  triggers = {
    redeploy = sha1(jsonencode([
      aws_api_gateway_resource.analyze.id,
      aws_api_gateway_method.analyze_post.id,
      aws_api_gateway_integration.analyze_post.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "this" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  deployment_id = aws_api_gateway_deployment.this.id
  stage_name    = var.api_stage_name
}

# --- API Key + Usage Plan ---

resource "aws_api_gateway_api_key" "this" {
  name = local.api_key_name
}

resource "aws_api_gateway_usage_plan" "this" {
  name = "${var.project_name}-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.this.id
    stage  = aws_api_gateway_stage.this.stage_name
  }

  throttle_settings {
    burst_limit = var.api_throttle_burst
    rate_limit  = var.api_throttle_rate
  }

  quota_settings {
    limit  = var.api_quota_limit
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "this" {
  key_id        = aws_api_gateway_api_key.this.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.this.id
}
