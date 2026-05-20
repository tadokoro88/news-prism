# News Prism — context.md schema (mock)

`NEWS_PRISM_CONTEXT_URL` の指すファイルとして News Prism Lambda / CLI が fetch する。
個人 OKR や業務領域を「LLM への背景情報」として渡し、3 視点解析時に `relevant_okr_refs`
や `action_items.linked_okr_refs` に紐付ける材料にする。

このファイル自体は **mock の schema 例**。実利用では自分の private repo に置く想定。

---

## 業務 OKR

### F1: Bedrock 系プロジェクト推進
- **F1.KR1**: Bedrock を使った社内アプリ 2 件を本番リリース (期限: Q3)
- **F1.KR2**: Bedrock Guardrails の社内ベースライン草案を策定
- **F1.KR3**: AgentCore Evaluations の社内導入ガイド作成

### F2: セキュリティ標準整備
- **F2.KR1**: クラウドセキュリティベースライン v3 策定
- **F2.KR2**: 脆弱性周知プロセスの SLA 短縮 (現 7 日 → 3 日)
- **F2.KR3**: サプライチェーンセキュリティの SBOM 運用整備

---

## 個人キャリア OKR

### C1: 技術発信
- **C1.KR1**: Qiita 記事 12 本公開 (年内)
- **C1.KR2**: Champion / Hero / Ambassador 系プログラムへの応募

### C2: 専門領域の深化
- **C2.KR1**: Bedrock 周辺機能を網羅的に検証 (Guardrails / AgentCore / Knowledge Bases)
- **C2.KR2**: Security 系 AWS サービスとの連携実装 (Security Agent / GuardDuty / Inspector / Config)

---

## 短期フォーカス (今四半期)

- AI セキュリティと Bedrock 運用の交差領域に集中
- 個人ツール (News Prism) を実験ハブとして活用
- 検証 → 記事化 → 発信のサイクルを回す

---

## 使い方

1. このファイルを自分の **private** GitHub repo に置く (例: `your-account/my-goals`)
2. fine-grained PAT を作る (Contents: Read のみ、対象 repo 限定、年 1 rotation 推奨)
3. Lambda / local env に以下をセット:
   ```
   NEWS_PRISM_CONTEXT_URL=https://raw.githubusercontent.com/<account>/<repo>/main/context.md
   NEWS_PRISM_GH_PAT=github_pat_xxxxxxxxxx   # Lambda は Secrets Manager 経由が安全
   ```
4. KR ID 形式は自由 (`F1.KR1` でも `OKR-2026Q3-001` でも、prompt から解釈される)
