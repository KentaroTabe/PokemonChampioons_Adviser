# 辞書: キーワードによるイベント定義
# 画数が多い漢字は潰れやすくOCRで誤読されるため、ひらがなや確実なパーツのみに緩和しています
EVENT_DICTIONARY = {
    "weather_events": {
        "sandstorm_start": {
            # "吹き始めた" の語尾が "はめた" "めた" と誤読されるのを利用して区別
            "keywords": ["あらし", "めた"], 
            "action": "set_weather",
            "value": "sandstorm",
            "duration": 5
        },
        "rain_start": {
            "keywords": ["雨", "降り出し"],
            "action": "set_weather",
            "value": "rain",
            "duration": 5
        }
    },
    "status_events": {
        "substitute_on": {
            "keywords": ["身代わり"], # 画数が多く潰れやすい「現れ」を条件から除外
            "action": "set_status",
            "status_key": "substitute",
            "value": True
        },
        "confusion_on": {
            "keywords": ["混乱"],
            "action": "set_status",
            "status_key": "confusion",
            "value": True
        }
    },
    "damage_events": {
         "sandstorm_damage": {
             # 「襲う」が「た'う」等に誤読されるのを利用
             "keywords": ["あらし", "う"], 
             "action": "log_damage",
             "source": "sandstorm"
         }
    },
    "action_events": {
         "attack": {
             "keywords": ["襲う"],
             "action": "log_attack"
         }
    },
    "item_events": {
         "leftovers": {
             # 「べ/ぺ」のOCR誤認を避けるため、確実な後半部分だけをキーワードにする
             "keywords": ["のこし"],
             "action": "log_item",
             "item_name": "leftovers"
         }
    }
}

# ==============================================================================
# バトル状態管理クラス
# ==============================================================================
class BattleState:
    def __init__(self):
        self.field = {
            "weather": "none",
            "weather_turns_left": 0
        }
        self.player = {
            "display_name": None, # 画面に表示されている名前(OCR)
            "species": None,      # 画像マッチングで特定した種族ID(例: 1018)
            "hp_percent": None,
            "hp_raw": None,
            "substitute": False,
            "confusion": False
        }
        self.opponent = {
            "display_name": None,
            "species": None,
            "hp_percent": None,
            "substitute": False,
            "confusion": False
        }
        self.last_message = "" # 重複排除用
        self.last_left_popup = "" # 左ポップアップ重複排除用
        self.last_right_popup = "" # 右ポップアップ重複排除用
    
    def update_basic_info(self, info):
        if "my_display_name" in info:
            self.player["display_name"] = info["my_display_name"]
        if "my_species" in info:
            self.player["species"] = info["my_species"]
        if "my_hp_percent" in info:
            self.player["hp_percent"] = info["my_hp_percent"]
        if "my_hp_raw" in info:
            self.player["hp_raw"] = info["my_hp_raw"]
            
        if "opp_display_name" in info:
            self.opponent["display_name"] = info["opp_display_name"]
        if "opp_species" in info:
            self.opponent["species"] = info["opp_species"]
        if "opponent_hp_percent" in info:
            self.opponent["hp_percent"] = info["opponent_hp_percent"]

    def apply_event(self, event_def, raw_text, source_type="message"):
        action = event_def.get("action")
        
        # ターゲットの確実な推定
        if source_type == "right_popup":
            target = "opponent"
        elif source_type == "left_popup":
            target = "player"
        else:
            # メッセージウィンドウの場合は【抽出した表示名】から推論する（最強の安定度）
            if self.opponent["display_name"] and self.opponent["display_name"] in raw_text:
                target = "opponent"
            elif self.player["display_name"] and self.player["display_name"] in raw_text:
                target = "player"
            else:
                # 名前が読めなかった場合のフォールバック
                is_opponent = any(k in raw_text for k in ["相手", "間手", "帽手", "相羊"])
                target = "opponent" if is_opponent else "player"

        if action == "set_weather":
            self.field["weather"] = event_def["value"]
            self.field["weather_turns_left"] = event_def["duration"]
            print(f"[State Update] 天候が {event_def['value']} になりました。")
            
        elif action == "set_status":
            status_key = event_def["status_key"]
            value = event_def["value"]
            if target == "opponent":
                self.opponent[status_key] = value
                print(f"[State Update] 相手の {status_key} が {value} になりました。")
            else:
                self.player[status_key] = value
                print(f"[State Update] 自分の {status_key} が {value} になりました。")
                
        elif action == "log_damage":
            print(f"[State Update] スリップダメージ処理: {target} が {event_def.get('source')} のダメージを受けました。")
            
        elif action == "log_attack":
            print(f"[State Update] 攻撃アクションを検知: {raw_text}")

        elif action == "log_item":
            item_name = event_def.get("item_name")
            print(f"[State Update] {target} のアイテム発動を検知: {item_name}")

    def print_state(self):
        print("--- Current Battle State ---")
        print(f"Field: {self.field}")
        print(f"Player: {self.player}")
        print(f"Opponent: {self.opponent}")
        print("----------------------------")
        
    def to_dict(self):
         return {
             "field": self.field,
             "player": self.player,
             "opponent": self.opponent
         }

# グローバルなバトル状態
battle_state = BattleState()