> 配置の注記 (2026-10-31、開発者判断 (b)): この文書は委託先 (実装担当のサブエージェント) が作業ツリー側に出力した report.md の完全版。§1〜§7 は道具の出力で `logs/inheritance_eval/phaseA_20261031/I-01/report.md` と同じ内容、§8〜§11 (重大な不一致・最小対応の割合・現行にしか無い機能・判定) は委託先が書いた解釈で、本体の logs/ への追記が権限設定で拒否されたためここに置く。測定の出力は変更していない。

# I-01 日本語辞書・型登録・名前／単位／形式の変換 (段階 A、phaseA_20261031)

- 生成: tools/inheritance_eval/conversion.py (計算時間 12.55 秒 / 上限 1500 秒)
- 対象規制: gen9championsbssregmc (Showdown d849b2200、mod champions)
- 区間は Wilson 95% (件数が少ないので目安。走査は全数なので区間を付けない)

## 1. 経路ごとの結果

| 資産 | 経路 | pass (うち表記の差) | fail | excluded | timeout | fail の内訳 |
|---|---|---|---|---|---|---|
| S fixture | current | 61 (1) | 18 | 1 | 0 | 曖昧の黙殺 8, 欠落 1, 表示ラベル 1, 補完 1, 誤変換 7 |
| S fixture | minimal | 57 (0) | 20 | 1 | 0 | 曖昧の黙殺 3, 欠落 16, 非対応 1 |
| S fixture | minimal+stone | 8 (0) | 6 | 0 | 0 | 欠落 6 |
| 名前の表 | - | 1592 (0) | 282 | 823 | 0 | 欠落 241, 誤変換 41 |
| 登録構築 (config/my_team.json) | current | 240 (3) | 0 | 6 | 0 |  |
| 登録構築 (config/my_team.json) | current-human | 0 (0) | 30 | 3 | 0 | 誤変換 30 |
| 登録構築 (config/my_team.json) | minimal | 205 (1) | 5 | 3 | 0 | 非対応 5 |

## 2. 全走査 (id → 日本語 → id の往復と Showdown ja との突き合わせ)

| 範囲 | 種類 | n | 往復一致 | 欠落 | 誤変換 | 除外 (非参戦) | 名前の関係 (現行 vs Showdown ja) | M の往復 | M+石 の往復 | mod に無い id |
|---|---|---|---|---|---|---|---|---|---|---|
| dex | moves | 416 | 398 (95.7%) | 18 | 0 | 0 | 一致 377, 現行のみ 19, 両方なし 18, 表記の差 2 | ok 379, unknown 37 | - | 36 |
| dex | species | 1053 | 2 (0.2%) | 191 | 37 | 823 | 一致 775, 両方なし 165, 現行のみ 106, Showdownのみ 5, 表記の差 2 | ok 782, unknown 271 | - | 50 |
| regmc | abilities | 215 | 215 (100.0%) | 0 | 0 | 0 | 一致 214, 現行のみ 1 | ok 214, unknown 1 | - | 0 |
| regmc | items | 164 | 163 (99.4%) | 1 | 0 | 0 | 一致 82, 現行のみ 81, Showdownのみ 1 | ok 82, unknown 81, ambiguous 1 | ok 161, unknown 2, ambiguous 1 | 0 |
| regmc | moves | 503 | 503 (100.0%) | 0 | 0 | 0 | 一致 503 | ok 503 | - | 0 |
| regmc | species | 346 | 311 (89.9%) | 31 | 4 | 0 | 一致 265, 現行のみ 72, 両方なし 7, 表記の差 2 | ok 267, unknown 79 | - | 0 |

## 3. 誤変換 (severity = 誤変換 の fail) の一覧

