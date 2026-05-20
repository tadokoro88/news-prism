# News Prism

通勤中に気になったニュース URL を投げると、複数視点で深掘り解析して返してくれる個人ツール。**Amazon Bedrock + Prompt Caching** をフル活用し、4 並列の persona-driven 解析で wall-time 約 12 秒、cache 全 hit 時 input cost 約 60% 削減を実機で確認している。

> 「ChatGPT に投げる」との違い: **個人化 (自分の OKR / 進捗を context に持ち込み)** / **データ主権 (Bedrock 経由)** / **構造化出力 (JSON)** / **履歴 (DynamoDB + Web UI)**

---

## 何ができる

- 記事 URL を 1 つ投げると、3 視点 (任意定義) + 中立的要約 + アクション提案 + 関連 OKR 紐付けを構造化 JSON で返す
- スマホ Safari からアクセス、結果は CloudFront 経由の素 HTML/JS で読む (PWA 対応、ホーム画面に追加可能)
- 解析履歴は DynamoDB に永続化、後から見返せる
- `NEWS_PRISM_CONTEXT_URL` を private repo の `context.md` (例: `examples/context.md`) に向けると、自分の OKR を背景情報として LLM に渡せる (未設定なら通常の 3 視点解析として動作)

---

## アーキテクチャ

```
[iPhone Safari / Web UI]
        │  POST /api/analyze (URL)
        ▼
[CloudFront]  ──/api/* ──> [API Gateway (API Key, Usage Plan)]
       │                          │
       │ default behavior         ▼
       ▼                       [Lambda (Python 3.12)]
[S3 (Web UI static)]             │
                                 ├──> [Secrets Manager] (GitHub PAT for context.md)
                                 ├──> [GitHub raw] (context.md fetch)
                                 ├──> [Bedrock] × 4 並列 (Summary + 3 persona)
                                 └──> [DynamoDB] (analyses, PK=ulid + GSI by user_id+created_at)
```

主要コンポーネント:

| Component | 役割 | 実装 |
|---|---|---|
| Lambda | 記事 fetch → context fetch → Bedrock 4 並列 → DynamoDB | `src/news_prism/` |
| 4 並列 persona call | Summary + 3 視点 (定義は `prompts.py`)、`cachePoint` で prompt prefix を cache | `bedrock_client.py` |
| API Gateway (REST) | API Key + Usage Plan throttling | `infra/api_gateway.tf` |
| Web UI | 素 HTML/JS、PWA 対応、dark/light mode 自動 | `src/web/index.html` |
| CloudFront | API Key 注入 (Origin Custom Header)、CF Function でパス書換 (`/api/*` → `/v1/*`) | `infra/web.tf` |
| Terraform | 全インフラを IaC、状態 local | `infra/` |

詳細な設計判断 (DECISION 一覧) と数値根拠は本記事 (記事 URL は後日追加) を参照。

---

## 動かす

### 前提

