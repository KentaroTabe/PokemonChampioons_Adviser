"""接続テストの操作パネル (ターミナルもチャットも使わずに接続テストを回すための常駐ページ)。

ブラウザで http://localhost:<CONTROL_PORT_DEFAULT>/ (config/ports.env、既定 8010) を開き、ボタンで
  - 接続テストの開始 / 終了 (scripts/start_connection_test.sh / end_connection_test.sh [--no-audit])
  - experiment ラベルの ON / OFF (scripts/experiment_label.sh → tools.team_build.promote) と試用中 Package の実戦サマリー
  - 稼働状況の実測 (scripts/status.sh)、更新の反映 (scripts/deploy.sh)
を実行し、出力を同じページで読む。アドバイザー (8000) は接続テスト中しか動かないので、起動役は
アドバイザーとは別の常駐が要る → launchd (com.championsadviser.control-panel、KeepAlive) で常駐させる。

    bash scripts/control_panel_install.sh install    # launchd に登録 (ログイン時に自動起動、落ちても復帰)
    bash scripts/control_panel.sh                    # 前景で起動 (動作確認)
    python -m tools.control_panel [--port N] [--bind ADDR]

設計:
- 実行できるのは ACTIONS に列挙した固定のスクリプトだけ (任意コマンドは受けない)。同時に走るジョブは 1 つ
- 状態は毎回実測する (lsof / pgrep / マーカー・ログ・registry のファイル)。記憶で答えない
- 出力は logs/control_panel/<時刻>__<操作>.log に書き、ページは末尾を定期取得する。パネルを再起動しても直前の出力は読める
- ジョブは別セッション (setsid) で起動するので、start スクリプトが nohup で起こす常駐はパネルの再起動に巻き込まれない
- 依存は標準ライブラリ + 純粋 Python のモジュール (champions_agent.config、tools.analyze_battles、種族名の表引きに
  advisor.ja_names を遅延 import) だけ
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from champions_agent.config import BUILD_SMOKE_CANARY_BATTLES
from tools.analyze_battles import MIN_BATTLE_SCENES, _parse_battle

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ID_RE = re.compile(r"^[a-z_]+-[0-9a-f]{16}$")
FRAME_STAT_RE = re.compile(r"受信=(\d+) 処理=(\d+) 破棄=(\d+)")
POST_HEADER = "X-Control-Panel"        # fetch だけが付けられるヘッダ。別サイトのフォーム送信からの実行を弾く
LOG_NAME_SEP = "__"                    # logs/control_panel/<時刻><SEP><操作>.log
ADVISOR_PATTERN = "uvicorn server:app_asgi"      # scripts/lib/ports.sh と同じ「自分たち」の判定
FRONTEND_PATTERN = "http.server"
SHOWDOWN_PATTERN = "pokemon-showdown"
TRAINING_PATTERN = "train_forever"
MEASURING_PATTERN = "tools.team_build.run|check_advisor_player"   # 構築の測定。接続テストとの同時実行は不可 (第14回)
EXPERIMENT_STATUSES = ("canary", "validation", "candidate")        # 試用の対象にできる registry の status (この順で表示)


class JobBusy(RuntimeError):
    """既に別のジョブが走っている"""


@dataclass(frozen=True)
class Action:
    label: str
    argv: tuple                 # 固定の引数列 (リポジトリルートで実行)
    needs_package: bool = False  # 末尾に Package id を付ける
    confirm: str = ""           # ボタンを押したときの確認文 (空なら確認なし)


ACTIONS = {
    "start": Action("接続テスト開始", ("bash", "scripts/start_connection_test.sh")),
    "end": Action("接続テスト終了 (一括監査あり)", ("bash", "scripts/end_connection_test.sh"),
                  confirm="終了処理を実行します: アドバイザー停止 → 集計 → 決定監査 → 相手バンク更新 → 改善案の測定起動 → "
                          "sonnet の一括監査 (API 課金、数分)。よろしいですか?"),
    "end_no_audit": Action("接続テスト終了 (監査なし)", ("bash", "scripts/end_connection_test.sh", "--no-audit"),
                           confirm="終了処理を実行します: アドバイザー停止 → 集計 → 決定監査 → 相手バンク更新 → 改善案の測定起動 "
                                   "(sonnet の一括監査は省略)。よろしいですか?"),
    "status": Action("稼働状況の詳細", ("bash", "scripts/status.sh")),
    "deploy": Action("更新の反映 (deploy)", ("bash", "scripts/deploy.sh"),
                     confirm="アドバイザーを再起動して最新コードを反映します (対戦中なら自動で中止、停止中なら起動しない)。よろしいですか?"),
    "experiment_on": Action("experiment ラベル ON", ("bash", "scripts/experiment_label.sh", "on"), needs_package=True),
    "experiment_off": Action("experiment ラベル OFF", ("bash", "scripts/experiment_label.sh", "off")),
    "canary_summary": Action("試用中 Package の実戦サマリー", ("bash", "scripts/canary_summary.sh")),
}


# ------------------------------------------------------------------ 設定
def read_env_file(path: Path) -> dict:
    """KEY=VALUE の設定ファイル (bash が source できる形式) を読む。# 行と空行は無視"""
    out: dict = {}
    path = Path(path)
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass
class Settings:
    port: int
    bind: str
    poll_sec: float
    tail_lines: int
    path_prefix: str
    server_log_tail_bytes: int
    advisor_port: int
    frontend_port: int
    showdown_port: int
    smoke_battles: int = BUILD_SMOKE_CANARY_BATTLES

    @classmethod
    def load(cls, root: Path) -> "Settings":
        ports = read_env_file(Path(root) / "config" / "ports.env")
        cp = read_env_file(Path(root) / "config" / "control_panel.env")
        try:
            return cls(port=int(ports["CONTROL_PORT_DEFAULT"]), bind=cp["CONTROL_BIND"],
                       poll_sec=float(cp["CONTROL_POLL_SEC"]), tail_lines=int(cp["CONTROL_LOG_TAIL_LINES"]),
                       path_prefix=cp.get("CONTROL_PATH_PREFIX", ""),
                       server_log_tail_bytes=int(cp["CONTROL_SERVER_LOG_TAIL_BYTES"]),
                       advisor_port=int(ports["ADVISOR_PORT_DEFAULT"]), frontend_port=int(ports["FRONTEND_PORT_DEFAULT"]),
                       showdown_port=int(ports["SHOWDOWN_PORT_DEFAULT"]))
        except KeyError as e:
            raise SystemExit(f"設定が無い: {e} (config/ports.env / config/control_panel.env)")


