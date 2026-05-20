output "invoke_url" {
  description = "POST {invoke_url}/analyze で叩く"
  value       = "${aws_api_gateway_stage.this.invoke_url}/analyze"
}

output "api_key_value" {
  description = "API Gateway API Key の値。x-api-key header に乗せる"
  value       = aws_api_gateway_api_key.this.value
  sensitive   = true
}

output "table_name" {
  description = "DynamoDB analyses テーブル名"
  value       = aws_dynamodb_table.analyses.name
}

output "lambda_function_name" {
  description = "Lambda 関数名 (logs / invoke 用)"
  value       = aws_lambda_function.analyze.function_name
}

output "lambda_log_group" {
  description = "CloudWatch Logs group"
  value       = aws_cloudwatch_log_group.lambda.name
}

# --- Phase 3 / Web UI ---

# ACM cert の DNS validation 用 CNAME を mgmt zone に手動投入する
output "acm_validation_records" {
  description = "mgmt account の Route53 (korotora.com zone) に手動投入する CNAME"
  value = [
    for opt in aws_acm_certificate.web.domain_validation_options : {
      name  = opt.resource_record_name
      type  = opt.resource_record_type
      value = opt.resource_record_value
    }
  ]
}

# CloudFront 配信ドメイン (mgmt zone に alias record として登録)
output "cloudfront_domain_name" {
  description = "CloudFront distribution の domain (mgmt zone の ALIAS A レコードに設定)"
  value       = aws_cloudfront_distribution.web.domain_name
}

output "cloudfront_hosted_zone_id" {
  description = "CloudFront の Route53 hosted zone ID (alias target zone、固定値 Z2FDTNDATAQYW2)"
  value       = aws_cloudfront_distribution.web.hosted_zone_id
}

output "web_url" {
  description = "Web UI URL"
  value       = "https://${var.web_domain}"
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (cache invalidation 用)"
  value       = aws_cloudfront_distribution.web.id
}

output "web_bucket_name" {
  description = "Web UI 用 S3 bucket"
  value       = aws_s3_bucket.web.id
}
