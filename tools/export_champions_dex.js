// Showdownのchampions modから種族/技データをJSONエクスポートする。
// poke-env (gen9静的データ) がチャンピオンズ新フォーム・リバランス技を
// 知らないため、実行時に注入するためのデータを生成する。
//
//   node tools/export_champions_dex.js > champions_agent/data/champions_dex.json
//
// 事前に pokemon-showdown がビルド済みであること (node pokemon-showdown start で自動ビルド)。
const path = require('path');
const {Dex} = require(path.join(__dirname, '..', 'pokemon-showdown', 'dist', 'sim'));

const dex = Dex.mod('champions');

const species = {};
for (const s of dex.species.all()) {
  if (!s.exists) continue;
  species[s.id] = {
    num: s.num,
    name: s.name,
    baseSpecies: s.baseSpecies,
    forme: s.forme || '',
    types: s.types,
    baseStats: s.baseStats,
    abilities: s.abilities,
    heightm: s.heightm,
    weightkg: s.weightkg,
    requiredItem: s.requiredItem || undefined,
    isMega: s.isMega || undefined,
  };
}

const moves = {};
for (const m of dex.moves.all()) {
  if (!m.exists) continue;
  moves[m.id] = {
    id: m.id,
    num: m.num,
    name: m.name,
    type: m.type,
    category: m.category,
    basePower: m.basePower,
    accuracy: m.accuracy,
    pp: m.pp,
    priority: m.priority,
    target: m.target,
    flags: m.flags,
  };
}

process.stdout.write(JSON.stringify({species, moves}));
