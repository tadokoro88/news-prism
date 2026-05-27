# Bedrock Guardrails (Phase 8.1、SPEC §15、DECISION-0024)
#
# 公開 / 認証なし構成における indirect prompt injection と第三者 PII の二次配信を
# 構造的に防ぐ。Summary には Contextual Grounding を加える。
#
# - content filter (Hate / Violence / Sexual / Misconduct / Insults) は全 NONE
#   (報道との相性問題、誤検知が UX を破壊するため明示的に切る)
# - PROMPT_ATTACK は input HIGH のみ (output に prompt attack は出ない設計)
# - PII は format 明確 type だけ ANONYMIZE。NAME / ADDRESS は OFF
#   (報道記事の著名人名・地名が伏字化されると UX 破壊)
# - DRAFT version で運用、必要になったら aws_bedrock_guardrail_version を起こす

locals {
  guardrail_blocked_input   = "(news-prism) guardrail がこの入力をブロックしました。"
  guardrail_blocked_outputs = "(news-prism) guardrail がこの出力をブロックしました。"

  # 5 種 content filter は全部 NONE で固定 (SPEC §15.1)。
  # for_each で重複を避ける。
  guardrail_content_off_types = [
    "SEXUAL",
    "VIOLENCE",
    "HATE",
    "INSULTS",
    "MISCONDUCT",
  ]

  # ANONYMIZE 対象 PII type (SPEC §15.2)。NAME / ADDRESS / USERNAME は意図的に外す。
  # 名称は Bedrock の正式 enum に合わせる (SPEC の "US_SSN" は略記)。
  guardrail_pii_anonymize_types = [
    "EMAIL",
    "PHONE",
    "IP_ADDRESS",
    "CREDIT_DEBIT_CARD_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
  ]
}

# ---- Default profile (scoe / supply_chain_security / blogger) ----

resource "aws_bedrock_guardrail" "default" {
  name                      = "${var.project_name}-default"
  description               = "News Prism default guardrail: Prompt Attack (article scope) + PII Anonymize"
  blocked_input_messaging   = local.guardrail_blocked_input
  blocked_outputs_messaging = local.guardrail_blocked_outputs

  content_policy_config {
    # 5 種 NONE (明示)
    dynamic "filters_config" {
      for_each = local.guardrail_content_off_types
      content {
        type            = filters_config.value
        input_strength  = "NONE"
        output_strength = "NONE"
      }
    }

    # Prompt Attack は input HIGH。output_strength は仕様上 NONE 固定。
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  sensitive_information_policy_config {
    # 注: top-level `action` だけ書くと AWS 暗黙 default で OUTPUT only になり、
    # INPUT 側 (記事本文に含まれる PII) は scan されない (Phase 8.1 で実測、DECISION-0025)。
    # API は input_action / input_enabled / output_action / output_enabled をサポートするが、
    # Terraform AWS provider v5.x の pii_entities_config はこれら attribute を持たない。
    # Phase 8.2 で provider を v6.22+ にアップグレードした上で input_action 等を IaC で
    # 正式記述する。それまでは OUTPUT only に乗ったままで運用 (= tool_use 経路で素通り
    # するため実効ゼロ、これも Article 1 の記事ネタ)。
    dynamic "pii_entities_config" {
      for_each = local.guardrail_pii_anonymize_types
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }
}

# ---- Grounding profile (summary only) ----
#
# default の (a)+(b) に加えて (c) Contextual Grounding を載せる。
# grounding source = <article>, response = Summary 出力 (SPEC §15.1)。
# threshold は HIGH 寄りで開始、誤検知が多ければ Phase 8.2 でチューニング。

resource "aws_bedrock_guardrail" "grounding" {
  name                      = "${var.project_name}-grounding"
  description               = "News Prism grounding guardrail: default + Contextual Grounding for Summary"
  blocked_input_messaging   = local.guardrail_blocked_input
  blocked_outputs_messaging = local.guardrail_blocked_outputs

  content_policy_config {
    dynamic "filters_config" {
      for_each = local.guardrail_content_off_types
      content {
        type            = filters_config.value
        input_strength  = "NONE"
        output_strength = "NONE"
      }
    }

    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  sensitive_information_policy_config {
    # 注: top-level `action` だけ書くと AWS 暗黙 default で OUTPUT only になり、
    # INPUT 側 (記事本文に含まれる PII) は scan されない (Phase 8.1 で実測、DECISION-0025)。
    # API は input_action / input_enabled / output_action / output_enabled をサポートするが、
    # Terraform AWS provider v5.x の pii_entities_config はこれら attribute を持たない。
    # Phase 8.2 で provider を v6.22+ にアップグレードした上で input_action 等を IaC で
    # 正式記述する。それまでは OUTPUT only に乗ったままで運用 (= tool_use 経路で素通り
    # するため実効ゼロ、これも Article 1 の記事ネタ)。
    dynamic "pii_entities_config" {
      for_each = local.guardrail_pii_anonymize_types
      content {
        type   = pii_entities_config.value
        action = "ANONYMIZE"
      }
    }
  }

  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = var.guardrail_grounding_threshold
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = var.guardrail_relevance_threshold
    }
  }
}
