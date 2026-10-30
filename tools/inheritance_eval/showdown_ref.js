// 引き継ぎ評価 I-01 の外部の標準変換 (ローカル Showdown の Teams.import / export、TeamValidator、Battle の実数値)。
// tools/inheritance_eval/conversion.py から呼ぶ。サーバーは起動しない (dist のモジュールを読むだけ)。読み取り専用。
//
//   node tools/inheritance_eval/showdown_ref.js <pokemon-showdown のディレクトリ> < tasks.json > results.json
//
// 入力: {"format": "gen9championsbssregmc", "tasks": [{"id": "...", "kind": "team_text", "text": "..."},
//                                                    {"id": "...", "kind": "exists", "category": "items", "ids": [...]}]}
// 出力: {"showdown_version": ..., "results": [{"id": ..., ...}]}
'use strict';

const psDir = process.argv[2];
if (!psDir) {
	console.error('使い方: node showdown_ref.js <pokemon-showdown のディレクトリ>');
	process.exit(2);
}
const {Teams} = require(psDir + '/dist/sim/teams.js');
const {TeamValidator} = require(psDir + '/dist/sim/team-validator.js');
const {Battle} = require(psDir + '/dist/sim/battle.js');
const {Dex} = require(psDir + '/dist/sim/dex.js');

function toID(s) {
	return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function readStdin() {
	return require('fs').readFileSync(0, 'utf8');
}

function setSummary(dex, set) {
	const species = dex.species.get(set.species);
	return {
		species: species.exists ? species.id : toID(set.species),
		species_exists: !!species.exists,
		item: set.item ? toID(set.item) : null,
		item_exists: set.item ? !!dex.items.get(set.item).exists : null,
		ability: set.ability ? toID(set.ability) : null,
		nature: set.nature ? toID(set.nature) : null,
		evs: set.evs,
		level: set.level,
		moves: (set.moves || []).map(toID),
	};
}

function stats(format, set) {
	// 1 体だけのチームで対戦を作り、対象 mod の statModify で計算した実数値を読む (対戦は進めない)
	const b = new Battle({formatid: format});
	b.setPlayer('p1', {team: Teams.pack([set])});
	const p = b.p1.pokemon[0];
	const out = Object.assign({hp: p.maxhp}, p.storedStats);
	b.destroy();
	return out;
}

function teamText(format, dex, validator, text) {
	const sets = Teams.import(text) || [];
	return sets.map(set => {
		const copy = JSON.parse(JSON.stringify(set));
		let problems = null;
		try {
			problems = validator.validateSet(copy, {});
		} catch (e) {
			problems = ['validateSet の例外: ' + e.message];
		}
		let st = null;
		let statError = null;
		try {
			st = stats(format, JSON.parse(JSON.stringify(set)));
		} catch (e) {
			statError = e.message;
		}
		return {
			parsed: setSummary(dex, set),
			problems: problems || [],
			stats: st,
			stat_error: statError,
			exported: Teams.export([set]).trim(),
		};
	});
}

function exists(dex, category, ids) {
	const table = {species: dex.species, items: dex.items, moves: dex.moves, abilities: dex.abilities,
		natures: dex.natures}[category];
	const out = {};
	for (const id of ids) {
		const got = table.get(id);
		out[id] = got && got.exists ? {id: got.id, name: got.name, nonstandard: got.isNonstandard || null} : null;
	}
	return out;
}

function main() {
	const req = JSON.parse(readStdin());
	const format = req.format;
	const fmt = Dex.formats.get(format);
	const dex = Dex.forFormat(fmt);
	const validator = TeamValidator.get(format);
	const results = [];
	for (const task of req.tasks || []) {
		try {
			if (task.kind === 'team_text') {
				results.push({id: task.id, sets: teamText(format, dex, validator, task.text)});
			} else if (task.kind === 'exists') {
				results.push({id: task.id, exists: exists(dex, task.category, task.ids)});
			} else if (task.kind === 'mega_stones') {
				// 対象 mod のメガストーン: {石 id, 基本種 id, メガ後 id, isNonstandard}
				const stones = [];
				for (const it of dex.items.all()) {
					if (!it.megaStone) continue;
					for (const [base, form] of Object.entries(it.megaStone)) {
						stones.push({id: it.id, base: toID(base), form: toID(form), nonstandard: it.isNonstandard || null});
					}
				}
				results.push({id: task.id, stones});
			} else {
				results.push({id: task.id, error: '未知の kind: ' + task.kind});
			}
		} catch (e) {
			results.push({id: task.id, error: String(e && e.stack || e)});
		}
	}
	process.stdout.write(JSON.stringify({format: fmt.id, mod: dex.currentMod, exists: !!fmt.exists, results}));
}

main();
