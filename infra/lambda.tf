# ---- IAM ----

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_inline" {
  # Bedrock — CRIS inference profile + 配下の region 別 model ARN 両方が必要なため
  # Resource: "*" にする。読み取り専用 (InvokeModel + Stream) なので blast radius は限定的
  statement {
    sid = "BedrockInvoke"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = ["*"]
  }

  # DynamoDB — テーブル + GSI のみ
  statement {
    sid     = "DynamoDBWrite"
    actions = ["dynamodb:PutItem"]
    resources = [
      aws_dynamodb_table.analyses.arn,
      "${aws_dynamodb_table.analyses.arn}/index/*",
    ]
  }

  # Secrets Manager — GitHub PAT 取得用、特定 secret ARN に絞る
  statement {
    sid       = "SecretsManagerRead"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.github_pat.arn]
  }
}

resource "aws_iam_role_policy" "lambda_inline" {
  name   = "${local.function_name}-inline"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_inline.json
}

# ---- Build artifact ----

# build.sh が事前に実行されている前提で、build/ を zip 化する
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = local.build_dir
  output_path = local.zip_path
}

# ---- Function ----

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_lambda_function" "analyze" {
  function_name    = local.function_name
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "news_prism.lambda_handler.lambda_handler"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb
  architectures    = ["x86_64"]

  environment {
    variables = {
      BEDROCK_MODEL_ID             = var.bedrock_model_id
      ANALYSES_TABLE_NAME          = aws_dynamodb_table.analyses.name
      NEWS_PRISM_CONTEXT_URL       = var.context_url
      NEWS_PRISM_GH_PAT_SECRET_ARN = aws_secretsmanager_secret.github_pat.arn
      LOG_LEVEL                    = "INFO"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic,
    aws_cloudwatch_log_group.lambda,
  ]
}
