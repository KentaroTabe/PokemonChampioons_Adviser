"""LLMProvider — 構築システムの LLM 呼び出し (docs/TEAM_BUILDING_IMPLEMENTATION.md §2.1, §10)。

規約:
- 入力は JSON 要約のみ (画像なし)。出力は {"authoritative": {...}, "display": {...}} の JSON
- 全入出力を run の llm/ に保存 (provider / model / prompt hash / schema version / sampling / raw output / トークン数)
- authoritative は呼び出し側の検証器で検査し、不合格は理由つきで再試行 (最大 BUILD_LLM_MAX_RETRIES)
- Provider を変えたら固定 BuildSpec セットで candidate-generation regression test (指標: concept diversity /
  valid candidate rate / downstream WR) — tools/team_build/llm/regression.py (後続)

Provider:
- ClaudeCLIProvider: `claude -p <prompt> --model <id> --output-format json --system-prompt <file>`
  (tools/audit_subtask と同じヘッドレス方式)。ツール不使用 (--allowedTools 空)
- MockProvider: テストと dry-run 用。あらかじめ与えた応答を返す
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

REPO = Path(__file__).resolve().parent.parent.parent.parent
MODELS = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5-20251001"}
MAX_RETRIES = 2
DEFAULT_TIMEOUT = 600


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def extract_json(text: str) -> Optional[dict]:
    """応答本文から最初の JSON オブジェクトを取り出す (前後の説明文や ```json フェンスを許容)"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("{"):
        try:
            return json.loads(t)
        except Exception:
            pass
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
        start = t.find("{", start + 1)
    return None


class LLMProvider:
    name = "base"

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = Path(log_dir) if log_dir else None
        self.calls: list = []

    def complete(self, model: str, system: str, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
        """{"text": str, "raw": any, "usage": {...}} を返す (サブクラスで実装)"""
        raise NotImplementedError

    def call(self, stage: str, tier: str, system: str, payload: dict,
             validator: Optional[Callable[[dict], list]] = None,
             max_retries: int = MAX_RETRIES, timeout: int = DEFAULT_TIMEOUT) -> dict:
        """1 段の呼び出し。validator が問題リストを返したら理由を添えて再試行。
        戻り値: {"ok": bool, "authoritative": dict, "display": dict, "problems": [...], "attempts": n, "record": path}"""
        model = MODELS.get(tier, tier)
        prompt = json.dumps(payload, ensure_ascii=False, indent=1)
        problems: list = []
        result = {"ok": False, "authoritative": {}, "display": {}, "problems": [], "attempts": 0}
        for attempt in range(1, max_retries + 2):
            p = prompt if not problems else (prompt + "\n\n前回の出力の問題 (直して再出力):\n- " + "\n- ".join(problems))
            t0 = time.time()
            try:
                res = self.complete(model, system, p, timeout=timeout)
            except Exception as e:
                res = {"text": "", "raw": None, "usage": {}, "error": repr(e)}
            parsed = extract_json(res.get("text") or "")
            auth = (parsed or {}).get("authoritative") if isinstance(parsed, dict) else None
            disp = (parsed or {}).get("display") if isinstance(parsed, dict) else None
            problems = []
            if parsed is None:
                problems.append("出力に JSON オブジェクトが見つからない")
            elif not isinstance(auth, dict):
                problems.append("authoritative フィールドが無い (id/enum/構造化値の JSON オブジェクトにする)")
            elif validator is not None:
                problems = list(validator(auth) or [])
            rec = {"stage": stage, "tier": tier, "model": model, "provider": self.name, "attempt": attempt,
                   "prompt_hash": _hash(system + "\n" + p), "system_hash": _hash(system),
                   "elapsed_s": round(time.time() - t0, 1), "usage": res.get("usage"),
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
        # 同一秒内の複数呼び出しで上書きしないよう連番を付ける
        path = self.log_dir / f"{stage}_{tier}_{len(self.calls):03d}_a{attempt}.json"
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return path


class MockProvider(LLMProvider):
    """テスト / dry-run: responses は呼び出し順に返す文字列 (JSON テキスト) のリスト"""
    name = "mock"

    def __init__(self, responses: list, log_dir: Optional[Path] = None):
        super().__init__(log_dir)
        self.responses = list(responses)
        self.i = 0

    def complete(self, model, system, prompt, timeout=DEFAULT_TIMEOUT) -> dict:
        text = self.responses[min(self.i, len(self.responses) - 1)] if self.responses else ""
        self.i += 1
        return {"text": text, "raw": None, "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4}}


class ClaudeCLIProvider(LLMProvider):
    """claude CLI のヘッドレス実行 (tools/audit_subtask と同方式)。ツールは使わせない"""
    name = "claude-cli"

    def complete(self, model, system, prompt, timeout=DEFAULT_TIMEOUT) -> dict:
        cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
               "--system-prompt", system, "--max-turns", "1"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(REPO))
        if res.returncode != 0:
            raise RuntimeError(f"claude 実行失敗 (rc={res.returncode}): {res.stderr[-400:]}")
        raw = None
        text = res.stdout
        try:
            raw = json.loads(res.stdout)
            if isinstance(raw, dict):
                text = raw.get("result") or raw.get("content") or res.stdout
        except Exception:
            pass
        usage = (raw or {}).get("usage") if isinstance(raw, dict) else None
        return {"text": text if isinstance(text, str) else json.dumps(text, ensure_ascii=False),
                "raw": raw, "usage": usage}


def get_provider(mode: str = "headless", log_dir: Optional[Path] = None, mock_responses=None) -> LLMProvider:
    if mode == "mock":
        return MockProvider(mock_responses or [], log_dir)
    return ClaudeCLIProvider(log_dir)
