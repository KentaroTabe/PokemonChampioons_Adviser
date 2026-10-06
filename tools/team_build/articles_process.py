"""記事のバッチ処理の骨格 (docs/ARTICLE_BANK_DESIGN_1006.md §3.9)。HTTP はしない (取得の関数は作らない: ホストの許可待ち)。

process_batch(units, dic, aliases, site_store, llm_resolver=None, limits=None, policy=None) の手順:
  0. 入力の検査: unit の kind が表にあること、source / meta に本文が無いこと (assert_no_prose)、出典の 2 軸が表の値であること
  1. ホストの門と上限: host_allowed(policy, source.host, "fetch", url=source.url) が偽の unit は解析しない (status = host_not_allowed。
     方針に allowed_urls があれば、その URL 以外も偽)。
     ページ (url_hash。無ければ本文のハッシュ) 単位で、BUILD_ARTICLE_BATCH_MAX_ARTICLES ページ・本文の合計
     BUILD_ARTICLE_BATCH_MAX_BODY_CHARS 文字までを受け入れ、超えたページの unit は処理しない (status = deferred。本文をディスクへ退避しない)
  2. 受け入れた unit を全部解析し、未解決の名前とサイト固有 id の観測を集める (観測は SiteIdStore へ。同じページの観測は 1 回と数える)
  3. known_transform / site_id_verified で確定できる別名を別名辞書に反映する (decide。既存と矛盾するものは conflict)
  4. llm_resolver が与えられていれば、host_allowed(policy, host, "send_llm") が真の unit の名前だけを 1 回で LLM に渡して
     candidate を得る (自動確定しない。確認待ちの候補がある名前は送り直さない)
  5. 新たに confirmed になった別名に関係する unit だけ、メモリ上の本文で再解析する
  6. build_record → 本文の門 (assert_no_prose を記録全体に掛ける)
  7. 本文は破棄する (戻り値に unit・本文を入れない)。失敗・上限超過は未解決のまま state 行に残す
戻り値: {"records", "state_rows" (本文なし: url_hash / body_hash / unit / kind / status / parser_version / n_unresolved /
         n_unresolved_names), "alias_entries": {"confirmed", "candidate"} (この処理で足した・格上げした entry),
         "alias_conflicts", "aliases" (更新後の別名辞書のデータ), "site_store" (更新後の SiteIdStore),
         "unresolved_names" (ローカル用。名前 1 語ずつ + state 行と同じ url_hash / unit), "counts"}
保存 (別名辞書・観測・state) は呼び出し側が行う (append_state / article_aliases.save_aliases / SiteIdStore.save)。
テストは tests/test_articles_process.py。
"""
from __future__ import annotations

import datetime
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import (BUILD_ARTICLE_BATCH_MAX_ARTICLES, BUILD_ARTICLE_BATCH_MAX_BODY_CHARS,
                                    BUILD_ARTICLE_RECORD_KIND_MEMBERS)
from tools.team_build.article_aliases import (ACTIVE_STATUSES, SiteIdStore, alias_key, auto_entries, decide, empty_aliases,
                                              propose_with_llm)
from tools.team_build.article_bank import assert_no_prose, build_record, host_allowed, normalize_source
from tools.team_build.article_parse import ArticleDictionary, default_dictionary, parse_article
from tools.team_build.article_units import url_hash

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE_PATH = REPO / "logs" / "articles" / "state.jsonl"
STATE_ROW_KEYS = ("url_hash", "body_hash", "unit", "kind", "status", "parser_version", "n_unresolved", "n_unresolved_names")