- AWS アカウント (Bedrock JP CRIS が有効、APAC inference profile 使用可)
- macOS / Linux + Terraform 1.6+ + uv ([astral-sh/uv](https://github.com/astral-sh/uv)) + AWS CLI v2
- (任意) `context.md` を置く private GitHub repo + fine-grained PAT (Contents: Read)

### ① ローカル CLI で試す

```bash
# 依存を入れる
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# .env を作る (.env.example を参照)
cp .env.example .env
# BEDROCK_MODEL_ID, NEWS_PRISM_CONTEXT_URL, NEWS_PRISM_GH_PAT を埋める

# 1 URL を解析
python -m news_prism.poc https://aws.amazon.com/jp/blogs/news/
```

### ② AWS にデプロイ

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # bedrock_model_id (必須) / context_url (任意) 等を埋める

./build.sh                    # Lambda 用 zip を生成 (uv pip でクロスプラットフォーム build)
terraform init
terraform apply -target=aws_acm_certificate.web   # 1 段階目: ACM cert だけ
terraform output acm_validation_records           # → 自分の Route53 zone に CNAME 手動投入

terraform apply               # 2 段階目: validation 待ち合わせ + 残り全部
terraform output cloudfront_domain_name           # → ALIAS A レコードを zone に手動投入

# GitHub PAT を Secrets Manager に投入 (Terraform は値を持たない)
aws secretsmanager put-secret-value \
  --secret-id news-prism/github-pat \
  --secret-string "github_pat_xxxxxxxx"
```

詳細手順は `infra/README.md` 参照。

### ③ 動作確認

```bash
URL=$(terraform output -raw invoke_url)
KEY=$(terraform output -raw api_key_value)

curl -sS -X POST "$URL" \
  -H "x-api-key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://aws.amazon.com/jp/blogs/news/"}'

# Web UI
open "https://$(terraform output -raw web_domain || echo news-prism.example.com)/"
```

### ④ 数値の再現 (Champion 記事の主張を自分で測りたい場合)

```bash
export INVOKE_URL=$(terraform output -raw invoke_url)
export API_KEY=$(terraform output -raw api_key_value)
python3 scripts/measure_burst.py > burst.json
# stderr に per-call 表 + サマリ、stdout に詳細 JSON
```

8 記事連投で cache hit / wall_time / コスト削減率を実測する。

---

## 設定

### 主要環境変数

| 変数 | 説明 | デフォルト |
|---|---|---|
| `BEDROCK_MODEL_ID` | Bedrock の inference profile / model ID | 必須 |
| `AWS_REGION` | AWS リージョン | `ap-northeast-1` |
| `NEWS_PRISM_CONTEXT_URL` | OKR/context を fetch する URL | 未設定 (= context なしで動作) |
| `NEWS_PRISM_GH_PAT` | 上記 URL を読む PAT (local CLI 用) | 未設定 (= public URL なら不要) |
| `NEWS_PRISM_GH_PAT_SECRET_ARN` | PAT を格納した Secrets Manager ARN (Lambda 用) | 未設定 |
| `ANALYSES_TABLE_NAME` | DynamoDB テーブル名 | `news-prism-analyses` |
| `LOG_LEVEL` | Lambda logging level | `INFO` |

### 個人化機能 (`context.md` 連携)

`NEWS_PRISM_CONTEXT_URL` を設定すると、自分の OKR / 業務領域を背景情報として LLM に渡せる (4 視点目「あなたへの示唆」が加わる)。未設定なら通常の 3 視点解析として動く。

#### 1. `context.md` を書く

`examples/context.md` を mock スキーマとして参照。自分の OKR (Objective / Key Result) や業務領域、関心テーマを書く。フォーマット自体は素の Markdown なので柔軟に拡張可。

#### 2. private repo に置く + PAT を作る

OKR は通常 private 情報なので、別の private GitHub repo (例: `my-goals`) に `context.md` として置く。fetch には fine-grained PAT が必要:

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate
2. **Repository access**: Only select repositories → `my-goals` を選択
3. **Permissions**: Repository permissions → **Contents: Read-only** のみ付与
4. 生成された `github_pat_...` を `NEWS_PRISM_GH_PAT` (local CLI) または Secrets Manager (Lambda) に登録

#### 3. Raw URL の取得

GitHub UI で `context.md` を開く → 右上の **Raw** ボタンクリック → アドレスバーの URL をコピー。形式は:

```
https://raw.githubusercontent.com/<account>/<private-repo>/refs/heads/main/context.md
```

(branch が `main` 以外なら `refs/heads/<branch>`)

この URL を `NEWS_PRISM_CONTEXT_URL` に設定する。

#### 4. (補足) 企業 MITM プロキシ環境

企業ネットワークの SSL inspection (MITM プロキシ) 配下では Python の `urllib` が verify に失敗することがある。`.env` で `SSL_CERT_FILE` に **certifi roots + 社内 CA を merge したバンドル** を指定すると通る:

```bash
SSL_CERT_FILE=/path/to/ca-bundle.pem
```

社内 CA だけ / certifi だけだとサーバが返す cert chain と噛み合わず fail するケースがある (`unable to get local issuer certificate` や `self-signed certificate in certificate chain`)。両方含む bundle を作るのが確実。

### 3 視点の定義を変える

`src/news_prism/prompts.py` の `_PERSONA_SYSTEMS` を書き換える。persona 名 (キー) と persona 数を変えるなら `PERSONAS` 定数も更新する。

---

## 主な設計選択 (要点だけ)

- **Lambda packaging**: zip + `pip install --platform manylinux2014_x86_64` (Docker 不要)
- **Lambda zip サイズ**: 約 18MB (Bedrock 用 boto3 は Lambda runtime 同梱を流用)
- **cache 配置**: persona ごとに独立 `cachePoint`、`<context>` の後 / `<article>` の前。4 並列で各 ~2,850 tokens の cache を持つ
- **Bedrock model**: Sonnet 4.6 APAC CRIS (`jp.anthropic.claude-sonnet-4-6`)、temperature は persona 別に調整 (Blogger 0.9 / 他 0.3-0.6)
- **DynamoDB**: PK = `analysis_id` (ULID), GSI1 = `user_id` + `created_at` (時系列一覧用)、On-demand
- **API Key**: CloudFront → API Gateway の **Origin Custom Header で注入**、ブラウザ JS には露出しない
- **Terraform state**: local (個人開発、共同編集する時に S3 backend に移行)

---

## ライセンス

[MIT](./LICENSE)

---

## Author

Takayuki Tadokoro
