"""相手の型 (性格/努力値/持ち物) の推定。

行動の先後 (素早さの不等式) と与ダメージ/被ダメージの観測から、
使用率DBの型候補 (spread_usage × item_usage) を尤度スコアリングする。
自由なEV回帰は行わない: 実際の相手はDB上位の型にほぼ収まるため、
候補集合に対するベイズ更新の方がはるかに頑健。

観測の収集は SpreadTracker.on_frame (サーバーの毎フレーム処理から呼ぶ)。
推定結果は best() でアドバイザーが参照する。

2026-10-30 (docs/DESIGN_ITEM_INFERENCE_20261030.md §6.4 の論点 3・4):
- 読む使用率 DB と snapshot は advisor.data_pin の版 (環境変数 ADVICE_USAGE_DB / ADVICE_USAGE_SNAPSHOT、再生の set_override)。
  版を明示したとき (pinned) はその snapshot だけを読み、DB・snapshot が無ければ InputsPinError で止める (最新へ退避しない)。
  明示が無ければ従来どおり DB_PATH の チャンピオンズの全 snapshot (_champions_filter) → 全ソースの順。
  トラッカーの推定器はデータの版 (data_version) ごとに作り直し、data_pin.reset_caches() でも消える。
- 表示で判明した相手の持ち物は、トラッカーが個体ごとに一度だけ observe_item(..., add_missing=True) に渡す
  (item_observation / knocked_off_items / item_to_pass。消費・喪失の後は出力の持ち物を「なし」にする)。

2026-10-31 (レビューの再現、開発者承認。docs/KNOWN_ISSUES.md E-ev-1 / E-ev-2):
- 版を明示したとき、DB のテーブル欠落等の sqlite の例外も InputsPinError に包んで上げる (空の仮説で続けない)。
- 持ち物の変更・消費・喪失で、以前の条件で得た実効素早さの範囲 (spe_lower / spe_upper) を捨てる (speed_range_stale)。
- 推定器は観測の記録 (_obs_log) と事前の仮説 (_prior_hyps) を持つ。候補外の持ち物の初判明は 事前の配分 × 新しい持ち物 から
  仮説を作り直して記録した観測を評価し直し (prior_item_hyps + replay_observations)、実際の変更 (別の持ち物を渡し済み、または
  持ち物なしの後) は配分の事後を保って持ち物だけ切り替える (switch_item_hyps)。初判明か変更かは item_reveal_kind。
"""
from __future__ import annotations

import copy
import math
import sqlite3
import time
from pathlib import Path
from typing import Optional

from advisor.damage import MonView, calc_damage
from advisor.dex import get_dex

DB_PATH = (Path(__file__).resolve().parent.parent
           / "champions_agent" / "data" / "db" / "champions.sqlite3")

_STATS = ("hp", "atk", "def", "spa", "spd", "spe")

_NATURE_MODS = {
    "adamant": ("atk", "spa"), "naughty": ("atk", "spd"),
    "lonely": ("atk", "def"), "brave": ("atk", "spe"),
    "bold": ("def", "atk"), "impish": ("def", "spa"),
    "lax": ("def", "spd"), "relaxed": ("def", "spe"),
    "modest": ("spa", "atk"), "mild": ("spa", "def"),
    "rash": ("spa", "spd"), "quiet": ("spa", "spe"),
    "calm": ("spd", "atk"), "gentle": ("spd", "def"),
    "careful": ("spd", "spa"), "sassy": ("spd", "spe"),
    "timid": ("spe", "atk"), "hasty": ("spe", "def"),
    "naive": ("spe", "spd"), "jolly": ("spe", "spa"),
}

_NATURE_JA = {
    "adamant": "いじっぱり", "jolly": "ようき", "modest": "ひかえめ",
    "timid": "おくびょう", "bold": "ずぶとい", "impish": "わんぱく",
    "calm": "おだやか", "careful": "しんちょう", "relaxed": "のんき",
    "sassy": "なまいき", "brave": "ゆうかん", "quiet": "れいせい",
    "naughty": "やんちゃ", "lonely": "さみしがり", "lax": "のうてんき",
    "rash": "うっかりや", "mild": "おっとり", "gentle": "おとなしい",
    "hasty": "せっかち", "naive": "むじゃき",
}


def _dashify(species_id: str) -> str:
    """showdown id -> DBのダッシュ形式候補 (greattusk -> great-tusk 等は
    既存テーブルの名寄せに依存するため、両方式を試す)"""
    return species_id


