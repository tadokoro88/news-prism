# GitHub PAT を Secrets Manager に格納。
#
# 設計 (DECISION-0017):
#  - 「枠」だけ Terraform が作る (name / 説明 / 削除猶予)
#  - 値 (SecretString) は意図的に Terraform 管理外にする。理由: tfstate に
#    plaintext で残るのを避けたい (local state なので disk 流出時に直撃)
#  - 値は AWS CLI で out-of-band put する:
#      aws secretsmanager put-secret-value \
#        --secret-id news-prism/github-pat \
#        --secret-string "github_pat_xxxx"
#  - Lambda は GetSecretValue で取得 (IAM は lambda.tf の inline policy)

resource "aws_secretsmanager_secret" "github_pat" {
  name        = "${var.project_name}/github-pat"
  description = "Fine-grained PAT for my-goals private repo. Value is set out-of-band (see infra/secrets.tf comment)."

  # 個人ツールで誤削除リカバリのリスクは低い + やり直しを軽くしたい
  recovery_window_in_days = 7
}

# 意図的に aws_secretsmanager_secret_version を作らない。
# 初回 apply 後、`aws secretsmanager put-secret-value` で値を投入する。
