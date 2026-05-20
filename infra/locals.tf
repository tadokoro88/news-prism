locals {
  table_name    = "${var.project_name}-analyses"
  function_name = "${var.project_name}-analyze"
  api_name      = "${var.project_name}-api"
  api_key_name  = "${var.project_name}-api-key"

  build_dir = "${path.module}/build"
  zip_path  = "${path.module}/lambda.zip"

  # Web UI (Phase 3)
  web_bucket_name = "${var.project_name}-web-${data.aws_caller_identity.current.account_id}"
  web_dir         = "${path.module}/../src/web"
}