# ------------------------------------------------------------------ 実測 (プロセス・ポート)
def _run(argv: list, timeout: float = 10.0) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class Probe:
    """プロセス・ポート・LAN アドレスの実測 (lsof / pgrep / ipconfig)。テストは差し替える"""

    def listener(self, port: int) -> Optional[dict]:
        pids = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).split()
        if not pids:
            return None
        return {"pid": int(pids[0]), "command": _run(["ps", "-o", "command=", "-p", pids[0]]).strip()}

    def pgrep(self, pattern: str) -> list:
        return [ln for ln in _run(["pgrep", "-fl", pattern]).splitlines() if ln.strip()]

    def lan_ip(self) -> str:
        return _run(["ipconfig", "getifaddr", "en0"]).strip() or "localhost"


def service_state(probe: Probe, last_port: int, default_port: int, pattern: str) -> dict:
    """前回使ったポート → 既定ポートの順に「自分たちのコマンドが待ち受けているか」を見る (scripts/lib/ports.sh の choose_port と同じ順)。
    どちらも自分たちでなければ既定ポートの状態 (foreign = 別のプロセス / free = 空き) を返す"""
    for p in dict.fromkeys([last_port, default_port]):
        ln = probe.listener(p)
        if ln and pattern in ln["command"]:
            return {"state": "ours", "port": p, "pid": ln["pid"]}
    ln = probe.listener(default_port)
    return {"state": "foreign" if ln else "free", "port": default_port, "pid": (ln or {}).get("pid")}


# ------------------------------------------------------------------ 実測 (ファイル)
def current_ports(root: Path, s: Settings) -> dict:
    """前回の起動で決めたポート (logs/ports.env)。無ければ既定"""
    last = read_env_file(Path(root) / "logs" / "ports.env")
    return {"advisor": int(last.get("ADVISOR_PORT", s.advisor_port)),
            "frontend": int(last.get("FRONTEND_PORT", s.frontend_port))}


