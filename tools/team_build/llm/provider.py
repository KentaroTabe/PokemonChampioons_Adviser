"""LLMProvider — 構築システムの LLM 呼び出し (docs/TEAM_BUILDING_IMPLEMENTATION.md §2.1, §10)。

規約:
- 入力は JSON 要約のみ (画像なし)。出力は {"authoritative": {...}, "display": {...}} の JSON
- 全入出力を run の llm/ に保存 (provider / model / prompt hash / schema version / sampling / raw output / トークン数)
- authoritative は呼び出し側の検証器で検査し、不合格は理由つきで再試行 (最大 BUILD_LLM_MAX_RETRIES)
- Provider を変えたら固定 BuildSpec セットで candidate-generation regression test (指標: concept diversity /
  valid candidate rate / downstream WR) — tools/team_build/llm/regression.py (後続)

Provider:
- ClaudeCLIProvider: `claude -p <prompt> --model <id> --output-format json --system-prompt <file> --json-schema <schema>`
  (tools/audit_subtask と同じヘッドレス方式)。ツール不使用 (--allowedTools 空)。
  2026-09-24: --json-schema (構造化出力) で「schema に合う JSON オブジェクトが返る」ことを CLI 側で保証し、応答の
  structured_output を使う (本文の JSON 抽出 extract_json は structured_output が無いときの後備え)。
  既定 schema は OUTPUT_SCHEMA (authoritative 必須の緩い形)、段ごとに call(schema=...) で差し替えられる
- MockProvider: テストと dry-run 用。あらかじめ与えた応答を返す (schema を渡すと、応答が JSON なら structured として返す)
- チャット内 (Agent ツール) で動かすときは、主セッションが同じ prompt ファイルを Agent(model=…) に渡し、
  応答 JSON を record() で同じ形式で保存する (AgentToolProvider は主セッション側の手順で代替)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import (BUILD_LLM_CLI_TOOLS, BUILD_LLM_EFFORT, BUILD_LLM_MAX_BUDGET_USD,
                                    BUILD_LLM_MODELS)

REPO = Path(__file__).resolve().parent.parent.parent.parent
MODELS = dict(BUILD_LLM_MODELS)      # tier → モデル id (config)。provider ごとに models=... で上書きできる
MAX_RETRIES = 2
DEFAULT_TIMEOUT = 600
# 構造化出力の既定 schema (claude CLI の --json-schema)。authoritative 必須・display 任意の緩い形から始め、
# 段ごとの条件 (id / enum) は call(schema=...) で順次 schema へ移す。schema が不正だと CLI が失敗するので
# テスト (test_team_build_concepts) で妥当性を固定する
OUTPUT_SCHEMA = {"type": "object",
                 "properties": {"authoritative": {"type": "object"}, "display": {"type": "object"}},
                 "required": ["authoritative"]}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def schema_hash(schema: Optional[dict]) -> Optional[str]:
    return _hash(json.dumps(schema, sort_keys=True, ensure_ascii=False)) if schema else None


def extract_json(text: str) -> Optional[dict]:
    """応答本文から最初の JSON オブジェクトを取り出す (前後の説明文や ```json フェンスを許容)。
    2026-09-22: 以前は括弧の深さを数えて切り出していたため、文字列の中に `}` があると途中で切れ、その内側の小さな
    オブジェクト ({"ok": true}) だけを拾って本文 (display.markdown) を捨てていた (arch_0918 の記事)。
    json の raw_decode で「その位置から読める最初のオブジェクト」を取る (文字列内の括弧を正しく扱う)。
    strict=False: モデルは長い本文を文字列に入れるとき生の改行を書くことがある (厳密モードでは制御文字として不正)"""
    if not text:
        return None
    t = text.strip()
    dec = json.JSONDecoder(strict=False)
    start = t.find("{")
    while start != -1:
        try:
            obj, _end = dec.raw_decode(t, start)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = t.find("{", start + 1)
    return None


def normalize_output(parsed) -> tuple:
    """{"authoritative": {...}, "display": {...}} に揃える。モデルが authoritative を省いて最上位に値を置いたら、
    display 以外を authoritative とみなす (寛容な正規化。検証は authoritative の中身に対して行う)"""
    if not isinstance(parsed, dict):
        return None, None
    auth = parsed.get("authoritative")
    disp = parsed.get("display")
    if not isinstance(auth, dict) or not auth:
        rest = {k: v for k, v in parsed.items() if k not in ("authoritative", "display")}
        auth = rest if rest else (auth if isinstance(auth, dict) else None)
    if disp is not None and not isinstance(disp, dict):
        disp = {"text": disp}
    return auth, disp


class LLMProvider:
    name = "base"
    default_schema: Optional[dict] = None     # 構造化出力の既定 schema (None = 本文の JSON 抽出だけ)

    def __init__(self, log_dir: Optional[Path] = None, models: Optional[dict] = None,
                 effort: Optional[dict] = None):
        self.log_dir = Path(log_dir) if log_dir else None
        self.calls: list = []
        self.models = dict(MODELS, **(models or {}))          # tier → モデル id (config + 上書き)
        self.effort = dict(BUILD_LLM_EFFORT, **(effort or {}))  # stage → effort (None = CLI の既定)

    def complete(self, model: str, system: str, prompt: str, timeout: int = DEFAULT_TIMEOUT,
                 schema: Optional[dict] = None, effort: Optional[str] = None) -> dict:
        """{"text": str, "raw": any, "usage": {...}, "structured": dict|None, "cost_usd": float|None} を返す
        (サブクラスで実装)。structured は schema に合うことが保証された出力 (無ければ None)"""
        raise NotImplementedError

    def call(self, stage: str, tier: str, system: str, payload: dict,
             validator: Optional[Callable[[dict], list]] = None,
             max_retries: int = MAX_RETRIES, timeout: int = DEFAULT_TIMEOUT,
             schema: Optional[dict] = None, effort: Optional[str] = None) -> dict:
        """1 段の呼び出し。validator が問題リストを返したら理由を添えて再試行。
        schema: 構造化出力の JSON Schema (None なら provider の既定 default_schema)。
        effort: この呼び出しの effort (None なら stage の設定 BUILD_LLM_EFFORT、それも None なら CLI の既定)。
        戻り値: {"ok": bool, "authoritative": dict, "display": dict, "problems": [...], "attempts": n, "record": path}"""
        model = self.models.get(tier, tier)
        schema = self.default_schema if schema is None else schema
        effort = effort if effort is not None else self.effort.get(stage)
        prompt = json.dumps(payload, ensure_ascii=False, indent=1)
        problems: list = []
        result = {"ok": False, "authoritative": {}, "display": {}, "problems": [], "attempts": 0}
        for attempt in range(1, max_retries + 2):
            p = prompt if not problems else (prompt + "\n\n前回の出力の問題 (直して再出力):\n- " + "\n- ".join(problems))
            t0 = time.time()
            try:
                res = self.complete(model, system, p, timeout=timeout, schema=schema, effort=effort)
            except Exception as e:
                res = {"text": "", "raw": None, "usage": {}, "error": repr(e)}
            structured = res.get("structured")
            # 構造化出力があればそれを使う (schema 準拠が保証されている)。無ければ本文から JSON を取り出す
            parsed = structured if isinstance(structured, dict) else extract_json(res.get("text") or "")
            auth, disp = normalize_output(parsed)
            problems = []
            if parsed is None:
                problems.append("出力に JSON オブジェクトが見つからない")
            elif not isinstance(auth, dict):
                problems.append("authoritative フィールドが無い (id/enum/構造化値の JSON オブジェクトにする)")
            elif validator is not None:
                problems = list(validator(auth) or [])
            rec = {"stage": stage, "tier": tier, "model": model, "provider": self.name, "attempt": attempt,
                   "prompt_hash": _hash(system + "\n" + p), "system_hash": _hash(system),
                   "schema_hash": schema_hash(schema), "structured": isinstance(structured, dict), "effort": effort,
                   "elapsed_s": round(time.time() - t0, 1), "usage": res.get("usage"), "cost_usd": res.get("cost_usd"),
                   "error": res.get("error"), "problems": problems, "prompt": p, "system": system,
                   "raw_text": res.get("text")}
            path = self._record(stage, tier, attempt, rec)
            result.update({"attempts": attempt, "record": str(path) if path else None,
                           "model": model, "provider": self.name})
            if not problems:
                result.update({"ok": True, "authoritative": auth, "display": disp or {}})
                return result
            result["problems"] = problems
        return result

    def _record(self, stage: str, tier: str, attempt: int, rec: dict) -> Optional[Path]:
        self.calls.append(rec)
        if self.log_dir is None:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # 同一秒内の複数呼び出しで上書きしないよう連番を付ける。別のプロセス (run 後の記事の再生成) が同じ番号を
        # 使っていても既存の記録を上書きしない (2026-09-19: arch_0918 の run 中の記事の記録が再生成で消えた)
        k = len(self.calls)
        path = self.log_dir / f"{stage}_{tier}_{k:03d}_a{attempt}.json"
        while path.exists():
            k += 1
            path = self.log_dir / f"{stage}_{tier}_{k:03d}_a{attempt}.json"
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return path


class MockProvider(LLMProvider):
    """テスト / dry-run: responses は呼び出し順に返す文字列 (JSON テキスト) のリスト"""
    name = "mock"

    def __init__(self, responses: list, log_dir: Optional[Path] = None, models: Optional[dict] = None,
                 effort: Optional[dict] = None):
        super().__init__(log_dir, models=models, effort=effort)
        self.responses = list(responses)
        self.i = 0

    def complete(self, model, system, prompt, timeout=DEFAULT_TIMEOUT, schema=None, effort=None) -> dict:
        text = self.responses[min(self.i, len(self.responses) - 1)] if self.responses else ""
        self.i += 1
        structured = None
        if schema is not None:      # CLI の構造化出力を模す: 応答がそのまま JSON オブジェクトなら structured
            try:
                obj = json.loads(text)
                structured = obj if isinstance(obj, dict) else None
            except ValueError:
                structured = None
        return {"text": text, "raw": None, "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4},
                "structured": structured, "cost_usd": None}


# claude CLI は既定でツール定義を system prompt に載せる (実測: 約 29k トークン/呼び出し、キャッシュ読み)。
# 全ツールを disallow すると約 17k〜25k に減り、--tools "" (ツール定義を載せない) で 12.8k (2026-09-24 haiku 実測)。
# --bare は OAuth が外れて使えない。構造化出力 (--json-schema) は --tools "" でも動く


class ClaudeCLIProvider(LLMProvider):
    """claude CLI のヘッドレス実行 (tools/audit_subtask と同方式)。ツールは使わせない (定義も載せない: --tools "")。
    schema を渡すと --json-schema で構造化出力にし、応答 JSON の structured_output を返す
    (2026-09-24 実測: --max-turns 1・ツール無しで structured_output が返る。内部で tool_use を 1 回使う)。
    effort (段ごと、config BUILD_LLM_EFFORT) は --effort、費用上限は --max-budget-usd (config BUILD_LLM_MAX_BUDGET_USD)"""
    name = "claude-cli"
    default_schema = OUTPUT_SCHEMA

    def __init__(self, log_dir: Optional[Path] = None, models: Optional[dict] = None, effort: Optional[dict] = None,
                 max_budget_usd: Optional[float] = BUILD_LLM_MAX_BUDGET_USD, tools: str = BUILD_LLM_CLI_TOOLS):
        super().__init__(log_dir, models=models, effort=effort)
        self.max_budget_usd = max_budget_usd
        self.tools = tools

    def command(self, model: str, system: str, prompt: str, schema: Optional[dict] = None,
                effort: Optional[str] = None) -> list:
        cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
               "--system-prompt", system, "--max-turns", "1", "--tools", self.tools]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
        if effort:
            cmd += ["--effort", str(effort)]
        if self.max_budget_usd:
            cmd += ["--max-budget-usd", str(self.max_budget_usd)]
        return cmd

    def complete(self, model, system, prompt, timeout=DEFAULT_TIMEOUT, schema=None, effort=None) -> dict:
        cmd = self.command(model, system, prompt, schema, effort)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(REPO))
        if res.returncode != 0:
            # API エラーは --output-format json だと stdout の JSON に入り stderr が空のことがある (2026-09-24 実測) → 両方を残す
            detail = (res.stderr or "")[-400:] or (res.stdout or "")[-400:]
            raise RuntimeError(f"claude 実行失敗 (rc={res.returncode}): {detail}")
        raw = None
        text = res.stdout
        structured = None
        cost = None
        try:
            raw = json.loads(res.stdout)
            if isinstance(raw, dict):
                text = raw.get("result") or raw.get("content") or res.stdout
                so = raw.get("structured_output")
                structured = so if isinstance(so, dict) else None
                cost = raw.get("total_cost_usd")
        except Exception:
            pass
        usage = (raw or {}).get("usage") if isinstance(raw, dict) else None
        return {"text": text if isinstance(text, str) else json.dumps(text, ensure_ascii=False),
                "raw": raw, "usage": usage, "structured": structured,
                "cost_usd": (float(cost) if isinstance(cost, (int, float)) else None)}


def get_provider(mode: str = "headless", log_dir: Optional[Path] = None, mock_responses=None,
                 models: Optional[dict] = None, effort: Optional[dict] = None) -> LLMProvider:
    if mode == "mock":
        return MockProvider(mock_responses or [], log_dir, models=models, effort=effort)
    return ClaudeCLIProvider(log_dir, models=models, effort=effort)