| 資産 | case | 経路 | 欄 | 理由 | split |
|---|---|---|---|---|---|
| S fixture | S-form-14 | current | - | 候補外の meowsticfemalemega を返した | confirm |
| S fixture | S-unk-06 | current | - | 存在しない名前を garchompite に置き換えた | confirm |
| S fixture | S-unit-06 | current | - | 範囲外・単位違いの値を受理した: {'atk': 33} | confirm |
| S fixture | S-unit-08 | current | - | 範囲外・単位違いの値を受理した: {'hp': 32, 'atk': 252, 'spe': 252} | confirm |
| S fixture | S-unit-09 | current | - | 範囲外・単位違いの値を受理した: {'atk': 252, 'def': 4, 'spe': 252} | confirm |
| S fixture | S-unit-11 | current | - | EVs 欄 {'hp': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'hp': 2, 'spa': 32, 'spe': 32}); Showdown の検証: Charizard has more than 32 Stat Points in Special Attack. | confirm |
| S fixture | S-unit-14 | current | - | {'hp': 252} を {'hp': 32} に黙って丸めた | diag |
| 登録構築 (config/my_team.json) | REG-00 | current-human | points | EVs 欄 {'def': 160, 'hp': 252, 'spd': 112} (正しくは能力ポイント {'def': 20, 'hp': 32, 'spd': 14}); Pelipper has more than 32 Stat Points in HP.; Pelipper has more than 32 | diag |
| 登録構築 (config/my_team.json) | REG-01 | current-human | points | EVs 欄 {'atk': 252, 'hp': 64, 'spe': 208} (正しくは能力ポイント {'atk': 32, 'hp': 8, 'spe': 26}); Swampert has more than 32 Stat Points in HP.; Swampert has more than 32 S | diag |
| 登録構築 (config/my_team.json) | REG-02 | current-human | points | EVs 欄 {'atk': 252, 'def': 8, 'hp': 8, 'spe': 252} (正しくは能力ポイント {'atk': 32, 'def': 1, 'hp': 1, 'spe': 32}); Mimikyu has more than 32 Stat Points in Attack.; Mimik | diag |
| 登録構築 (config/my_team.json) | REG-03 | current-human | points | EVs 欄 {'hp': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'hp': 2, 'spa': 32, 'spe': 32}); Archaludon has more than 32 Stat Points in Special Attack.; Archaludon ha | diag |
| 登録構築 (config/my_team.json) | REG-04 | current-human | points | EVs 欄 {'hp': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'hp': 2, 'spa': 32, 'spe': 32}); Rotom has more than 32 Stat Points in Special Attack.; Rotom has more tha | diag |
| 登録構築 (config/my_team.json) | REG-05 | current-human | points | EVs 欄 {'hp': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'hp': 2, 'spa': 32, 'spe': 32}); Raichu has more than 32 Stat Points in Special Attack.; Raichu has more t | diag |
| 登録構築 (config/my_team.json) | REG-06 | current-human | points | EVs 欄 {'def': 252, 'hp': 252, 'spd': 16} (正しくは能力ポイント {'def': 32, 'hp': 32, 'spd': 2}); Grimmsnarl has more than 32 Stat Points in HP.; Grimmsnarl has more than  | diag |
| 登録構築 (config/my_team.json) | REG-07 | current-human | points | EVs 欄 {'hp': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'hp': 2, 'spa': 32, 'spe': 32}); Dragonite has more than 32 Stat Points in Special Attack.; Dragonite has  | diag |
| 登録構築 (config/my_team.json) | REG-09 | current-human | points | EVs 欄 {'def': 16, 'hp': 252, 'spd': 252} (正しくは能力ポイント {'def': 2, 'hp': 32, 'spd': 32}); Hippowdon has more than 32 Stat Points in HP.; Hippowdon has more than 32 | diag |
| 登録構築 (config/my_team.json) | REG-10 | current-human | points | EVs 欄 {'def': 252, 'hp': 252, 'spd': 16} (正しくは能力ポイント {'def': 32, 'hp': 32, 'spd': 2}); Bellibolt has more than 32 Stat Points in HP.; Bellibolt has more than 32 | diag |
| 登録構築 (config/my_team.json) | REG-11 | current-human | points | EVs 欄 {'atk': 252, 'hp': 8, 'spd': 8, 'spe': 252} (正しくは能力ポイント {'atk': 32, 'hp': 1, 'spd': 1, 'spe': 32}); Staraptor has more than 32 Stat Points in Attack.; Sta | diag |
| 登録構築 (config/my_team.json) | REG-12 | current-human | points | EVs 欄 {'hp': 80, 'spa': 252, 'spe': 192} (正しくは能力ポイント {'hp': 10, 'spa': 32, 'spe': 24}); Greninja has more than 32 Stat Points in HP.; Greninja has more than 32  | diag |
| 登録構築 (config/my_team.json) | REG-13 | current-human | points | EVs 欄 {'atk': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'atk': 2, 'spa': 32, 'spe': 32}); Garchomp has more than 32 Stat Points in Special Attack.; Garchomp has  | diag |
| 登録構築 (config/my_team.json) | REG-14 | current-human | points | EVs 欄 {'atk': 128, 'hp': 176, 'spe': 224} (正しくは能力ポイント {'atk': 16, 'hp': 22, 'spe': 28}); Metagross has more than 32 Stat Points in HP.; Metagross has more than  | diag |
| 登録構築 (config/my_team.json) | REG-15 | current-human | points | EVs 欄 {'def': 200, 'hp': 252, 'spd': 72} (正しくは能力ポイント {'def': 25, 'hp': 32, 'spd': 9}); Slurpuff has more than 32 Stat Points in HP.; Slurpuff has more than 32 S | diag |
| 登録構築 (config/my_team.json) | REG-16 | current-human | points | EVs 欄 {'hp': 8, 'spa': 252, 'spd': 8, 'spe': 252} (正しくは能力ポイント {'hp': 1, 'spa': 32, 'spd': 1, 'spe': 32}); Glimmora has more than 32 Stat Points in Special Attac | diag |
| 登録構築 (config/my_team.json) | REG-18 | current-human | points | EVs 欄 {'hp': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'hp': 2, 'spa': 32, 'spe': 32}); Hydreigon has more than 32 Stat Points in Special Attack.; Hydreigon has  | diag |
| 登録構築 (config/my_team.json) | REG-19 | current-human | points | EVs 欄 {'atk': 252, 'hp': 16, 'spe': 252} (正しくは能力ポイント {'atk': 32, 'hp': 2, 'spe': 32}); Meowscarada has more than 32 Stat Points in Attack.; Meowscarada has more | diag |
| 登録構築 (config/my_team.json) | REG-20 | current-human | points | EVs 欄 {'def': 252, 'hp': 252, 'spa': 16} (正しくは能力ポイント {'def': 32, 'hp': 32, 'spa': 2}); Primarina has more than 32 Stat Points in HP.; Primarina has more than 32 | diag |
| 登録構築 (config/my_team.json) | REG-22 | current-human | points | EVs 欄 {'atk': 252, 'hp': 252, 'spe': 16} (正しくは能力ポイント {'atk': 32, 'hp': 32, 'spe': 2}); Kingambit has more than 32 Stat Points in HP.; Kingambit has more than 32 | diag |
| 登録構築 (config/my_team.json) | REG-23 | current-human | points | EVs 欄 {'atk': 252, 'def': 16, 'spe': 252} (正しくは能力ポイント {'atk': 32, 'def': 2, 'spe': 32}); Basculegion has more than 32 Stat Points in Attack.; Basculegion has mo | diag |
| 登録構築 (config/my_team.json) | REG-24 | current-human | points | EVs 欄 {'atk': 252, 'hp': 16, 'spe': 252} (正しくは能力ポイント {'atk': 32, 'hp': 2, 'spe': 32}); Cinderace has more than 32 Stat Points in Attack.; Cinderace has more tha | diag |
| 登録構築 (config/my_team.json) | REG-25 | current-human | points | EVs 欄 {'atk': 252, 'hp': 16, 'spe': 252} (正しくは能力ポイント {'atk': 32, 'hp': 2, 'spe': 32}); Gallade has more than 32 Stat Points in Attack.; Gallade has more than 32 | diag |
| 登録構築 (config/my_team.json) | REG-26 | current-human | points | EVs 欄 {'hp': 16, 'spa': 252, 'spe': 252} (正しくは能力ポイント {'hp': 2, 'spa': 32, 'spe': 32}); Charizard has more than 32 Stat Points in Special Attack.; Charizard has  | diag |
| 登録構築 (config/my_team.json) | REG-27 | current-human | points | EVs 欄 {'atk': 252, 'def': 200, 'hp': 32, 'spe': 40} (正しくは能力ポイント {'atk': 32, 'def': 25, 'hp': 4, 'spe': 5}); Sneasler has more than 32 Stat Points in Attack.; Sn | diag |
| 登録構築 (config/my_team.json) | REG-28 | current-human | points | EVs 欄 {'atk': 252, 'hp': 8, 'spe': 252} (正しくは能力ポイント {'atk': 32, 'hp': 1, 'spe': 32}); Lopunny has more than 32 Stat Points in Attack.; Lopunny has more than 32  | diag |
| 登録構築 (config/my_team.json) | REG-29 | current-human | points | EVs 欄 {'def': 8, 'hp': 136, 'spa': 128, 'spe': 252} (正しくは能力ポイント {'def': 1, 'hp': 17, 'spa': 16, 'spe': 32}); Blastoise has more than 32 Stat Points in HP.; Blas | diag |
| 登録構築 (config/my_team.json) | REG-30 | current-human | points | EVs 欄 {'def': 184, 'hp': 16, 'spa': 120, 'spe': 208} (正しくは能力ポイント {'def': 23, 'hp': 2, 'spa': 15, 'spe': 26}); Indeedee has more than 32 Stat Points in Defense.; | diag |
| 登録構築 (config/my_team.json) | REG-31 | current-human | points | EVs 欄 {'hp': 8, 'spa': 252, 'spd': 8, 'spe': 252} (正しくは能力ポイント {'hp': 1, 'spa': 32, 'spd': 1, 'spe': 32}); Armarouge has more than 32 Stat Points in Special Atta | diag |
| 登録構築 (config/my_team.json) | REG-32 | current-human | points | EVs 欄 {'atk': 40, 'hp': 248, 'spd': 120, 'spe': 120} (正しくは能力ポイント {'atk': 5, 'hp': 31, 'spd': 15, 'spe': 15}); Salamence has more than 32 Stat Points in HP.; Sal | diag |
| 名前の表 | SCAN-regmc-species:basculegionf | - | species | イダイトウ(メス) → basculegion (元は basculegionf) | confirm |
| 名前の表 | SCAN-regmc-species:decidueyehisui | - | species | ヒスイジュナイパー → decidueye (元は decidueyehisui) | confirm |
| 名前の表 | SCAN-regmc-species:indeedeef | - | species | イエッサン(メス) → indeedee (元は indeedeef) | confirm |
| 名前の表 | SCAN-regmc-species:meowsticf | - | species | ニャオニクス(メス) → meowstic (元は meowsticf) | confirm |
| 名前の表 | SCAN-dex-species:aron | - | species | ココドラ → gogoat (元は aron、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:basculegionfemale | - | species | イダイトウ(メス) → basculegion (元は basculegionfemale) | diag |
| 名前の表 | SCAN-dex-species:basculegionmale | - | species | イダイトウ(オス) → basculegion (元は basculegionmale) | diag |
| 名前の表 | SCAN-dex-species:chansey | - | species | ラッキー → umbreon (元は chansey、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:charmeleon | - | species | リザード → charizard (元は charmeleon、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:chespin | - | species | ハリマロン → overqwil (元は chespin、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:crabrawler | - | species | マケンカニ → crabominable (元は crabrawler、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:dewpider | - | species | シズクモ → araquanid (元は dewpider、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:floette | - | species | フラエッテ → floettemega (元は floette、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:gastly | - | species | ゴース → torkoal (元は gastly、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:graveler | - | species | ゴローン → pangoro (元は graveler、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:heatranmega | - | species | メガヒードラン → slowbromega (元は heatranmega、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:indeedeefemale | - | species | イエッサン(メス) → indeedee (元は indeedeefemale) | diag |
| 名前の表 | SCAN-dex-species:indeedeemale | - | species | イエッサン(オス) → indeedee (元は indeedeemale) | diag |
| 名前の表 | SCAN-dex-species:jigglypuff | - | species | プリン → wigglytuff (元は jigglypuff、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:krokorok | - | species | ワルビル → krookodile (元は krokorok、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:magearna | - | species | マギアナ → magearnaoriginalmega (元は magearna、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:magearnamega | - | species | メガマギアナ → magearnaoriginalmega (元は magearnamega) | diag |
| 名前の表 | SCAN-dex-species:mareanie | - | species | ヒドイデ → toxapex (元は mareanie、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:marill | - | species | マリル → azumarill (元は marill、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:meowsticfemale | - | species | ニャオニクス(メス) → meowstic (元は meowsticfemale) | diag |
| 名前の表 | SCAN-dex-species:meowsticmale | - | species | ニャオニクス(オス) → meowstic (元は meowsticmale) | diag |
| 名前の表 | SCAN-dex-species:monferno | - | species | モウカザル → infernape (元は monferno、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:nidoking | - | species | ニドキング → slowking (元は nidoking、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:pawmo | - | species | パモット → pawmot (元は pawmo、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:pyroarmale | - | species | カエンジシ(オス) → pyroar (元は pyroarmale) | diag |
| 名前の表 | SCAN-dex-species:quaxwell | - | species | ウェルカモ → volcarona (元は quaxwell、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:rhydon | - | species | サイドン → rhyperior (元は rhydon、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:sinistea | - | species | ヤバチャ → sinistcha (元は sinistea、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:slowpoke | - | species | ヤドン → slowbro (元は slowpoke、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:sneasel | - | species | ニューラ → weavile (元は sneasel、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:tandemaus | - | species | ワッカネズミ → maushold (元は tandemaus、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:tinkatink | - | species | カヌチャン → tinkaton (元は tinkatink、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:tinkatuff | - | species | ナカヌチャン → tinkaton (元は tinkatuff、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:tyrunt | - | species | チゴラス → tyrantrum (元は tyrunt、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:zapdos | - | species | サンダー → jolteon (元は zapdos、非参戦の名前を別種へ置換) | diag |
| 名前の表 | SCAN-dex-species:zorua | - | species | ゾロア → zoroark (元は zorua、非参戦の名前を別種へ置換) | diag |

## 4. 曖昧の黙殺 (決まらない入力を 1 つに決めた)

- S-form-08 (current): 候補 ['rotom', 'rotomfan', 'rotomfrost', 'rotomheat', 'rotommow', 'rotomwash'] から rotom に決めた
- S-form-08 (minimal): 候補 ['rotom', 'rotomfan', 'rotomfrost', 'rotomheat', 'rotommow', 'rotomwash'] から rotom に決めた
- S-form-09 (current): 候補 ['basculegion', 'basculegionfemale'] から basculegion に決めた
- S-form-10 (current): 候補 ['indeedee', 'indeedeefemale'] から indeedee に決めた
- S-form-11 (current): 候補 ['meowstic', 'meowsticfemale'] から meowstic に決めた
- S-form-12 (current): 候補 ['ninetales', 'ninetalesalola'] から ninetales に決めた
- S-form-12 (minimal): 候補 ['ninetales', 'ninetalesalola'] から ninetales に決めた
- S-form-13 (current): 候補 ['arcanine', 'arcaninehisui'] から arcanine に決めた
- S-form-13 (minimal): 候補 ['arcanine', 'arcaninehisui'] から arcanine に決めた
- S-unk-07 (current): 候補 ['charizarditex', 'charizarditey'] から charizarditex に決めた
- S-unit-05 (current): 曖昧な単位を 1 つに決めた: {'atk': 252}

## 5. fixture のカテゴリ別 (経路 × split)

| カテゴリ | 経路 | split | pass / (pass+fail) | excluded |
|---|---|---|---|---|
| form | current | confirm | 10/18 (33.7〜75.4%) | 0 |
| form | current | diag | 3/3 (43.8〜100.0%) | 0 |
| form | minimal+stone | confirm | 3/4 (30.1〜95.4%) | 0 |
| form | minimal+stone | diag | 1/3 (6.1〜79.2%) | 0 |
| form | minimal | confirm | 8/18 (24.6〜66.3%) | 0 |
| form | minimal | diag | 0/3 (0.0〜56.2%) | 0 |
| missing | current | confirm | 6/6 (61.0〜100.0%) | 0 |
| missing | current | diag | 0/1 (0.0〜79.3%) | 0 |
| missing | minimal+stone | confirm | 1/1 (20.7〜100.0%) | 0 |
| missing | minimal | confirm | 6/6 (61.0〜100.0%) | 0 |
| missing | minimal | diag | 1/1 (20.7〜100.0%) | 0 |
| reverse | current | confirm | 6/6 (61.0〜100.0%) | 0 |
| reverse | minimal | confirm | 5/6 (43.6〜97.0%) | 0 |
| side | current | confirm | 1/1 (20.7〜100.0%) | 0 |
| side | minimal | confirm | 1/1 (20.7〜100.0%) | 0 |
| stat | current | confirm | 3/3 (43.8〜100.0%) | 0 |
| stat | minimal | confirm | 3/3 (43.8〜100.0%) | 0 |
| unit | current | confirm | 9/14 (38.8〜83.7%) | 0 |
| unit | current | diag | 0/2 (0.0〜65.8%) | 0 |
| unit | minimal | confirm | 10/13 (49.7〜91.8%) | 0 |
| unit | minimal | diag | 1/1 (20.7〜100.0%) | 0 |
| unknown | current | confirm | 8/10 (49.0〜94.3%) | 0 |
| unknown | minimal+stone | confirm | 3/3 (43.8〜100.0%) | 0 |
| unknown | minimal | confirm | 10/10 (72.2〜100.0%) | 0 |
| variant | current | confirm | 14/14 (78.5〜100.0%) | 0 |
| variant | current | diag | 1/1 (20.7〜100.0%) | 1 |
| variant | minimal+stone | confirm | 0/2 (0.0〜65.8%) | 0 |
| variant | minimal+stone | diag | 0/1 (0.0〜79.3%) | 0 |
| variant | minimal | confirm | 12/14 (60.1〜96.0%) | 0 |
| variant | minimal | diag | 0/1 (0.0〜79.3%) | 1 |

## 6. 辞書の重複・欠落

- jp_names.json species: 項目 1118、id 1118、複数の表記を持つ id 0、正規化キーの衝突 (別 id) 1 例 [('にどらん', ['nidoranf', 'nidoranm'])]
- jp_names.json moves: 項目 901、id 901、複数の表記を持つ id 0、正規化キーの衝突 (別 id) 0
- jp_names.json abilities: 項目 313、id 313、複数の表記を持つ id 0、正規化キーの衝突 (別 id) 0
- jp_names.json items: 項目 2148、id 2136、複数の表記を持つ id 12、正規化キーの衝突 (別 id) 2 例 [('ちかのかぎ', ['basementkeygoldenrod', 'basementkeynewmauville']), ('にどらんのあめ', ['nidoranfcandy', 'nidoranmcandy'])]
- jp_names.json types: 項目 19、id 19、複数の表記を持つ id 0、正規化キーの衝突 (別 id) 0
- jp_names.json natures: 項目 25、id 25、複数の表記を持つ id 0、正規化キーの衝突 (別 id) 0
- Showdown ja species: 項目 1559、名前なし (null) 449、同じ正規化キーの複数 id 0
- Showdown ja moves: 項目 953、名前なし (null) 19、同じ正規化キーの複数 id 0
- Showdown ja items: 項目 583、名前なし (null) 95、同じ正規化キーの複数 id 2 例 [('ながねぎ', ['leek', 'stick']), ('ぼんさくのちゃわん', ['masterpieceteacup', 'unremarkableteacup'])]
- Showdown ja abilities: 項目 322、名前なし (null) 5、同じ正規化キーの複数 id 0
- Showdown ja natures: 項目 -、名前なし (null) -、同じ正規化キーの複数 id 0

## 7. 名前の差 (対象規制、現行 vs Showdown ja、正規化しても違うもの)

| 種類 | id | 現行 | Showdown ja |
|---|---|---|---|
| (該当なし) | | | |

---

以下 §8〜§11 は集計を読んで手で書いた (道具の再実行では生成されない。数値は上の表と measurements.jsonl から)。

## 8. 重大な不一致 (正確な入力の誤変換)

1. **人間対 AI 用の書き出し (tools/export_my_team_showdown) が能力ポイントを努力値に換算して EVs 欄に書く。** 登録の完全な 30 個体すべてで
   Showdown の champions の検証が「more than 32 Stat Points」で拒否する (current-human 30/30 fail、S-unit-11)。champions の EVs 欄は
   能力ポイント (team-validator の useStatPoints)。測定の参照チームの経路 (tools/evaluate_team.build_myteam_text) は点数をそのまま書くので
   無関係 (30/30 一致、Showdown の検証も 30/30 問題なし)。
2. **単位の混在を黙って受理する。** `advisor_player._ev_of_line` は「EVs: 4 HP / 252 Atk」の 4 を点数 (努力値 32 相当)、252 を努力値として
   同じ行で混ぜる (S-unit-08)。`tools/register_my_team.parse_team_text` は努力値表記の本文の 252 を能力ポイントとして登録する (S-unit-09)。
   `my_team.get_my_build` は「能力ポイント」欄の 33 を努力値 33 として受理する (S-unit-06)。evs 欄の 32 は点数と決めて 252 にする
   (S-unit-05、曖昧の黙殺)。PokemonSet.to_showdown_text は 252 を 32 に黙って丸める (S-unit-14、diag。この経路に範囲外の値が来るかは未確認)。
3. **名前の解決 (NameResolver の類似度照合) が、存在しない名前・非参戦の名前を別の id に置き換える。**
   「ガブリアスナイトW」→ garchompite (S-unk-06)、「リザードンナイト」→ charizarditex (S-unk-07、曖昧の黙殺)。
   図鑑の範囲の走査 (diag) では非参戦種の正しい名前が別の参戦種に解決される: ラッキー → umbreon、サンダー → jolteon、
   ニドキング → slowking、ヤドン → slowbro、ココドラ → gogoat 等 (dex species の誤変換 37 件。うち非参戦の名前の置換が大半、
   残りは性別の組み立て名が基本形に戻るもの)。対象規制の名前の範囲 (regmc) でも、性別・地方の形態の組み立て名
   (イダイトウ(メス) / イエッサン(メス) / ニャオニクス(メス) / ヒスイジュナイパー) が基本形の id に戻る (4 件)。
4. **id の体系の違い**: 「メガニャオニクス」→ meowsticfemalemega (PokeAPI 由来の id。Showdown の id は meowsticfmega / meowsticmmega)。
   名前で ♂♀ が決まらないうえ、Showdown に無い id を返す (S-form-14)。

誤変換が 0 件だった範囲 (同じ粒度で): 対象規制の技 503/503・特性 215/215・持ち物 163/164 (欠落 1: leek) の往復、
登録の完全な 30 個体の測定用の往復 (種族・持ち物・特性・性格・能力ポイント・技 の 180 欄 + 実数値 30 + Showdown の検証 30、うち表記の差 3)、
表記揺れ (幅・かな・空白・中黒・長音の字形) 14/14、実数値 3/3、欠損値 6/6、現行の名前と Showdown ja の名前の差 0 件 (幅の差 2 件だけ)。

## 9. 最小対応 (Showdown ja + 正規化 + Showdown の Teams / 検証 / 実数値) で足りる割合

- 対象規制の全走査 (id → Showdown ja → id): 技 503/503 (100%)、特性 214/215 (99.5%)、種 267/346 (77.2%)、持ち物 82/164 (50.0%)。
  持ち物の欠落はほぼメガストーン (Showdown ja/items.ts の名前が null)。メガストーンの定義から「<種名>ナイト[XYZ]」を足す M+石 で 161/164 (98.2%)。
  ただし M+石 の組み立て名は公式名と違うものがある (リザードンナイトX ≠ リザードナイトX、カイリューナイト ≠ カイリュナイト)。
  種の欠落は形態 (地方・性別・メガ X/Y/Z の多く) で、Showdown ja/pokedex.ts の名前が null (全体で 449 件)。
- 登録の往復 (欄単位): 205/210 で一致、誤変換 0。残り 5 は 非対応 (持ち物の名前 3: メガガブリアスZナイト / リザードナイトＹ / メガミミロップナイト、
  登録キーの復元 2: イダイトウ / イエッサン)。
- S fixture (confirm): 誤変換 0、曖昧の黙殺 3 (ロトム / キュウコン / ウインディ を基本形に決める)、欠落 12 (石・Z 形態・メガ X/Y の名前)。
  単位の境界は Showdown の検証が範囲外を拒否するので、現行が誤変換した 4 件 (S-unit-06/08/09/11) は M ではすべて拒否または一致。
- 形式の変換 (Teams.import / export) と実数値 (Battle の statModify) は現行の計算と全件一致 (登録 30/30、fixture 3/3)。

## 10. 現行にしか無い機能 → 「曖昧さを残す機能」と「不要な複雑さ」

| 機能 | どこ | 区分 | 根拠 |
|---|---|---|---|
| メガストーン・Z 形態の名前 (PokeAPI + 組み立て) | jp_names.json | 必要なデータ | Showdown ja に無い (対象規制の石 81 件が null)。M+石 は公式名とずれる |
| 人が確認した別名 (カイリューナイト 等) | article_aliases.json | 必要なデータ (確認つき) | 手入力・記事の表記を受ける。確認の記録 (basis / history) がある |
| 種族ID で形態を決める登録の欄 | my_team.json | 必要 (曖昧さを解く) | HUD の名前は形態を省く。ロトム・イダイトウ・イエッサン は 種族ID でだけ決まる |
| 「メガ<種>Zナイト」の構文の解析 (接尾辞の厳密一致) | normalize.parse_mega_stone_name | 必要だが表で足りる | 登録の表記を受ける。表の別名にすれば構文の解析は要らない |
| 類似度照合 (cutoff 0.8) と緩いキー (清音化・小書き・OCR 混同) | NameResolver.resolve | 正確な入力には不要な複雑さ / OCR では保留 | 存在しない名前・非参戦の名前を別 id に置換 (§8-3)。誤読の補正としての価値は I-02 で測る |
| 非参戦種の除外 | champions_illegal_ids | 類似度照合とセットでだけ意味がある | 完全一致なら除外は要らない (非参戦の名前は非参戦の id のまま返し、使えるかは別に判定すればよい) |
| 形態の表示名の組み立て (アローラ{} / {}(メス) 等) | advisor.infer | 表示だけ必要、逆引きは壊れている | 組み立て名から id に戻らない (regmc 種の欠落 31 件の大半)、性別は基本形に戻る |
| 努力値表記も受ける (32 以下は点数とみなす) | my_team / advisor_player | 不要な複雑さ (曖昧の黙殺の源) | §8-2。点数だけを受け、範囲外は拒否すれば足りる |
| 未登録の欄を使用率で補完 | build_myteam_text | 測定用の補完。変換とは分ける | 元の型の保持ではない (S-miss-07、登録の不完全な 3 個体) |
| 文中の名前探し・候補を絞った照合 | find_in_text / resolve_restricted | I-02 の範囲 | 画面の文の読み取り用。本試験では測っていない |

## 11. 判定 (辞書・変換・登録)

| 対象 | 判定 | 対象範囲と理由 |
|---|---|---|
| 辞書 (名前の表) | **最小部分を移植** | 移植するのは表のデータだけ: jp_names.json の種・技・特性・持ち物・性格の正規名 (対象規制で技・特性 100%、持ち物 99.4% の往復一致、Showdown ja との名前の差 0)、メガストーン・Z 形態の名前、人が確認した別名 (article_aliases)。Showdown ja を基準にし、足りない名前 (石・形態) をこの表で補う。照合は正規化の完全一致にする。類似度照合 (NameResolver.resolve) は正確な入力で別 id への置換を起こすので移植しない (OCR の補正としての要否は I-02 の結果まで保留)。形態の組み立て名の逆引きは移植しない |
| 変換 (名前・単位・形式) | **外部実装／簡易実装に置換** | 形式の変換・検証・実数値は Showdown の Teams.import / export・TeamValidator・Battle で現行と全件一致し、単位の範囲外を拒否する。現行は単位の混在・範囲外の受理・書き出しの単位の誤り (人間対 AI 用、30/30) があり、移植しない。旧コードは比較資料として残す |
| 登録 (my_team.json) | データ: **データ保存** / 読み書きのコード: **外部実装／簡易実装に置換** | 登録の値は自分の型の事実 (観測事実) として保存する (33 個体、うち 3 個体は不完全)。新しい読み書きは「種族ID 必須 + 能力ポイントだけ (0〜32、合計 66) + 名前は表の完全一致、決まらなければ確認待ち」の簡易実装で、往復 205/210 (残りは辞書の移植で解消する名前の欠落) かつ誤変換 0。evs 欄・32 以下の自動判定は持ち込まない |

用途 → 対照 → 正しさ → 効果 → 費用 → 採否 → 未確認範囲:
日本語の名前・型登録と Showdown 形式の相互変換 → Showdown ja + 正規化の完全一致 + Showdown の Teams / 検証 / Battle (M) と現行 (OLD) → OLD は確認集合で誤変換 6 件・曖昧の黙殺 8 件 (単位 4・名前 2・書き出しの単位は登録 30/30)、M は誤変換 0・曖昧の黙殺 3・欠落 12 (石・形態の名前) → 判断を変えない部品のため効果は測らない → 計算 約 12 秒 (上限 25 分) → 辞書は最小部分を移植、変換は置換、登録はデータ保存 + コードは置換 → 公式の日本語名 (Z ストーン・Z メガ・地方形態) の外部確認、OCR 誤読への強さ (I-02)、自分の登録を相手に当てない契約 (engine 側)、PokemonSet の丸めの経路に範囲外の値が来るか、HUD 名 (形態を省く) を相手側でどう解くか、は未確認。

### 未確認・注意

- 公式の日本語名を外部で確認していない (外部アクセスなし)。Z ストーン・Z メガの日本語名は jp_names.json の組み立て名に合わせた (S-form-04/05/06/17)。
- 「曖昧」の期待値 (HUD 名 → 複数形態) は、HUD が形態を省くという登録の docstring と、両形態が champions で使える (非 Illegal、使用率にある) ことから置いた。名前だけで形態を決める用途 (例: 基本形を既定にする) を仕様として認めるなら、この 8 件 (OLD) / 3 件 (M) の扱いは変わる。
- fixture は 80 件 (上限)。R-* の 4 件と登録の往復は開発で使った表記を含むので diag (過去データ診断)。区間は小標本の目安。
- Showdown の dist を node で読んだ (Teams / TeamValidator / Battle。サーバーは起動しない)。共通規約の「ローカルの CLI (validate-team 等)」の範囲として扱った。
- 本体ツリーの logs/ への手書きの追記 (Edit) は権限設定で拒否された。本体の report.md は §1〜§7 (道具の出力) だけで、§8〜§11 はこのワークツリーの写しにだけある。