def read_marker(path: Path) -> Optional[float]:
    try:
        return float(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def registry_rows(root: Path) -> dict:
    """logs/registry/index.jsonl を読む (同じ id は最後の行が最新)。tools.team_build.registry と同じ規則"""
    idx = Path(root) / "logs" / "registry" / "index.jsonl"
    rows: dict = {}
    if not idx.exists():
        return rows
    for line in idx.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        rows[r["id"]] = r
    return rows


def species_ja_list(ids: list) -> list:
    """種族 id → 日本語 (表引き: advisor.ja_names。手書きの翻訳はしない)。表が読めなければ id のまま"""
    try:
        from advisor.ja_names import species_ja
        return [species_ja(s) or s for s in ids]
    except Exception:
        return list(ids)


def package_view(r: dict) -> dict:
    meta = r.get("meta") or {}
    hold = meta.get("holdout") or {}
    species = meta.get("species") or []
    return {"id": r["id"], "status": r.get("status"), "run_id": r.get("run_id"), "created": r.get("created"),
            "candidate_id": meta.get("candidate_id"), "species": species, "species_ja": species_ja_list(species),
            "direction_ja": meta.get("direction_ja"),
            "holdout": {"verdict": hold.get("verdict"), "delta": hold.get("delta")},
            "history": r.get("history") or []}


def package_choices(rows: dict) -> list:
    """試用の対象にできる Package (canary → validation → candidate の順、各 status 内は新しい順)"""
    order = {s: i for i, s in enumerate(EXPERIMENT_STATUSES)}
    pk = [r for r in rows.values() if r.get("kind") == "package" and r.get("status") in order]
    pk.sort(key=lambda r: r.get("created") or "", reverse=True)
    pk.sort(key=lambda r: order[r["status"]])
    return [package_view(r) for r in pk]


def battles_since(battle_dir: Path, since_ts: float) -> dict:
    """マーカー以降の対戦ログの件数と勝敗 (断片 = 対戦シーンが MIN_BATTLE_SCENES 未満のログは数えない)"""
    rows = []
    for f in sorted(Path(battle_dir).glob("battle_*.jsonl")):
        try:
            if f.stat().st_mtime < since_ts:
                continue
        except OSError:
            continue
        b = _parse_battle(str(f))
        if b["n_battle_scenes"] >= MIN_BATTLE_SCENES:
            rows.append(b)
    decided = [b for b in rows if b["outcome"] in ("win", "loss")]
    wins = sum(1 for b in decided if b["outcome"] == "win")
    return {"n": len(rows), "decided": len(decided), "wins": wins, "losses": len(decided) - wins,
            "last_file": rows[-1]["file"] if rows else None}


def frame_stats(server_log: Path, tail_bytes: int) -> Optional[dict]:
    """server ログ末尾の最新「受信= 処理= 破棄=」(scripts/show_frame_stats.sh と同じ行)"""
    try:
        with Path(server_log).open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    m = None
    for m in FRAME_STAT_RE.finditer(text):
        pass
    if m is None:
        return None
    recv, proc, drop = (int(x) for x in m.groups())
    return {"received": recv, "processed": proc, "dropped": drop, "drop_rate": (drop / recv if recv else None)}


def tail_text(path: Path, lines: int) -> str:
    try:
        data = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])


