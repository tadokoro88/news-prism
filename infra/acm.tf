# CloudFront 用 ACM cert は us-east-1 リージョン必須。
#
# DNS validation だが、Route53 hosted zone が別 account (mgmt) にあるため
# Terraform からは validation record を投入しない。ユーザが手動で mgmt の
# Route53 zone に CNAME を追加する (`terraform output acm_validation` 参照)。
#
# 検証完了は `aws_acm_certificate_validation` で wait する。
# 二段階 apply:
#   1) `terraform apply -target=aws_acm_certificate.web` → cert 作成、validation 値 output
#   2) ユーザが mgmt zone に CNAME を手動投入
#   3) `terraform apply` → validation 待ち合わせ + CloudFront 作成

resource "aws_acm_certificate" "web" {
  provider          = aws.use1
  domain_name       = var.web_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# 検証完了を待つだけ (record は別 account にあるため作らない)。
# ユーザが手動で CNAME 投入後、ACM が自動検出するまで 5-30 分かかる
resource "aws_acm_certificate_validation" "web" {
  provider        = aws.use1
  certificate_arn = aws_acm_certificate.web.arn

  timeouts {
    create = "45m"
  }
}
