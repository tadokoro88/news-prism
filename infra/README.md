# infra/

News Prism の AWS リソース (Lambda + API Gateway + DynamoDB) を Terraform で管理する。

関連:
- DECISION-0015 (DynamoDB schema)
- DECISION-0016 (IaC ツール / Lambda packaging / state backend)
- SPEC §14 (Phase 2 仕様)

## 構成

```
infra/
├── main.tf              provider / terraform block
├── variables.tf         入力変数
├── locals.tf            派生値
├── dynamodb.tf          news-prism-analyses + GSI1
├── lambda.tf            関数 + IAM + Log Group + zip 化
├── api_gateway.tf       REST API + API Key + Usage Plan
├── outputs.tf           invoke URL / API Key (sensitive)
├── build.sh             Lambda zip を作るスクリプト
├── requirements-lambda.txt  Lambda 用 deps (boto3 / dotenv は除外)
└── terraform.tfvars.example  tfvars テンプレ
```

## 初回デプロイ

```bash
cd infra

# 1. tfvars 作成 (PAT は tfvars に入れない、Secrets Manager で扱う)
cp terraform.tfvars.example terraform.tfvars
# bedrock_model_id を埋める

# 2. Lambda zip ビルド (Mac から Linux 用 wheel を pull)
./build.sh

# 3. Terraform
terraform init
terraform plan
terraform apply

# 4. GitHub PAT を Secrets Manager に投入 (out-of-band、tfstate に乗らない)
aws secretsmanager put-secret-value \
  --secret-id news-prism/github-pat \
  --secret-string "github_pat_xxxxxxxxxxxx"
```

> PAT を `terraform.tfvars` 経由で渡さない理由: local state を選んでいるため
> `terraform.tfstate` に plaintext で残ってしまう (DECISION-0017)。
> Terraform は **secret の「枠」だけ作る**。値は AWS CLI で別途 put する。

PAT を rotate するときも `put-secret-value` を実行するだけ (Lambda コンテナの
warm 起動中はキャッシュが残るので、次の cold start で反映)。

## 動作確認

```bash
# invoke URL と API Key を取得
URL=$(terraform output -raw invoke_url)
KEY=$(terraform output -raw api_key_value)

# 1 記事投げてみる
curl -X POST "$URL" \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://aws.amazon.com/jp/blogs/news/some-article/"}'

# Lambda logs (--follow で追跡)
LOG=$(terraform output -raw lambda_log_group)
aws logs tail "$LOG" --follow

# DynamoDB に保存されたか
TBL=$(terraform output -raw table_name)
aws dynamodb scan --table-name "$TBL" --max-items 5
```

## コード変更時の再 deploy

```bash
./build.sh          # zip 再生成
terraform apply     # archive_file の hash が変わるので Lambda update が走る
```

## State

local state (`terraform.tfstate`) で運用。共同編集 / CI 化のタイミングで S3 backend に移行する (DECISION-0016)。

## 後で見直し予定

- IAM の `bedrock:InvokeModel` の Resource を CRIS profile + model ARN に絞る
- (公開時) `terraform.tfstate` を S3 backend + DynamoDB lock に移行

---

## Phase 3: Web UI のデプロイ

Route53 hosted zone が **別 account (例: mgmt)** にあるため、DNS レコードは Terraform で
扱わず手動で投入する。以下、`web_domain` 変数で指定した FQDN を `${web_domain}` と表記する。
手順:

### 1 段階目: ACM cert だけ先に作る

```bash
cd infra
./build.sh  # Lambda 用に zip を最新化 (Web UI ファイル変更だけなら不要)
terraform apply -target=aws_acm_certificate.web
terraform output acm_validation_records
```

`acm_validation_records` で出る `name` / `type` / `value` を **親 account の Route53
hosted zone に手動で CNAME 投入**する。1 件のみ (`_xxx.${web_domain}.` → `_yyy.acm-validations.aws.`)。

### 2 段階目: 検証完了を待って残りを作る

```bash
terraform apply
```

- `aws_acm_certificate_validation` が ACM の検証完了を最大 45 分待つ (通常 5-30 分で完了)
- 検証完了後、CloudFront / S3 / API Gateway origin 統合 / Web UI ファイル upload が走る

### 3 段階目: CloudFront の alias を mgmt に手動投入

```bash
terraform output cloudfront_domain_name
terraform output cloudfront_hosted_zone_id   # 固定値 Z2FDTNDATAQYW2 (CloudFront 共通)
```

親 account の hosted zone に以下の **A レコード (Alias)** を手動追加:

| Name | Type | Alias to | Hosted zone ID |
|---|---|---|---|
| `${web_domain}` | A | `${cloudfront_domain_name}` | `Z2FDTNDATAQYW2` |

(AAAA も同様に追加すると IPv6 も有効になる)

### 動作確認

```bash
curl https://${web_domain}/             # index.html が返る
curl https://${web_domain}/api/analyze  # 405 (POST のみ許容)
```

ブラウザで `https://${web_domain}/` を開いて URL 入力 → 解析。
**ブラウザは API Key を持たない** (CloudFront → API Gateway 方向で Origin Custom Header 注入)。

### Web UI ファイル更新

```bash
# src/web/index.html を編集してから
terraform apply  # aws_s3_object の etag (filemd5) が変わって自動で再 upload
```

CloudFront default behavior は `Managed-CachingOptimized` を使っているため、
古い HTML がエッジに残る可能性あり。即時反映したい時:

```bash
DIST=$(terraform state show aws_cloudfront_distribution.web | grep '^id' | head -1 | awk '{print $3}' | tr -d '"')
aws cloudfront create-invalidation --distribution-id "$DIST" --paths '/*'
```