# ------------------------------------------------------------------ ジョブ (固定のスクリプトを 1 つずつ)
class JobRunner:
    def __init__(self, root: Path, log_dir: Path, actions: dict, path_prefix: str = ""):
        self.root = Path(root)
        self.log_dir = Path(log_dir)
        self.actions = actions
        self.path_prefix = path_prefix
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._job: Optional[dict] = None

    def argv(self, action_id: str, package_id: Optional[str] = None) -> list:
        act = self.actions.get(action_id)
        if act is None:
            raise ValueError(f"未知の操作: {action_id}")
        argv = list(act.argv)
        if act.needs_package:
            if not package_id or not PACKAGE_ID_RE.match(package_id):
                raise ValueError("Package の id が不正です")
            argv.append(package_id)
        return argv

    def start(self, action_id: str, package_id: Optional[str] = None) -> dict:
        argv = self.argv(action_id, package_id)
        with self._lock:
            self._reap()
            if self._job and self._job["running"]:
                raise JobBusy(self._job["label"])
            self.log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log = self.log_dir / f"{ts}{LOG_NAME_SEP}{action_id}.log"
            env = dict(os.environ)
            if self.path_prefix:
                env["PATH"] = self.path_prefix + ":" + env.get("PATH", "")
            env["PYTHONUNBUFFERED"] = "1"
            with log.open("wb") as f:
                self._proc = subprocess.Popen(argv, cwd=str(self.root), stdout=f, stderr=subprocess.STDOUT,
                                              start_new_session=True, env=env)
            self._job = {"action": action_id, "label": self.actions[action_id].label, "argv": argv,
                         "package_id": package_id, "log": str(log), "started": time.time(),
                         "running": True, "rc": None, "finished": None}
            return dict(self._job)

    def _reap(self) -> None:
        if self._proc is not None and self._job and self._job["running"]:
            rc = self._proc.poll()
            if rc is not None:
                self._job.update(running=False, rc=rc, finished=time.time())
                self._proc = None

    def _latest_log(self) -> Optional[dict]:
        """パネル起動後にまだ何も実行していなければ、前回 (再起動前) の出力を見せる"""
        files = [p for p in self.log_dir.glob(f"*{LOG_NAME_SEP}*.log")] if self.log_dir.exists() else []
        if not files:
            return None
        p = max(files, key=lambda x: x.stat().st_mtime)
        action = p.stem.split(LOG_NAME_SEP, 1)[1]
        act = self.actions.get(action)
        return {"action": action, "label": (act.label if act else action), "argv": None, "package_id": None,
                "log": str(p), "started": p.stat().st_mtime, "running": False, "rc": None, "finished": None,
                "previous": True}

    def snapshot(self, tail_lines: int) -> Optional[dict]:
        with self._lock:
            self._reap()
            job = dict(self._job) if self._job else self._latest_log()
        if job is None:
            return None
        job["tail"] = tail_text(Path(job["log"]), tail_lines)
        return job


# ------------------------------------------------------------------ パネル (状態 + 操作)
class Panel:
    def __init__(self, root: Path, settings: Settings, probe: Optional[Probe] = None, actions: dict = ACTIONS):
        self.root = Path(root)
        self.s = settings
        self.probe = probe or Probe()
        self.actions = actions
        self.runner = JobRunner(self.root, self.root / "logs" / "control_panel", actions, settings.path_prefix)

    def status(self) -> dict:
        root, s, probe = self.root, self.s, self.probe
        ports = current_ports(root, s)
        advisor = service_state(probe, ports["advisor"], s.advisor_port, ADVISOR_PATTERN)
        frontend = service_state(probe, ports["frontend"], s.frontend_port, FRONTEND_PATTERN)
        showdown = service_state(probe, s.showdown_port, s.showdown_port, SHOWDOWN_PATTERN)
        training = bool(probe.pgrep(TRAINING_PATTERN))
        measuring = bool(probe.pgrep(MEASURING_PATTERN))
        marker = read_marker(root / "logs" / ".connection_test_start")
        rows = registry_rows(root)
        mark = root / "logs" / ".experiment_package"
        pid = mark.read_text(encoding="utf-8").strip() if mark.exists() else ""
        experiment = {"package_id": pid or None, "package": (package_view(rows[pid]) if pid in rows else None)}
        warnings = []
        if measuring:
            warnings.append("構築の測定 run が動いています。接続テストと同時に回すと助言が遅れます (第14回で確認)。測定を止めるか終わるまで待ってください")
        if pid and pid not in rows:
            warnings.append(f"experiment ラベルの Package が registry にありません: {pid}")
        elif pid and experiment["package"] and experiment["package"]["status"] != "canary":
            warnings.append(f"experiment ラベルの Package は {experiment['package']['status']} です (canary ではない)")
        if marker is not None and advisor["state"] != "ours":
            warnings.append("接続テストの開始マーカーがあるのにアドバイザーが動いていません (終了処理を実行するか、開始し直してください)")
        ip = probe.lan_ip()
        return {
            "now": time.time(),
            "processes": {"advisor": advisor, "frontend": frontend, "showdown": showdown,
                          "training": training, "measuring": measuring},
            # フロントの URL は自分たちが待ち受けているときだけ (停止中に既定ポートを出すと別のプロセスを指してしまう)
            "urls": {"frontend": (f"http://{ip}:{frontend['port']}" if frontend["state"] == "ours" else None),
                     "frontend_local": (f"http://localhost:{frontend['port']}" if frontend["state"] == "ours" else None)},
            "test": {"active": marker is not None, "started": marker, "target": s.smoke_battles,
                     "battles": (battles_since(root / "logs" / "battles", marker) if marker is not None else None)},
            "frames": frame_stats(root / "logs" / "server_nohup.log", s.server_log_tail_bytes),
            "experiment": experiment,
            "packages": package_choices(rows),
            "job": self.runner.snapshot(s.tail_lines),
            "warnings": warnings,
        }

    def run(self, action_id: str, package_id: Optional[str] = None) -> dict:
        act = self.actions.get(action_id)
        if act is None:
            raise ValueError(f"未知の操作: {action_id}")
        if act.needs_package:
            if not package_id or not PACKAGE_ID_RE.match(package_id):
                raise ValueError("Package の id が不正です")
            if package_id not in registry_rows(self.root):
                raise ValueError(f"registry に無い Package: {package_id}")
        return self.runner.start(action_id, package_id)


