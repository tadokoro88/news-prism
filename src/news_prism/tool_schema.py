"""Bedrock Converse API tool_use 用の tool spec 定義。

DECISION-0011 により Summary call + 3 視点 call の 4 並列構成、tool は 2 種:
- `return_summary`: Summary persona 専用
- `return_perspective`: SCoE / Supply Chain / Blogger 共通
"""

from __future__ import annotations

from typing import Any

KR_ID_PATTERN = r"^[SP]\d+\.KR\d+$"


RETURN_SUMMARY_TOOL: dict[str, Any] = {
    "toolSpec": {
        "name": "return_summary",
        "description": "ニュース記事の中立的・事実ベースの要約を返す。",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "要約方針の簡潔なメモ (scratchpad、~100 字)。"
                            "summary 内容と重複させないこと。"
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "記事の要約。約 500 文字、中立的・事実ベース。"
                            "解釈や評価は含めない。"
                        ),
                    },
                },
                "required": ["reasoning", "summary"],
            }
        },
    }
}


RETURN_PERSPECTIVE_TOOL: dict[str, Any] = {
    "toolSpec": {
        "name": "return_perspective",
        "description": "特定 persona の視点から記事を分析し、構造化された結果を返す。",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "視点を出す前の思考プロセス scratchpad (~100 字)。"
                            "perspective / action と内容を重複させないこと。"
                        ),
                    },
                    "perspective": {
                        "type": "string",
                        "description": (
                            "当該 persona の語り (~250 字、要点重視、3-4 文に集約、深掘りパラグラフは避ける)。"
                            "記事が当該視点と無関係な場合は『特になし: <理由>』と明示し、"
                            "無理に解釈を絞り出さない。"
                        ),
                    },
                    "relevant_okr_refs": {
                        "type": "array",
                        "description": (
                            "<context> 内に明示された KR ID のうち、記事と直接関係するもののみ (strict)。"
                            "該当なしは空配列。"
                        ),
                        "items": {"type": "string", "pattern": KR_ID_PATTERN},
                    },
                    "action_item": {
                        "type": "object",
                        "description": "actionable な提案 1 件。該当なしなら省略可。",
                        "properties": {
                            "action": {
                                "type": "string",
                                "description": "actionable な行動の記述 (~100 字)。",
                            },
                            "linked_okr_refs": {
                                "type": "array",
                                "items": {"type": "string", "pattern": KR_ID_PATTERN},
                            },
                        },
                        "required": ["action", "linked_okr_refs"],
                    },
                },
                "required": ["reasoning", "perspective", "relevant_okr_refs"],
            }
        },
    }
}


def get_persona_tool(persona: str) -> dict[str, Any]:
    """指定 persona に対応する tool spec を返す。"""
    if persona == "summary":
        return RETURN_SUMMARY_TOOL
    if persona in ("scoe", "supply_chain_security", "blogger"):
        return RETURN_PERSPECTIVE_TOOL
    raise ValueError(f"unknown persona: {persona}")


def get_persona_tool_name(persona: str) -> str:
    if persona == "summary":
        return "return_summary"
    if persona in ("scoe", "supply_chain_security", "blogger"):
        return "return_perspective"
    raise ValueError(f"unknown persona: {persona}")
