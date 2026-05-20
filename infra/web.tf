# Web UI 配信スタック:
#   - S3 (private bucket、OAC 経由でのみ CloudFront から読める)
#   - CloudFront distribution
#     - Default behavior: S3 origin (静的ファイル)
#     - /api/* behavior: API Gateway origin (CF Function でパス書換)
#   - API Key は CloudFront → API Gateway の Origin Custom Header で注入
#     (ブラウザは API Key を持たない = JS に露出しない)
#
# Route53 alias / ACM validation CNAME は別 account の hosted zone のため
# Terraform 外で手動投入する (outputs.tf の web_route53_records, acm_validation 参照)

# ---- S3 bucket ----

resource "aws_s3_bucket" "web" {
  bucket = local.web_bucket_name
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3 を declarative に。2023 以降の AWS default と一致するが、明示する
resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 静的ファイルアップロード (web ディレクトリ全件を再帰スキャン)
# macOS Finder が生成する .DS_Store などは除外
locals {
  _web_files_all = fileset(local.web_dir, "**/*")
  web_files = toset([
    for f in local._web_files_all : f
    if !endswith(f, ".DS_Store") && !startswith(basename(f), "._")
  ])

  mime_types = {
    "html"        = "text/html; charset=utf-8"
    "css"         = "text/css; charset=utf-8"
    "js"          = "application/javascript; charset=utf-8"
    "json"        = "application/json"
    "webmanifest" = "application/manifest+json"
    "svg"         = "image/svg+xml"
    "png"         = "image/png"
    "ico"         = "image/x-icon"
  }
}

resource "aws_s3_object" "web" {
  for_each = local.web_files

  bucket       = aws_s3_bucket.web.id
  key          = each.value
  source       = "${local.web_dir}/${each.value}"
  etag         = filemd5("${local.web_dir}/${each.value}")
  content_type = lookup(local.mime_types, lower(reverse(split(".", each.value))[0]), "application/octet-stream")
}

# ---- CloudFront ----

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${var.project_name}-web-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# /api/X → /v1/X にパス書換 (既存 API Gateway ステージを壊さない)
resource "aws_cloudfront_function" "api_rewrite" {
  name    = "${var.project_name}-api-rewrite"
  runtime = "cloudfront-js-2.0"
  code    = <<-EOT
    function handler(event) {
      var request = event.request;
      if (request.uri.startsWith('/api/')) {
        request.uri = '/${var.api_stage_name}' + request.uri.substring(4);
      }
      return request;
    }
  EOT
}

# Managed Cache Policies
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# Managed Response Headers Policy (HSTS / X-Content-Type-Options / X-Frame-Options /
# Referrer-Policy / Strict-Transport-Security 等を自動付与)
data "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_200" # Asia/Europe/NA、SA/AF/OC を除いてコスト圧縮
  aliases             = [var.web_domain]
  http_version        = "http2and3"

  # --- S3 origin ---
  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  # --- API Gateway origin (with API Key injected) ---
  origin {
    domain_name = "${aws_api_gateway_rest_api.this.id}.execute-api.${var.aws_region}.amazonaws.com"
    origin_id   = "apigw"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    # CloudFront → API Gateway の方向で API Key を注入 (browser には露出しない)
    custom_header {
      name  = "x-api-key"
      value = aws_api_gateway_api_key.this.value
    }
  }

  # --- Default behavior: S3 ---
  default_cache_behavior {
    target_origin_id           = "s3-web"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  # --- /api/* behavior: API Gateway ---
  ordered_cache_behavior {
    path_pattern               = "/api/*"
    target_origin_id           = "apigw"
    viewer_protocol_policy     = "https-only"
    allowed_methods            = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.api_rewrite.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.web.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  # SPA 風の動作は不要 (今は index.html 1 枚)。後で 404 / 403 を index.html にしたければ追加
}

# ---- S3 bucket policy: CloudFront OAC からのみ許可 ----

data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid     = "AllowCloudFrontOAC"
    actions = ["s3:GetObject"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    resources = ["${aws_s3_bucket.web.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }

  # defense-in-depth: 非 HTTPS なリクエストを明示拒否 (OAC は HTTPS なので実害なし)
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [
      aws_s3_bucket.web.arn,
      "${aws_s3_bucket.web.arn}/*",
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json
}