# ------------------------------------------------------------------ HTTP
PAGE_HTML = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>操作パネル - Champions Adviser</title>
<style>
 body{font-family:"Hiragino Sans",sans-serif;background:#14142a;color:#eee;margin:0;padding:12px}
 h2{margin:4px 0 10px;color:#9fd;font-size:18px} h3{margin:0 0 8px;color:#9fd;font-size:14px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
 .panel{background:#1e1e3e;border-radius:8px;padding:10px 14px;font-size:13px}
 .row{display:flex;justify-content:space-between;gap:8px;padding:3px 0;border-bottom:1px solid #2a2a4a}
 .row span:first-child{white-space:nowrap;color:#aab}
 .badge{border-radius:4px;padding:0 8px;font-size:12px;white-space:nowrap} .on{background:#284} .off{background:#533} .warn{background:#764}
 button{background:#46f;color:#fff;border:0;padding:8px 14px;border-radius:6px;cursor:pointer;margin:6px 6px 0 0;font-size:13px}
 button.danger{background:#a33} button.sub{background:#356} button:disabled{opacity:.45;cursor:default}
 select{font-size:13px;padding:6px;border-radius:6px;background:#223;color:#eee;border:1px solid #467;max-width:100%;margin-top:6px}
 pre{background:#0d0d1d;border-radius:6px;padding:10px;max-height:460px;overflow:auto;white-space:pre-wrap;font-size:12px;margin:6px 0 0}
 .bar{height:8px;background:#2a2a4a;border-radius:4px;overflow:hidden;margin:6px 0} .bar div{height:100%;background:#4f8}
 a{color:#9cf} .muted{color:#99a;font-size:12px} .warnbox{background:#553;border-left:4px solid #fc6;padding:6px 10px;margin:6px 0;font-size:12px}
</style></head><body>
<h2>Champions Adviser 操作パネル <span id="clock" class="muted"></span></h2>
<div id="warnings"></div>
<div class="grid">
 <div class="panel"><h3>常駐の実測</h3><div id="procs"></div><div class="muted" id="urls"></div>
   <button class="sub" data-action="status">稼働状況の詳細</button>
   <button class="sub" data-action="deploy">更新の反映</button></div>
 <div class="panel"><h3>接続テスト</h3><div id="test"></div>
   <button data-action="start">接続テスト開始</button>
   <button class="danger" data-action="end">終了 (一括監査あり)</button>
   <button class="danger sub" data-action="end_no_audit">終了 (監査なし)</button></div>
 <div class="panel"><h3>試用する候補 (experiment ラベル)</h3><div id="exp"></div>
   <select id="pkg"></select>
   <button data-action="experiment_on">ON</button>
   <button class="sub" data-action="experiment_off">OFF</button>
   <button class="sub" data-action="canary_summary">実戦サマリー</button></div>
</div>
<div class="panel" style="margin-top:12px"><h3>出力 <span id="jobhead" class="muted"></span></h3><pre id="out">(まだ何も実行していません)</pre></div>
<script>
const POLL_MS = __POLL_MS__;
const CONFIRM = __CONFIRM__;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const hhmm = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString('ja-JP', {hour: '2-digit', minute: '2-digit'}) : '-';
const badge = (on, a, b, cls) => `<span class="badge ${on ? 'on' : (cls || 'off')}">${on ? a : b}</span>`;
let pkgFilled = false;

function svc(name, s) {
  const txt = s.state === 'ours' ? `稼働中 (${s.port}, pid ${s.pid})` : s.state === 'foreign' ? `別のプロセスが ${s.port} を使用中` : `停止 (${s.port})`;
  return `<div class="row"><span>${name}</span>${badge(s.state === 'ours', txt, txt, s.state === 'foreign' ? 'warn' : 'off')}</div>`;
}
function render(st) {
  $('clock').textContent = new Date(st.now * 1000).toLocaleTimeString('ja-JP');
  $('warnings').innerHTML = (st.warnings || []).map((w) => `<div class="warnbox">⚠ ${esc(w)}</div>`).join('');
  const p = st.processes;
  $('procs').innerHTML = svc('アドバイザー', p.advisor) + svc('フロントエンド', p.frontend) + svc('Showdown', p.showdown)
    + `<div class="row"><span>学習ループ</span>${badge(p.training, '稼働中', '停止中')}</div>`
    + `<div class="row"><span>構築の測定 run</span>${badge(!p.measuring, 'なし', '実行中', 'warn')}</div>`;
  $('urls').innerHTML = st.urls.frontend
    ? `フロントエンド: <a href="${st.urls.frontend}" target="_blank">${st.urls.frontend}</a> `
      + `(このMac: <a href="${st.urls.frontend_local}" target="_blank">${st.urls.frontend_local}</a>)`
    : 'フロントエンド: 停止中 (「接続テスト開始」で起動すると URL がここに出ます)';
  const t = st.test;
  let th = '';
  if (t.active) {
    const b = t.battles;
    const pct = Math.min(100, Math.round(100 * b.n / t.target));
    th = `<div class="row"><span>状態</span>${badge(true, '進行中 (開始 ' + hhmm(t.started) + ')', '')}</div>`
      + `<div class="row"><span>対戦 (マーカー以降)</span><span>${b.n} 戦 / 目標 ${t.target} 戦 (勝 ${b.wins} 敗 ${b.losses}、勝敗確定 ${b.decided})</span></div>`
      + `<div class="bar"><div style="width:${pct}%"></div></div>`;
  } else {
    th = `<div class="row"><span>状態</span><span class="badge off">未開始</span></div>`;
  }
  if (st.frames) {
    const f = st.frames;
    const dr = f.drop_rate == null ? '-' : Math.round(f.drop_rate * 100) + '%';
    th += `<div class="row"><span>フレーム (server ログの最新値)</span><span>受信 ${f.received} / 処理 ${f.processed} / 破棄 ${f.dropped} (取りこぼし ${dr})</span></div>`;
  }
  $('test').innerHTML = th;
  const e = st.experiment;
  if (e.package_id) {
    const k = e.package || {};
    $('exp').innerHTML = `<div class="row"><span>ラベル</span>${badge(true, 'ON', '')}</div>`
      + `<div class="row"><span>Package</span><span>${esc(e.package_id)} ${k.status ? '(' + esc(k.status) + ')' : ''}</span></div>`
      + (k.candidate_id ? `<div class="row"><span>候補</span><span>${esc(k.candidate_id)}${k.direction_ja ? ' / ' + esc(k.direction_ja) : ''}</span></div>` : '')
      + (k.species && k.species.length ? `<div class="row"><span>種族</span><span>${esc((k.species_ja || k.species).join(' / '))}</span></div>` : '');
  } else {
    $('exp').innerHTML = `<div class="row"><span>ラベル</span><span class="badge off">OFF (対戦ログは organic / recommended)</span></div>`;
  }
  const sel = $('pkg');
  if (!pkgFilled || sel.options.length !== st.packages.length) {
    const cur = sel.value;
    sel.innerHTML = st.packages.map((k) => {
      const hd = k.holdout && k.holdout.verdict ? ` holdout ${k.holdout.verdict}` + (k.holdout.delta != null ? ` ${(k.holdout.delta >= 0 ? '+' : '') + k.holdout.delta.toFixed(3)}` : '') : '';
      const sp = (k.species_ja || k.species || []).join('/');
      return `<option value="${esc(k.id)}">${esc(k.status)}: ${esc(k.candidate_id || '')} ${esc(sp)} — ${esc(k.id)}${hd}</option>`;
    }).join('') || '<option value="">(試用できる Package が無い)</option>';
    if (cur) sel.value = cur;
    pkgFilled = true;
  }
  const j = st.job;
  const running = !!(j && j.running);
  document.querySelectorAll('button[data-action]').forEach((b) => { b.disabled = running; });
  if (j) {
    const state = j.running ? '実行中…' : (j.previous ? '前回の出力' : (j.rc === 0 ? '完了 (rc=0)' : `終了 (rc=${j.rc})`));
    $('jobhead').textContent = `${j.label} — ${state} (${hhmm(j.started)} 開始)`;
    const out = $('out');
    const atBottom = out.scrollTop + out.clientHeight >= out.scrollHeight - 20;
    out.textContent = j.tail || '(出力なし)';
    if (j.running && atBottom) out.scrollTop = out.scrollHeight;
  }
}
async function refresh() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    render(await r.json());
  } catch (e) {
    $('procs').innerHTML = '<span class="badge off">操作パネルに接続できません</span>';
  }
}
async function run(action) {
  if (CONFIRM[action] && !confirm(CONFIRM[action])) return;
  const body = {action: action, package_id: action === 'experiment_on' ? $('pkg').value : null};
  const r = await fetch('/api/run', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Control-Panel': '1'}, body: JSON.stringify(body)});
  const j = await r.json();
  if (!r.ok) alert(j.error || '失敗しました');
  refresh();
}
document.querySelectorAll('button[data-action]').forEach((b) => b.addEventListener('click', () => run(b.dataset.action)));
setInterval(refresh, POLL_MS);
refresh();
</script></body></html>
"""


def render_page(settings: Settings, actions: dict) -> str:
    confirm = {k: a.confirm for k, a in actions.items() if a.confirm}
    return (PAGE_HTML.replace("__POLL_MS__", str(int(settings.poll_sec * 1000)))
            .replace("__CONFIRM__", json.dumps(confirm, ensure_ascii=False)))


def make_handler(panel: Panel):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ControlPanel/1"

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, render_page(panel.s, panel.actions).encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/api/status":
                try:
                    self._json(200, panel.status())
                except Exception as e:  # 実測の失敗はページに出す (落とさない)
                    self._json(500, {"error": f"{type(e).__name__}: {e}"})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/run":
                self._json(404, {"error": "not found"})
                return
            if not self.headers.get(POST_HEADER):
                self._json(403, {"error": f"{POST_HEADER} ヘッダが必要です"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except ValueError:
                self._json(400, {"error": "JSON が不正です"})
                return
            try:
                job = panel.run(str(body.get("action") or ""), body.get("package_id") or None)
            except JobBusy as e:
                self._json(409, {"error": f"実行中のジョブがあります: {e}"})
                return
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"job": job})

        def log_message(self, fmt, *args) -> None:  # 定期取得のログは出さない
            if self.command == "GET" and self.path.startswith("/api/"):
                return
            super().log_message(fmt, *args)

    return Handler


def serve(root: Path, settings: Settings) -> None:
    panel = Panel(root, settings)
    httpd = ThreadingHTTPServer((settings.bind, settings.port), make_handler(panel))
    httpd.daemon_threads = True
    print(f"[control_panel] http://localhost:{settings.port}/ (bind {settings.bind}, root {root})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description="接続テストの操作パネル (常駐の Web ページ)")
    ap.add_argument("--port", type=int, default=None, help="待ち受けポート (既定 config/ports.env の CONTROL_PORT_DEFAULT)")
    ap.add_argument("--bind", default=None, help="待ち受けアドレス (既定 config/control_panel.env の CONTROL_BIND)")
    ap.add_argument("--root", default=str(REPO))
    args = ap.parse_args()
    root = Path(args.root)
    settings = Settings.load(root)
    if args.port:
        settings.port = args.port
    if args.bind:
        settings.bind = args.bind
    serve(root, settings)


if __name__ == "__main__":
    main()