def _ev_to_points(evs: dict) -> dict:
    """努力値 (0-252) をチャンピオンズの能力ポイント (0-32, 合計66) に換算。

    Showdown系スプレッドの端数 (252/252/4の4など) は切り上げた上で、
    合計が66に2以内で届かない場合は端数枠 (4ポイント未満) に寄せる
    (252/252/4 -> 32/32/2 という自然な表記になる)。
    """
    pts = {k: min(32, (v + 7) // 8) for k, v in evs.items() if v}
    total = sum(pts.values())
    diff = 66 - total
    if 0 < diff <= 2:
        small = [k for k, v in pts.items() if v < 4]
        if len(small) == 1:
            pts[small[0]] += diff
    return pts


def _nature_mult(nature: str) -> dict:
    pair = _NATURE_MODS.get(nature)
    if not pair:
        return {}
    return {pair[0]: 1.1, pair[1]: 0.9}


def _item_key(item) -> Optional[str]:
    """持ち物の名前を仮説の持ち物 id の形にそろえる (純粋。"Choice Scarf" / "choice-scarf" → "choicescarf")"""
    return (str(item or "")).replace("-", "").replace(" ", "").lower() or None


def data_version() -> tuple:
    """型推定が読む使用率の版 (db のパス, 明示した snapshot id or None, pinned)。advisor.data_pin.usage_source() を読む
    (環境変数と set_override。明示が無ければ db はこのモジュールの DB_PATH (テストが差し替える) で snapshot は None = 従来の範囲)"""
    from advisor.data_pin import usage_source
    src = usage_source()
    db = src["db"] if src["pinned"] else DB_PATH
    return (str(db), src["snapshot_id"], bool(src["pinned"]))


def _open_usage() -> tuple:
    """使用率 DB を開き (接続, [第一の絞り込み, 予備の絞り込み]) を返す (副作用: DB を開く)。
    版を明示したとき: data_pin.connect_usage(読み取り専用) + resolve_snapshot で、その snapshot だけを読む絞り込みを 2 つとも使う。
      DB・snapshot が無ければ InputsPinError (握りつぶさない)。
    明示が無いとき: 従来どおり DB_PATH を開き、チャンピオンズの全 snapshot (_champions_filter) → 全ソース ("1=1")。DB が無ければ (None, None)"""
    from advisor.data_pin import InputsPinError, connect_usage, resolve_snapshot
    _db, snap, pinned = data_version()
    if pinned:
        try:
            conn = connect_usage(Path(_db), True)
        except sqlite3.Error as e:
            raise InputsPinError(f"明示した使用率 DB {_db} を開けない: {e}") from e
        try:
            sid = resolve_snapshot(conn, snap, True)
        except sqlite3.Error as e:
            conn.close()
            raise InputsPinError(f"明示した使用率 DB {_db} の snapshot を読めない: {e}") from e
        except Exception:
            conn.close()
            raise
        flt = f"snapshot_id = {int(sid)}"
        return conn, [flt, flt]
    conn = connect_usage(Path(_db), False)
    if conn is None:
        return None, None
    try:
        # チャンピオンズ実データを優先し、無い場合のみ全ソース (Smogon フォールバック含む) を使う。SVデータの型を混ぜない
        from advisor.team_advice import _champions_filter
        return conn, [_champions_filter(conn), "1=1"]
    except Exception:
        conn.close()
        raise


def _usage_rows(db, table: str, cols: str, species_id: str, flt: str, limit: Optional[int]) -> list:
    """種族の行を重みの降順で (名前は DB の表記そのまま → 記号を抜いた表記の順に照合)。limit=None は全部"""
    lim = f" LIMIT {int(limit)}" if limit is not None else ""
    for name_expr in (
        "pokemon_name = ?",
        "REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ?",
    ):
        got = list(db.execute(
            f"SELECT {cols}, SUM(usage_percent) w FROM {table} "
            f"WHERE {name_expr} AND {flt} GROUP BY {cols} "
            f"ORDER BY w DESC{lim}", (species_id,)))
        if got:
            return got
    return []


def item_share(rows: list, item_id: str) -> Optional[float]:
    """持ち物の行 [(item_name, 重み)] の中で item_id の占める割合 (純粋)。行が無い・一覧に無ければ None"""
    total = sum(w for _, w in rows if w) or 0.0
    if total <= 0:
        return None
    key = _item_key(item_id)
    got = sum(w for n, w in rows if w and _item_key(n) == key)
    return (got / total) if got > 0 else None


def _pin_query_error(e: Exception, what: str):
    """版を明示しているとき、使用率の読み取りで起きた sqlite の例外を InputsPinError にして返す (明示が無ければ None。2026-10-31)"""
    from advisor.data_pin import InputsPinError
    db, snap, pinned = data_version()
    if not pinned:
        return None
    return InputsPinError(f"明示した使用率 DB {db} (snapshot {snap}) の {what} を読めない: {e}")


def _spread_key(h: dict) -> tuple:
    return (h["nature"], tuple(sorted((h["evs"] or {}).items())))


def _group_spreads(hyps: list) -> list:
    """仮説を配分 (性格・努力値) ごとにまとめ、[(代表の仮説, 重みの対数和)] を出てきた順に返す (純粋)"""
    groups: dict = {}
    for h in hyps:
        groups.setdefault(_spread_key(h), []).append(h)
    out = []
    for hs in groups.values():
        mx = max(h["logw"] for h in hs)
        out.append((hs[0], mx + math.log(sum(math.exp(h["logw"] - mx) for h in hs))))
    return out


def switch_item_hyps(hyps: list, item_id: str) -> list:
    """実際の持ち物の変更 (トリック等。2026-10-31): 配分の事後 (仮説の重みを配分ごとに合計) を保ち、持ち物だけ item_id に切り替える
    (純粋)。変更前の観測は変更前の持ち物で評価した重みとして配分に残る。hyps は変えずに新しい一覧を返す"""
    iid = _item_key(item_id)
    if not hyps or not iid:
        return list(hyps)
    return [{"nature": h["nature"], "evs": dict(h["evs"]), "item": iid, "logw": lw} for h, lw in _group_spreads(hyps)]


def prior_item_hyps(prior_hyps: list, item_id: str, prior: float) -> list:
    """候補外の持ち物の初判明 (2026-10-31): 事前の配分 × 新しい持ち物 の仮説 (純粋)。重み = 配分の事前の重み (事前の仮説を配分ごとに
    合計) × prior (持ち物の事前の重み = DB の採用率、無ければ下限)。観測は入っていない (呼び出し側が記録から評価し直す)"""
    iid = _item_key(item_id)
    if not prior_hyps or not iid:
        return []
    lp = math.log(max(float(prior), 1e-300))
    return [{"nature": h["nature"], "evs": dict(h["evs"]), "item": iid, "logw": lw + lp} for h, lw in _group_spreads(prior_hyps)]


def item_reveal_kind(confirmed: Optional[str], gone_before: bool, item_id: Optional[str]) -> Optional[str]:
    """判明した持ち物の観測が「初判明」か「実際の変更」か (純粋。2026-10-31)。
    - confirmed = これまでに渡した (確定した) 持ち物、gone_before = 観測の直前に「現在は持ち物なし」だったか
    - 別の持ち物を渡し済み、または持ち物なし (消費・喪失) の後に持ち物が判明した → "change" (以前の観測は以前の持ち物のもの)
    - それ以外 → "first" (観測の間ずっとこの持ち物だった)。item_id が無ければ None"""
    iid = _item_key(item_id)
    if not iid:
        return None
    if gone_before or (_item_key(confirmed) and _item_key(confirmed) != iid):
        return "change"
    return "first"


def speed_range_stale(item_changed: bool, gone_before: bool, gone_now: bool) -> bool:
    """以前の条件で得た実効素早さの範囲を捨てるか (純粋。2026-10-31): 持ち物が変わった、または「現在は持ち物なし」が切り替わった"""
    return bool(item_changed) or bool(gone_before) != bool(gone_now)


# 評価し直す観測の種類 (記録の kind)
REPLAYABLE_OBS = ("speed", "damage", "choice_lock", "item")


def replay_observations(hyps: list, log, apply) -> int:
    """記録した観測を hyps に順に掛け直す (純粋な高階関数。2026-10-31)。apply(hyps, entry) が尤度を掛ける。
    種類が REPLAYABLE_OBS に無い記録は飛ばす (その観測の重みは流用しない)。戻り値は掛け直した件数"""
    n = 0
    for e in log or ():
        if isinstance(e, dict) and e.get("kind") in REPLAYABLE_OBS:
            apply(hyps, e)
            n += 1
    return n


def trim_obs_log(log: list, max_n: int) -> list:
    """観測の記録を新しい方から max_n 件に切る (純粋。古い観測は評価し直せなくなる = 初判明のとき流用しない)"""
    if max_n is None or max_n < 0:
        return log
    return log[-max_n:] if max_n else []


def confirm_item_hyps(hyps: list, item_id: str, prior: float) -> list:
    """表示で確定した持ち物に仮説を絞る (純粋。2026-10-30)。
    - 仮説にその持ち物があれば、その持ち物の仮説だけを残す (持ち物の違う仮説は落とす)。
    - 無ければ、配分 (性格・努力値) ごとに持ち物だけを確定した持ち物に差し替えた仮説を 1 つ作る。重みは その配分の仮説の重みの
      合計 (これまでの観測を含む) × prior (事前の重み = DB の採用率、一覧に無ければ下限)。持ち物の違う仮説は落とす。
    hyps は変えずに新しい一覧を返す"""
    iid = _item_key(item_id)
    if not hyps or not iid:
        return list(hyps)
    same = [h for h in hyps if (h["item"] or "") == iid]
    if same:
        return same
    lp = math.log(max(float(prior), 1e-300))
    return [{"nature": h["nature"], "evs": dict(h["evs"]), "item": iid, "logw": lw + lp} for h, lw in _group_spreads(hyps)]


def item_observation(mon: Optional[dict]) -> tuple:
    """state の相手の個体の持ち物の欄の解釈 (純粋。2026-10-30)。戻り値 (渡す持ち物 id or None, 現在は持ち物なしか)。
    vision/state.PokemonState の欄 (item_id / item_consumed / item_removed) の組み合わせと意味:
    - item_id あり・item_consumed なし: 今その持ち物を持っている (表示の文言・発動・ポップアップ・手入力で判明) → (item_id, False)。
      item_removed が立っていても item_id があれば、喪失 (vision は item_id を消す) の後に入った別の持ち物 (トリック等) とみなす
    - item_id あり・item_consumed あり: その持ち物を持っていて消費した (きのみ・タスキ・ふうせん・しろいハーブ等が発動して無くなった)
      → 「以前その持ち物を持っていた」の観測 + 現在は持ち物なし → (item_id, True)
    - item_id なし・item_removed あり: はたきおとす等で失った。vision は item_id を消すので、失った物は state からは分からない
      (分かるのは同じフレームの発火 knockoff_opponent_<id> か、それ以前に判明していたとき) → (None, True)
    - item_id なし・item_consumed あり (通常は起きない): 消費したが物は不明 → (None, True)
    - どれも無い: 未判明 → (None, False)"""
    if not isinstance(mon, dict):
        return None, False
    iid = _item_key(mon.get("item_id"))
    consumed, removed = bool(mon.get("item_consumed")), bool(mon.get("item_removed"))
    if iid:
        return iid, consumed
    return None, (consumed or removed)


def knocked_off_items(fired: list, side: str = "opponent") -> list:
    """このフレームの発火から、はたきおとす等で失われた持ち物 id (純粋。vision.events の event_id knockoff_<side>_<item id>)"""
    pre = f"knockoff_{side}_"
    return [_item_key(f[len(pre):]) for f in (fired or []) if isinstance(f, str) and f.startswith(pre) and f[len(pre):]]


def item_to_pass(passed: Optional[str], item_id: Optional[str]) -> Optional[str]:
    """型推定に渡す持ち物 (純粋)。同じ個体にまだ渡していない持ち物の観測だけを返す (同じ物を続けて渡さない)。
    渡した物と違う物 (はたきおとす・トリックで持ち物が変わった後の新しい持ち物) は別の 1 回として返す"""
    iid = _item_key(item_id)
    if not iid or iid == _item_key(passed):
        return None
    return iid


def _snapshot_obj(obj):
    """観測の記録に残す引数の写し (MonView・FieldView。写せなければそのもの)"""
    try:
        return copy.deepcopy(obj)
    except Exception:
        return obj


class SpreadEstimator:
    """1体の相手ポケモンに対する型仮説の管理"""

    item_gone = False   # 既定 (__init__ を通さずに作る呼び出し側のため。意味は __init__ の注記)

    def __init__(self, species_id: str):
        self.species_id = species_id
        self.hyps: list = self._build_hypotheses(species_id)
        # 事前の仮説の写しと観測の記録 (2026-10-31。候補外の持ち物の初判明で評価し直す。メモリ内だけ)
        self._prior_hyps: list = copy.deepcopy(self.hyps)
        self._obs_log: list = []
        self._confirmed_item: Optional[str] = None   # add_missing=True で確定した持ち物 (初判明と変更の区別)
        self.n_obs = 0
        self.spe_lower: Optional[float] = None   # 実効素早さの下限 (観測由来)
        self.spe_upper: Optional[float] = None
        self._choice_locked = False
        # 現在は持ち物なし (消費・喪失を観測した。2026-10-30)。仮説の持ち物 (以前持っていた物) は変えず、
        # 出力 (best / top_k / speed_estimate) と以後の観測の計算では持ち物なしにする。新しい持ち物を観測したら外す
        self.item_gone = False

    # ------------------------------------------------------------------
    @staticmethod
    def _query_item_share(species_id: str, item_id: str) -> Optional[float]:
        """その種族の持ち物の一覧 (_query_spreads と同じ版・同じ絞り込みの順) の中で item_id の割合。一覧に無ければ None。
        版を明示していて DB・snapshot が無ければ InputsPinError"""
        from advisor.data_pin import InputsPinError
        try:
            db, filters = _open_usage()
        except InputsPinError:
            raise
        except Exception:
            return None
        if db is None:
            return None
        try:
            for flt in dict.fromkeys(filters):
                rows = _usage_rows(db, "item_usage", "item_name", species_id, flt, None)
                if rows:
                    return item_share(rows, item_id)
            return None
        except sqlite3.Error as e:
            err = _pin_query_error(e, "item_usage")
            if err is not None:
                raise err from e
            return None
        except Exception:
            return None
        finally:
            db.close()

    def _item_prior(self, item_id: str) -> float:
        """仮説に無い持ち物を足すときの事前の重み: DB の一覧にあればその採用率、無ければ config.EV_INFER_ITEM_FLOOR"""
        from champions_agent import config as C
        share = self._query_item_share(self.species_id, item_id)
        return share if share and share > 0 else float(C.EV_INFER_ITEM_FLOOR)

    @staticmethod
    def _query_spreads(species_id: str) -> list:
        """[配分の行 [(nature, evs, 重み)], 持ち物の行 [(item_name, 重み)]]。版は advisor.data_pin (_open_usage)。
        版を明示していて DB・snapshot・テーブルが無ければ (sqlite の例外も) InputsPinError (握りつぶさない)。明示が無いときの失敗は従来どおり空"""
        from advisor.data_pin import InputsPinError
        try:
            db, filters = _open_usage()
        except InputsPinError:
            raise
        except Exception:
            return [[], []]
        if db is None:
            return [[], []]
        try:
            def q(table, cols, flt, limit):
                return _usage_rows(db, table, cols, species_id, flt, limit)

            rows = q("spread_usage", "nature, evs", filters[0], 8)
            if not rows:
                rows = q("spread_usage", "nature, evs", filters[1], 8)
            elif all(r[0] is None for r in rows):
                # チャンピオンズ実データ (pokedb) は性格を持たないため、
                # 性格つきのフォールバック行を低重みで補完する
                extra = q("spread_usage", "nature, evs", filters[1], 6)
                seen = {(r[0], r[1]) for r in rows}
                total = sum(r[2] for r in rows) or 1.0
                for nat, evs, w in extra:
                    if nat is not None and (nat, evs) not in seen:
                        rows.append((nat, evs, w * 0.3 * total /
                                     (sum(x[2] for x in extra) or 1.0)))
            items = q("item_usage", "item_name", filters[0], 4) or \
                q("item_usage", "item_name", filters[1], 4)
            return [rows, items]
        except sqlite3.Error as e:
            # 版を明示していればテーブル欠落等も止める (2026-10-31。空の仮説で続けない)
            err = _pin_query_error(e, "spread_usage / item_usage")
            if err is not None:
                raise err from e
            return [[], []]
        except Exception:
            return [[], []]
        finally:
            db.close()

    def _build_hypotheses(self, species_id: str) -> list:
        spreads, items = self._query_spreads(species_id)
        if not spreads:
            return []
        if not items:
            items = [(None, 1.0)]
        # pokedb由来データは性格行 (evs=None) とEV行 (nature=None) が独立
        # なので、両方があればクロス結合して仮説にする
        complete = [(n, e, w) for n, e, w in spreads if n and e]
        ev_only = [(e, w) for n, e, w in spreads if e and not n]
        nat_only = [(n, w) for n, e, w in spreads if n and not e]
        if ev_only and nat_only:
            tot_n = sum(w for _, w in nat_only) or 1.0
            for e, we in ev_only[:4]:
                for n, wn in nat_only[:4]:
                    complete.append((n, e, we * wn / tot_n))
        elif ev_only:
            complete += [(None, e, w) for e, w in ev_only]
        spreads = complete or spreads
        total_s = sum(w for _, _, w in spreads) or 1.0
        total_i = sum(w for _, w in items) or 1.0
        hyps = []
        for nature, evs_str, ws in spreads:
            try:
                vals = [int(x) for x in evs_str.split("/")]
                # DBには努力値表記 (0-252) とチャンピオンズの能力ポイント表記
                # (0-32) が混在する。最大32以下なら能力ポイントとみなし換算
                if vals and max(vals) <= 32:
                    vals = [min(252, v * 8) for v in vals]
                evs = dict(zip(_STATS, vals))
            except Exception:
                continue
            for item, wi in items:
                item_id = (item or "").replace("-", "").replace(" ", "").lower() or None
                hyps.append({
                    "nature": nature,
                    "evs": evs,
                    "item": item_id,
                    "logw": math.log((ws / total_s) * (wi / total_i) + 1e-9),
                })
        return hyps

    # ------------------------------------------------------------------
    def _view(self, hyp: dict, opp_state: dict, gone: Optional[bool] = None) -> Optional[MonView]:
        """仮説の MonView。gone = 持ち物なしで計算するか (None は今の item_gone。観測の評価し直しでは記録の時点の値)"""
        if gone is None:
            gone = self.item_gone
        dex = get_dex()
        sp = dex.species(self.species_id)
        if sp is None:
            return None
        return MonView(
            species_id=self.species_id,
            types=sp["types"],
            base=sp["baseStats"],
            hp_frac=1.0,
            boosts=opp_state.get("boosts") or {},
            ability=opp_state.get("ability_id"),
            # 消費・喪失の後は持ち物なしで計算する (仮説の持ち物は以前持っていた物のまま。2026-10-30)
            item=None if gone else hyp["item"],
            ev=hyp["evs"],
            nature=_nature_mult(hyp["nature"]),
        )

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    @staticmethod
    def _opp_snapshot(opp_state: Optional[dict]) -> dict:
        """観測の記録に残す相手の欄の写し (_view と先後の計算が読む欄だけ)"""
        o = opp_state or {}
        return {"boosts": dict(o.get("boosts") or {}), "ability_id": o.get("ability_id"), "status": o.get("status")}

    def _record(self, kind: str, **args) -> dict:
        """観測を記録に足して記録 (entry) を返す (2026-10-31)。gone = 記録の時点の「現在は持ち物なし」。
        件数の上限は config.EV_INFER_OBS_LOG_MAX (超えたら古い方から捨てる)。__init__ を通さずに作った推定器でも動く"""
        from champions_agent import config as C
        entry = dict(args, kind=kind, t=time.time(), gone=bool(self.item_gone))
        log = getattr(self, "_obs_log", None)
        if log is None:
            log = self._obs_log = []
        log.append(entry)
        if len(log) > int(C.EV_INFER_OBS_LOG_MAX):
            self._obs_log = trim_obs_log(log, int(C.EV_INFER_OBS_LOG_MAX))
        return entry

    def _apply_obs(self, hyps: list, entry: dict) -> None:
        """記録 1 件の尤度を hyps に掛ける (観測したときと、初判明で評価し直すときの共通。持ち物なしは記録の時点の値)"""
        kind, gone = entry.get("kind"), entry.get("gone")
        if kind == "speed":
            from advisor.damage import FieldView, effective_speed
            fv = FieldView(weather="rain" if entry["rain"] else None)
            opp_state, my_spe = entry["opp"], entry["my_spe"]
            for h in hyps:
                v = self._view(h, opp_state, gone=gone)
                if v is None:
                    continue
                v.status = opp_state.get("status")
                spe = effective_speed(v, fv)
                consistent = (spe > my_spe) if entry["opp_first"] else (spe < my_spe)
                # 先後は追い風/こだわり等の未観測要因もあるためソフトに更新
                h["logw"] += math.log(1.0 if consistent else 0.2)
        elif kind == "damage":
            observed_pct = entry["pct"]
            for h in hyps:
                hyp_view = self._view(h, entry["opp"], gone=gone)
                if hyp_view is None:
                    continue
                try:
                    if entry["defender_is_hyp"]:
                        d = calc_damage(entry["attacker"], hyp_view, entry["move_id"], entry["fieldv"])
                    else:
                        d = calc_damage(hyp_view, entry["attacker"], entry["move_id"], entry["fieldv"])
                except Exception:
                    continue
                lo, hi = d["min"] - 3.0, d["max"] + 3.0
                if lo <= observed_pct <= hi:
                    like = 1.0
                else:
                    dist = (lo - observed_pct) if observed_pct < lo \
                        else (observed_pct - hi)
                    like = math.exp(-(dist / 6.0) ** 2) + 1e-6
                h["logw"] += math.log(like)
        elif kind == "choice_lock":
            # 3連続同一技はこだわり系のほぼ確定的な証拠 (変化技サイクル等の例外はあるが稀) なので強く更新する
            for h in hyps:
                if h["item"] and h["item"].startswith("choice"):
                    h["logw"] += math.log(4.0)
                else:
                    h["logw"] += math.log(0.1)
        elif kind == "item":
            iid = entry["item"]
            if not any((h["item"] or "") == iid for h in hyps):
                return   # 仮説に無い持ち物なら情報として使えない (全滅を避ける)
            for h in hyps:
                if (h["item"] or "") != iid:
                    h["logw"] += math.log(1e-6)

    def _replay(self, hyps: list) -> list:
        """記録した観測を新しい仮説に順に掛け直す (初判明の評価し直し)。記録に無い観測 (上限で捨てた分) の重みは流用しない"""
        replay_observations(hyps, getattr(self, "_obs_log", None) or [], self._apply_obs)
        return hyps

    def _invalidate_speed_range(self) -> None:
        self.spe_lower = None
        self.spe_upper = None

    def set_item_gone(self, gone: bool) -> None:
        """「現在は持ち物なし」を設定する (トラッカーが state の欄から決める。2026-10-31)。切り替わったら、以前の条件で得た
        実効素早さの範囲を捨てる (speed_range_stale。消費・喪失の後に古い下限・上限を流用しない)"""
        before = bool(self.item_gone)
        self.item_gone = bool(gone)
        if speed_range_stale(False, before, self.item_gone):
            self._invalidate_speed_range()

    # ------------------------------------------------------------------
    def observe_speed(self, opp_first: bool, my_effective_speed: float,
                      opp_state: dict, rain: bool = False) -> None:
        """同一優先度の技での先後観測。opp_first=Trueなら相手の実速が上"""
        if not self.hyps:
            return
        self.n_obs += 1
        # 実効素早さの範囲を狭める (表示・素早さ実数の把握用)
        if opp_first:
            self.spe_lower = max(self.spe_lower or 0, my_effective_speed)
        else:
            self.spe_upper = min(self.spe_upper or 9999, my_effective_speed)
        entry = self._record("speed", opp_first=bool(opp_first), my_spe=my_effective_speed,
                             opp=self._opp_snapshot(opp_state), rain=bool(rain))
        self._apply_obs(self.hyps, entry)

    def observe_item(self, item_id: Optional[str], add_missing: bool = False, change: Optional[bool] = None) -> None:
        """持ち物の判明 (発動/はたき落とし/表示) で、別の持ち物の仮説を実質除外する。

        add_missing=False (既定。シミュレータの探索 champions_agent/env/search_expert が毎ターン呼ぶ): 従来どおり、別の持ち物の仮説を
          ×1e-6 にする。仮説に無い持ち物なら何もしない (全滅を避ける)。
        add_missing=True (2026-10-30。サーバーのトラッカーが表示で確定した持ち物を個体ごとに一度だけ渡す): confirm_item_hyps で
          その持ち物の仮説だけを残し、仮説に無ければ配分ごとに持ち物を差し替えた仮説を足す (事前の重みは DB の採用率、一覧に無ければ
          config.EV_INFER_ITEM_FLOOR)。「現在は持ち物なし」(item_gone) はここでは変えない (消費した持ち物の観測もこの経路で渡すため。
          トラッカーが state の欄から決める)。
          2026-10-31: 初判明か実際の変更かで扱いを分ける (change: True=変更 / False=初判明 / None=推定器が item_reveal_kind で決める。
          トラッカーは None で渡し、推定器の _confirmed_item (トラッカーの _item_passed と同じ個体の記録) と item_gone で決まる)。
          - 初判明で候補にある: その仮説だけ残す (従来どおり)。
          - 初判明で候補に無い: 事前の配分 × 新しい持ち物 (prior_item_hyps) から作り直し、記録した観測を評価し直す
            (config.EV_INFER_REEVAL_ON_REVEAL が偽なら評価し直さず事前の重みのまま。どちらも以前の仮説の重みは流用しない)。
          - 実際の変更: 配分の事後を保って持ち物だけ切り替え (switch_item_hyps)、実効素早さの範囲を捨てる"""
        if not item_id or not self.hyps:
            return
        iid = _item_key(item_id)
        if add_missing:
            if change is None:
                kind = item_reveal_kind(getattr(self, "_confirmed_item", None), bool(self.item_gone), iid)
            else:
                kind = "change" if change else "first"
            if kind == "change":
                self.hyps = switch_item_hyps(self.hyps, iid)
                if speed_range_stale(True, bool(self.item_gone), bool(self.item_gone)):
                    self._invalidate_speed_range()
            elif any((h["item"] or "") == iid for h in self.hyps):
                self.hyps = confirm_item_hyps(self.hyps, iid, 1.0)
            else:
                self.hyps = self._reveal_outside(iid)
            self._confirmed_item = iid
            self.n_obs += 1
            return
        if not any((h["item"] or "") == iid for h in self.hyps):
            return   # 仮説に無い持ち物なら情報として使えない (全滅を避ける)
        self.n_obs += 1
        self._apply_obs(self.hyps, self._record("item", item=iid))

    def _reveal_outside(self, iid: str) -> list:
        """候補外の持ち物の初判明 (2026-10-31)。事前の仮説が無い (__init__ を通さずに作った) ときは従来の confirm_item_hyps"""
        from champions_agent import config as C
        prior = self._item_prior(iid)
        prior_hyps = getattr(self, "_prior_hyps", None)
        if not prior_hyps:
            return confirm_item_hyps(self.hyps, iid, prior)
        fresh = prior_item_hyps(prior_hyps, iid, prior)
        if bool(C.EV_INFER_REEVAL_ON_REVEAL):
            self._replay(fresh)
        return fresh

    def observe_choice_lock(self) -> None:
        """同一技の3連続使用を観測 -> こだわり系持ち物の仮説を強める"""
        if self._choice_locked or not self.hyps:
            return
        self._choice_locked = True
        self.n_obs += 1
        self._apply_obs(self.hyps, self._record("choice_lock"))

    def observe_damage(self, attacker: MonView, defender_is_hyp: bool,
                       move_id: str, observed_pct: float,
                       opp_state: dict, fieldv=None) -> None:
        """ダメージ観測。defender_is_hyp=True: 自分の技→相手 (耐久推定)、
        False: 相手の技→自分 (火力推定、attackerは仮説側で上書き)"""
        if not self.hyps or observed_pct <= 1.0:
            return
        self.n_obs += 1
        entry = self._record("damage", attacker=_snapshot_obj(attacker), defender_is_hyp=bool(defender_is_hyp),
                             move_id=move_id, pct=observed_pct, opp=self._opp_snapshot(opp_state),
                             fieldv=_snapshot_obj(fieldv))
        self._apply_obs(self.hyps, entry)

    # ------------------------------------------------------------------
    def best(self) -> Optional[dict]:
        """最有力仮説と確信度。観測がなければ事前分布の最上位"""
        if not self.hyps:
            return None
        mx = max(h["logw"] for h in self.hyps)
        total = sum(math.exp(h["logw"] - mx) for h in self.hyps)
        ranked = sorted(self.hyps, key=lambda h: -h["logw"])
        top = ranked[0]
        prob = math.exp(top["logw"] - mx) / total
        # 表示はチャンピオンズの能力ポイント (0-32, 合計66) 表記
        abbr = {"hp": "H", "atk": "A", "def": "B",
                "spa": "C", "spd": "D", "spe": "S"}
        pts = _ev_to_points(top["evs"])
        ev_txt = " ".join(f"{abbr[k]}{v}" for k, v in pts.items() if v)
        nature_ja = _NATURE_JA.get(top["nature"], top["nature"]) or "性格不明"
        # 消費・喪失の後は出力の持ち物を「なし」にする (engine に仮説の持ち物を入れさせない。2026-10-30)
        item_out = None if self.item_gone else top["item"]
        spe_note = ""
        if self.spe_lower or self.spe_upper:
            lo = f"{self.spe_lower:.0f}<" if self.spe_lower else ""
            hi = f"<{self.spe_upper:.0f}" if self.spe_upper else ""
            spe_note = f" 実効S{lo}S{hi}" if (lo or hi) else ""
        return {
            "nature": top["nature"],
            "nature_ja": nature_ja,
            "evs": top["evs"],
            "item": item_out,
            "prob": round(prob, 3),
            "n_obs": self.n_obs,
            "spe_lower": self.spe_lower,
            "spe_upper": self.spe_upper,
            "summary": f"{nature_ja}"
                       f" {ev_txt}"
                       + (f" @{item_out}" if item_out else (" 持ち物なし" if self.item_gone else ""))
                       + f" ({prob:.0%}, 観測{self.n_obs}件)"
                       + spe_note,
        }


    def top_k(self, k: int, min_weight: float = 0.0) -> list:
        """重み上位k仮説を [{"nature","evs","item","weight"}] で返す (P7)。

        weight は全仮説で正規化した事後確率 (上位kの和 = 被覆率)。
        min_weight 未満の仮説は、1つ以上返せていれば刈り込む。
        観測が無ければ使用率由来の事前分布そのもの。
        """
        if not self.hyps or k <= 0:
            return []
        mx = max(h["logw"] for h in self.hyps)
        ws = [math.exp(h["logw"] - mx) for h in self.hyps]
        total = sum(ws) or 1.0
        ranked = sorted(zip(self.hyps, ws), key=lambda pr: -pr[1])
        out = []
        for h, w in ranked[:k]:
            wn = w / total
            if wn < min_weight and out:
                break
            out.append({"nature": h["nature"], "evs": h["evs"],
                        "item": None if self.item_gone else h["item"], "weight": round(wn, 4)})
        return out

    def speed_estimate(self, opp_state: Optional[dict] = None) -> Optional[dict]:
        """最良仮説の実効素早さを観測レンジでクランプした推定値。

        戻り値: {"est": int, "lo": float|None, "hi": float|None, "n_obs": int}
        (フロントエンド表示とRLの素早さ比較に使う)
        """
        if not self.hyps:
            return None
        top = max(self.hyps, key=lambda h: h["logw"])
        v = self._view(top, opp_state or {})
        if v is None:
            return None
        v.status = (opp_state or {}).get("status")
        from advisor.damage import effective_speed
        est = effective_speed(v)
        if self.spe_lower:
            est = max(est, int(self.spe_lower) + 1)
        if self.spe_upper:
            est = min(est, max(1, int(self.spe_upper) - 1))
        return {"est": int(est), "lo": self.spe_lower, "hi": self.spe_upper,
                "n_obs": self.n_obs}


def transform_masked(opp: Optional[dict], opp_move_id: Optional[str] = None) -> bool:
    """相手の場の個体の素早さ・ダメージの観測を、その個体の種族の推定器に入れてはいけないか (純粋。2026-10-23 へんしん の残件)。

    へんしん (メタモン・かわりもの) 中の個体の 攻撃・防御・特攻・特防・素早さ と技はコピー元 (こちらの個体) のもので、相手の種族の
    型 (性格・努力値) の仮説では説明できない (HP と持ち物だけが相手のまま)。コピー元 = こちらの個体の値は登録で分かっていて、推定器
    (相手の種族ごとの型の仮説) に帰属させる先が無いので、観測を捨てる。対象は transform の付いた個体と、へんしん する種
    (config TRANSFORM_SPECIES) の個体 (観測根拠がまだ揃わず transform が付いていないフレームでも写しのことがある)。
    例外: 先後の観測で、相手の技が へんしん する前の自分の技 (config TRANSFORM_OWN_MOVES。へんしん そのもの) のときは、その
    ターンの素早さは相手の個体自身のもの (opp_move_id で渡す)。こだわりの固定 (observe_choice_lock) は持ち物 = 相手のままなので
    ここでは捨てない"""
    from champions_agent import config as C
    if not opp:
        return False
    if not (opp.get("transform") or opp.get("species_id") in tuple(C.TRANSFORM_SPECIES)):
        return False
    return not (opp_move_id and opp_move_id in tuple(C.TRANSFORM_OWN_MOVES))


class SpreadTracker:
    """フレームストリームから観測を抽出し、種族ごとの推定器を更新する。

    battle_loggerと同様にサーバーの毎フレーム処理から on_frame(state, fired)
    で呼ばれる。技イベントとHP変化イベント (state.events) を時系列で対応付ける。

    推定器 (_est) はデータの版 (data_version: 使用率 DB のパス + snapshot id + pinned) ごと (2026-10-30)。版が変わったら
    (再生の data_pin.set_override 等) 作り直す。data_pin.reset_caches() は drop_estimators() を呼ぶ。
    """

    def __init__(self):
        self._version = None
        self.reset()

    def reset(self):
        self._est: dict = {}          # species_id -> SpreadEstimator
        self._last_move = {"player": None, "opponent": None}  # (ts, move_id, turn)
        self._hp_seen_ts = 0.0
        self._order_turn = None       # 先後を判定済みのターン
        self._prev_scene = None
        self._opp_streak = {}         # species_id -> (move_id, 連続回数)
        # 型推定に渡した持ち物の観測 (相手の個体 (species_id, 枠) -> 持ち物 id。2026-10-30)。同じ観測を毎フレーム掛けない
        self._item_passed: dict = {}

    def drop_estimators(self) -> None:
        """推定器と、推定器に渡した持ち物の観測の記録を捨てる (データの版が変わったとき。対戦の途中の観測の追跡は残す)"""
        self._est = {}
        self._item_passed = {}
        self._version = None

    def _sync_version(self) -> None:
        v = data_version()
        if v != self._version:
            if self._version is not None:
                self._est = {}
                self._item_passed = {}
            self._version = v

    def estimator(self, species_id: str) -> SpreadEstimator:
        self._sync_version()
        if species_id not in self._est:
            self._est[species_id] = SpreadEstimator(species_id)
        return self._est[species_id]

    def best_for(self, species_id: Optional[str]) -> Optional[dict]:
        self._sync_version()
        est = self._est.get(species_id)
        return est.best() if est else None

    def hypotheses_for(self, species_id: Optional[str], k: int,
                       min_weight: float = 0.0) -> list:
        """多世界探索 (P7) 用: 種族の重み上位k仮説。推定器が無ければ
        事前分布 (使用率由来) の推定器を作って返す"""
        if not species_id:
            return []
        return self.estimator(species_id).top_k(k, min_weight)

    # ------------------------------------------------------------------
    def on_frame(self, state: dict, fired: list) -> None:
        from advisor.data_pin import InputsPinError
        try:
            self._on_frame(state, fired)
        except InputsPinError:
            # 明示した版のデータが無い: 握りつぶさない (最新へ退避しない)。起動時の検査
            # (tools.advice_mode.data_pin_startup_check → data_pin.verify_spec) が同じ DB・snapshot を確かめるので、通常は起きない
            raise
        except Exception:
            pass   # 推定は補助機能。失敗しても本体を止めない

    def _active(self, state: dict, side: str) -> Optional[dict]:
        s = state[side]
        idx = s.get("active_index")
        if idx is None or idx >= len(s.get("party", [])):
            return None
        return s["party"][idx]

    def _on_frame(self, state: dict, fired: list) -> None:
        # 新しい対戦でリセット
        if state.get("scene") == "selection" and self._prev_scene not in (
                "selection", None):
            self.reset()
        self._prev_scene = state.get("scene")

        # 選出画面: 相手6体の仮説を事前構築しておく (対戦開始直後から
        # 事前分布ベースの型表示ができる)
        if state.get("scene") == "selection":
            for p in state["opponent"].get("party", []):
                if p.get("species_id"):
                    self.estimator(p["species_id"])

        # 持ち物の判明・消費・喪失 (2026-10-30)
        self._observe_items(state, fired)

        now = time.time()
        turn = state.get("turn")
        for f in fired:
            for side in ("player", "opponent"):
                if f.startswith(f"move_{side}_"):
                    self._last_move[side] = (now, f.split("_", 2)[2], turn)

        # こだわりロック検知: 同一ポケモンが同一技を3連続 (交代で解除)
        for f in fired:
            if f.startswith("move_opponent_"):
                opp = self._active(state, "opponent")
                if opp and opp.get("species_id"):
                    sid = opp["species_id"]
                    mid = f.split("_", 2)[2]
                    prev_mid, streak = self._opp_streak.get(sid, (None, 0))
                    streak = streak + 1 if mid == prev_mid else 1
                    self._opp_streak[sid] = (mid, streak)
                    if streak >= 3:
                        self.estimator(sid).observe_choice_lock()
            elif f == "switch_opponent":
                self._opp_streak.clear()

        # --- 先後観測: 同一ターンに両者の技が揃ったら1回だけ判定 ---
        pm, om = self._last_move["player"], self._last_move["opponent"]
        if (pm and om and pm[2] == om[2] and pm[2] is not None
                and pm[2] != self._order_turn):
            self._observe_order(state, pm, om)
            self._order_turn = pm[2]

        # --- ダメージ観測: HP変化イベントを直前の技と対応付ける ---
        for e in state.get("events", []):
            if e.get("source") != "hp" or e.get("ts", 0) <= self._hp_seen_ts:
                continue
            self._hp_seen_ts = e["ts"]
            det = e.get("detail") or {}
            delta = (det.get("from") or 0) - (det.get("to") or 0)
            if delta <= 1.0:
                continue   # 回復や増加は扱わない
            self._observe_hp_drop(state, det.get("side"), delta, e["ts"])

    # ------------------------------------------------------------------
    def _observe_items(self, state: dict, fired: list) -> None:
        """相手の個体の持ち物の欄 (item_observation) を、個体 (species_id, 枠) ごとに一度だけ型推定に渡す。
        - 持ち物が初めて判明した (または はたきおとす・トリック 等で前に渡した物と違う物になった) とき observe_item(..., add_missing=True) を 1 回
        - 消費 (item_consumed): その持ち物の観測 (まだ渡していなければ) + 現在は持ち物なし (推定器の item_gone)
        - 喪失 (item_removed): 失った物が分かれば (このフレームの knockoff_opponent_<id> を場の個体に帰属) その観測 + 現在は持ち物なし
        「現在は持ち物なし」は仮説の持ち物を変えない (出力と以後の計算だけ)。
        2026-10-31: 持ち物の変更・消費・喪失で、以前の実効素早さの範囲を捨てる (observe_item の変更 / set_item_gone)"""
        opp = state.get("opponent") or {}
        party = opp.get("party") or []
        act = opp.get("active_index")
        knocked = knocked_off_items(fired, "opponent")
        for idx, p in enumerate(party):
            if not isinstance(p, dict) or not p.get("species_id"):
                continue
            sid = p["species_id"]
            held, gone = item_observation(p)
            if held is None and gone and p.get("item_removed") and idx == act and knocked:
                held = knocked[-1]
            if not held and not gone:
                continue
            key = (sid, idx)
            est = self.estimator(sid)
            new = item_to_pass(self._item_passed.get(key), held)
            if new:
                # 初判明か実際の変更かは推定器が決める (item_reveal_kind: 渡し済みの持ち物 = この記録と同じ、直前の持ち物なし)
                est.observe_item(new, add_missing=True)
                self._item_passed[key] = new
            est.set_item_gone(gone)

    def _my_view(self, state: dict):
        from advisor.engine import build_mon_view
        me = self._active(state, "player")
        if not me:
            return None, None
        return build_mon_view(me, None, side="player"), me

    def _observe_order(self, state: dict, pm, om) -> None:
        dex = get_dex()
        mv_p, mv_o = dex.move(pm[1]), dex.move(om[1])
        if not mv_p or not mv_o:
            return
        if mv_p.get("priority", 0) != mv_o.get("priority", 0):
            return   # 優先度が違うと素早さの情報にならない
        if state.get("field", {}).get("trick_room"):
            return
        opp = self._active(state, "opponent")
        my_view, me = self._my_view(state)
        if not opp or not opp.get("species_id") or my_view is None:
            return
        # へんしん 中の素早さはコピー元のもの (transform_masked。2026-10-23)
        if transform_masked(opp, om[1]):
            return
        # 特性による優先度変化の可能性がある行動は先後観測に使わない
        # (実戦: いたずらごころヤミラミのおにび先制を「実速が上」と誤学習し
        #  推定素早さが大きく狂った)。可能特性ベースで保守的に弾く
        def _pri_ability_noise(p_dict, mv) -> bool:
            try:
                from advisor.rl_bridge import _possible_abilities_dict
                poss = _possible_abilities_dict(p_dict)
            except Exception:
                return False
            cat = (mv.get("category") or "").lower()
            mtype = (mv.get("type") or "").lower()
            if "prankster" in poss and cat == "status":
                return True   # いたずらごころ: 変化技+1
            if "galewings" in poss and mtype == "flying":
                return True   # はやてのつばさ: ひこう技+1 (満タン時)
            if "triage" in poss and (mv.get("heal") or mv.get("drain")):
                return True   # ヒーリングシフト
            return False
        if _pri_ability_noise(opp, mv_o) or _pri_ability_noise(me, mv_p):
            return
        from advisor.damage import FieldView, effective_speed
        f = state.get("field", {})
        my_spe = effective_speed(my_view, FieldView(
            weather=f.get("weather"), terrain=f.get("terrain")))
        rain = f.get("weather") == "rain"
        opp_first = om[0] < pm[0]
        self.estimator(opp["species_id"]).observe_speed(
            opp_first, my_spe, opp, rain=rain)

    def _observe_hp_drop(self, state: dict, side: Optional[str],
                         delta: float, ts: float) -> None:
        if side == "opponent":
            attacker_move = self._last_move["player"]
            opp = self._active(state, "opponent")
            my_view, _me = self._my_view(state)
            if (not attacker_move or ts - attacker_move[0] > 12.0
                    or not opp or not opp.get("species_id")
                    or my_view is None):
                return
            # へんしん 中の防御・特防はコピー元のもの (transform_masked。2026-10-23)
            if transform_masked(opp):
                return
            self.estimator(opp["species_id"]).observe_damage(
                my_view, True, attacker_move[1], delta, opp)
        elif side == "player":
            attacker_move = self._last_move["opponent"]
            opp = self._active(state, "opponent")
            my_view, _me = self._my_view(state)
            if (not attacker_move or ts - attacker_move[0] > 12.0
                    or not opp or not opp.get("species_id")
                    or my_view is None):
                return
            # へんしん 中の攻撃・特攻と技はコピー元のもの (transform_masked。2026-10-23)
            if transform_masked(opp, attacker_move[1]):
                return
            self.estimator(opp["species_id"]).observe_damage(
                my_view, False, attacker_move[1], delta, opp)


_tracker: Optional[SpreadTracker] = None


def get_tracker() -> SpreadTracker:
    global _tracker
    if _tracker is None:
        _tracker = SpreadTracker()
    return _tracker
