terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # 個人ツール / 1 人開発のため local state で開始。
  # 必要になったら S3 backend に移行 (DECISION-0016)。
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "news-prism"
      ManagedBy = "terraform"
      Owner     = "taka"
    }
  }
}

# CloudFront 用 ACM cert は us-east-1 必須
provider "aws" {
  alias  = "use1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "news-prism"
      ManagedBy = "terraform"
      Owner     = "taka"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