def _hash16(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def batch_limits(limits: Optional[dict] = None) -> dict:
    """上限 (config の既定 + 上書き)。知らない名前は ValueError"""
    lim = {"max_articles": BUILD_ARTICLE_BATCH_MAX_ARTICLES, "max_body_chars": BUILD_ARTICLE_BATCH_MAX_BODY_CHARS}
    unknown = set(limits or {}) - set(lim)
    if unknown:
        raise ValueError(f"上限の名前が違う (許可: {', '.join(lim)})")
    lim.update(limits or {})
    return lim


def _unit_info(units: list) -> list:
    """unit ごとの本文を含まない情報 (種類・ホスト・url_hash・本文のハッシュ・ページ・ページ内の番号・文字数)。入力の検査もここで行う"""
    info, pos = [], Counter()
    for u in units:
        kind = u.get("kind")
        if kind not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
            raise ValueError(f"unit の kind が表に無い値 (許可: {', '.join(BUILD_ARTICLE_RECORD_KIND_MEMBERS)})")
        src, meta = u.get("source") or {}, u.get("meta") or {}
        assert_no_prose(src, "$.source")
        assert_no_prose(meta, "$.meta")
        normalize_source(src)                                     # 出典の 2 軸の検査 (記録を作る前に失敗させる)
        marked = u.get("marked") or ""
        body_hash = src.get("body_hash") or _hash16(marked)
        uh = src.get("url_hash") or (url_hash(src["url"]) if src.get("url") else None)
        page = uh or f"body:{body_hash}"
        info.append({"kind": kind, "host": src.get("host"), "url": src.get("url"), "url_hash": uh, "body_hash": body_hash, "page": page,
                     "unit": pos[page], "chars": len(marked)})
        pos[page] += 1
    return info


def _state_row(inf: dict, status: str, parsed: Optional[dict] = None, record: Optional[dict] = None) -> dict:
    return {"url_hash": inf["url_hash"], "body_hash": inf["body_hash"], "unit": inf["unit"], "kind": inf["kind"], "status": status,
            "parser_version": parsed.get("parser_version") if parsed else None,
            "n_unresolved": len(record.get("unresolved") or []) if record else None,
            "n_unresolved_names": len(parsed.get("unresolved_names") or []) if parsed else None}


def _parse(unit: dict, inf: dict, dic: ArticleDictionary) -> dict:
    return parse_article(unit.get("marked") or "", dic, max_members=BUILD_ARTICLE_RECORD_KIND_MEMBERS[inf["kind"]], host=inf["host"])


def process_batch(units: list, dic: Optional[ArticleDictionary] = None, aliases: Optional[dict] = None, site_store=None,
                  llm_resolver: Optional[Callable[[dict], dict]] = None, limits: Optional[dict] = None, policy: Optional[dict] = None,
                  today: Optional[str] = None) -> dict:
    """unit の列 → 記録・別名の候補・処理状態 (本文を含まない)。手順は module の docstring。
    dic: 辞書 (別名は aliases で差し替える)。aliases: 別名辞書のデータ (article_aliases.load_aliases の形、None = 空)。
    site_store: SiteIdStore かその状態の dict (None = 空)。policy: host_policy ({host: {"fetch", "send_llm"}}。None = どこも許可しない)。
    llm_resolver: 名前の対応を返す関数 (article_aliases.llm_resolver。None = LLM を使わない)"""
    lim = batch_limits(limits)
    today = today or datetime.date.today().isoformat()
    base_dic = dic or default_dictionary()
    alias_data = json.loads(json.dumps(aliases)) if aliases else empty_aliases()
    store = site_store if isinstance(site_store, SiteIdStore) else SiteIdStore(site_store)
    units = list(units or [])
    info = _unit_info(units)                                       # 0. 入力の検査
    counts: Counter = Counter(units=len(units))
    rows: list = [None] * len(units)
    # 1. ホストの門と上限 (ページ単位で受け入れる: 同じページの unit を途中で分けない)
    accepted: list = []
    n_pages = n_chars = 0
    for page in dict.fromkeys(inf["page"] for inf in info):
        idxs = [i for i, inf in enumerate(info) if inf["page"] == page]
        allowed = []
        for i in idxs:
            if host_allowed(policy, info[i]["host"], "fetch", url=info[i]["url"]):      # allowed_urls があれば URL も見る
                allowed.append(i)
            else:
                rows[i] = _state_row(info[i], "host_not_allowed")
                counts["host_not_allowed"] += 1
        if not allowed:
            continue
        page_chars = sum(info[i]["chars"] for i in allowed)
        if n_pages >= lim["max_articles"] or n_chars + page_chars > lim["max_body_chars"]:
            for i in allowed:
                rows[i] = _state_row(info[i], "deferred")
            counts["deferred"] += len(allowed)
            continue
        n_pages += 1
        n_chars += page_chars
        accepted.extend(allowed)
    counts["pages_accepted"], counts["body_chars"] = n_pages, n_chars
    # 2. 解析と観測
    work_dic = base_dic.with_aliases(alias_data)
    parsed_by: dict = {}
    for i in sorted(accepted):
        try:
            parsed_by[i] = _parse(units[i], info[i], work_dic)
        except Exception as e:                                     # 解析器の失敗は未解決として残す (例外の文言は記録しない)
            rows[i] = _state_row(info[i], "parse_error")
            counts["parse_error"] += 1
            counts[f"parse_error:{type(e).__name__}"] += 1
    for i in sorted(parsed_by):
        obs = parsed_by[i]["site_id_observations"]
        store = store.observe(obs, source_id=info[i]["page"])
        counts["site_observations"] += len(obs)
    items = [(i, it) for i in sorted(parsed_by) for it in parsed_by[i]["unresolved_names"]]
    # 3. 元の表記との対応を裏付けられる別名だけ自動確定
    dec = decide(auto_entries([it for _i, it in items], work_dic, store, today), alias_data)
    alias_data = dec["data"]
    changed = dec["added"] + dec["upgraded"]
    conflicts = list(dec["conflicts"])
    newly_confirmed = {alias_key(e["category"], e["alias"]) for e in changed if e["status"] == "confirmed"}
    # 4. LLM (送信が許されたホストの unit の名前だけ。確認待ち・確定済みの名前は送らない)
    if llm_resolver is not None:
        active = {alias_key(e["category"], e["alias"]) for e in alias_data["entries"] if e["status"] in ACTIVE_STATUSES}
        send = [it for i, it in items if host_allowed(policy, info[i]["host"], "send_llm")
                and alias_key(it["category"], it["text"]) not in active]
        counts["llm_names_blocked_by_host"] = sum(1 for i, _it in items if not host_allowed(policy, info[i]["host"], "send_llm"))
        if send:
            prop = propose_with_llm(send, llm_resolver, dic=work_dic, today=today)
            for k, v in prop["counts"].items():
                counts[f"llm_{k}"] += v
            dec2 = decide(prop["entries"], alias_data)
            alias_data = dec2["data"]
            changed += dec2["added"] + dec2["upgraded"]
            conflicts += dec2["conflicts"]
    # 5. 新たに confirmed になった別名に関係する unit だけ、メモリ上の本文で再解析
    if newly_confirmed:
        work_dic2 = base_dic.with_aliases(alias_data)
        for i in sorted(parsed_by):
            if any(alias_key(it["category"], it["text"]) in newly_confirmed for it in parsed_by[i]["unresolved_names"]):
                try:
                    parsed_by[i] = _parse(units[i], info[i], work_dic2)
                    counts["reparsed"] += 1
                except Exception as e:                             # 再解析の失敗は最初の解析結果を使う
                    counts[f"reparse_error:{type(e).__name__}"] += 1
    # 6. 記録と本文の門
    records: list = []
    for i in sorted(parsed_by):
        u = units[i]
        rec = build_record(parsed_by[i], u.get("source") or {}, u.get("meta") or {}, record_kind=info[i]["kind"])
        assert_no_prose(rec)
        records.append(rec)
        rows[i] = _state_row(info[i], rec["status"], parsed_by[i], rec)
        counts["processed"] += 1
        counts[f"status:{rec['status']}"] += 1
    unresolved_left = [dict(it, url_hash=info[i]["url_hash"], unit=info[i]["unit"]) for i in sorted(parsed_by)
                       for it in parsed_by[i]["unresolved_names"]]
    counts["aliases_confirmed"] = sum(1 for e in changed if e["status"] == "confirmed")
    counts["aliases_candidate"] = sum(1 for e in changed if e["status"] == "candidate")
    counts["alias_conflicts"] = len(conflicts)
    # 7. 本文の破棄: 戻り値には unit・本文・解析の途中結果を入れない
    parsed_by.clear()
    assert_no_prose(rows, "$.state_rows")
    return {"records": records, "state_rows": rows,
            "alias_entries": {"confirmed": [e for e in changed if e["status"] == "confirmed"],
                              "candidate": [e for e in changed if e["status"] == "candidate"]},
            "alias_conflicts": conflicts, "aliases": alias_data, "site_store": store, "unresolved_names": unresolved_left,
            "counts": dict(counts)}


def append_state(rows: list, path: Path = DEFAULT_STATE_PATH) -> Path:
    """処理状態の行を logs/articles/state.jsonl に足す (本文なし。STATE_ROW_KEYS 以外の項目・かな漢字を含む値は書かない)"""
    for r in rows:
        if set(r) - set(STATE_ROW_KEYS):
            raise ValueError("state 行に知らない項目がある")
    assert_no_prose(rows, "$.state_rows")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return p
