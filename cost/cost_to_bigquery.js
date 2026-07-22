#!/usr/bin/env node
/**
 * 원가·물류(택배비) → BigQuery `mart.mart_cost_daily` 적재
 * cloop-collab/RF_D2C · cost 폴더
 *
 * 통합리포트 자동입력의 마지막 EGNIS 의존(원가엔진)을 BQ 테이블로 대체.
 * 원가엔진 = cloop-dashboard-vercel/api/report.js 를 그대로 이식(옵션명 파싱·맛/개입/별칭·택배단가).
 *   ⚠ SOURCE OF TRUTH = api/report.js·index.html. 엔진 로직 변경 시 그쪽과 반드시 동기화.
 *
 * 입력:
 *   (1) 일별·옵션별 판매 = cafe24.rf_cafe24_order_items_current
 *       (대시보드 bq_order_items 라우트와 동일 SQL: status_code NOT LIKE 'C%' 제외 = 취소/환불 반영,
 *        SUM(quantity)=순판매박스, 옵션명=raw_json.option_value)
 *   (2) 최신 원가 단가 = 구글시트 '최신원가 (weekly)'!A:G (D열=품목명, G열=단가)
 * 출력: mart.mart_cost_daily(report_date, mall, cogs, ship) — 기간만 원자적 재적재(트랜잭션 DELETE+INSERT)
 *
 * 모드:
 *   node cost/cost_to_bigquery.js                 # 일상: 최근 LOOKBACK_DAYS(기본3)일 재적재(어제까지)
 *   node cost/cost_to_bigquery.js --backfill 40   # 과거 N일 백필(어제까지)
 */
"use strict";
const { BigQuery } = require("@google-cloud/bigquery");
const { google } = require("googleapis");

// ===== 설정 =====
const GCP_PROJECT = process.env.BQ_PROJECT || "rf-ads-db-500505";
const BQ_LOCATION = process.env.BQ_LOCATION || "asia-northeast3";
const DEST_DATASET = "mart";
const DEST_TABLE = "mart_cost_daily";
const SRC_TABLE = "cafe24.rf_cafe24_order_items_current";
const LOOKBACK_DAYS = parseInt(process.env.LOOKBACK_DAYS || "3", 10);
const MALLS = ["cloop", "sprint"];

// ===== 원가 상수 (api/report.js와 동일) =====
const OV_COST_SHEET_ID = "1sdYZEt9AEBLxpD4sE8E_5zKudqjfdwvij0S_HYgfxeE";
const OV_COST_RANGE = "'최신원가 (weekly)'!A:G";
const OV_SHIP = { 6: 2563, 12: 3237, 15: 3233, 20: 2905, 24: 3562 };
const OV_ALIAS = { "사과": "오리지널", "헛개마카": "마카헛개", "샤인머스켓": "샤인머스캣", "샤머": "샤인머스캣", "화이트": "화이트발사믹" };
const OV_OVERRIDE = [{ g: "오프아워", f: "라임브리즈", p: 328 }, { g: "오프아워", f: "피치릴렉서", p: 329 }, { g: "티카이브", f: "인진쑥차", p: 331 }, { g: "티카이브", f: "호박팥차", p: 355 }];
const OV_LABELS = /(추천구성|실속구성|실속세트|최대할인|무료배송|맛보기팩|입문팩|비밀특가|비밀최저가|최저가|베스트|구성|세트|할인|배송|맛보기|입문|추천|실속|최대|비밀|특가|단독|최초|시크릿|벌크업|구매자한정|한정|선택|개당|행사|사은품|증정|best|new)/gi;
const SP_ALIAS_E = { "애플": "애플블라스트", "레몬": "레몬부스트", "오렌지": "오렌지임팩트", "자몽": "시트러스버스트", "사우어베리": "사우어베리", "시트러스": "시트러스버스트" };
const SP_ALIAS_P = { "자몽": "자몽", "이온자몽": "자몽", "머스캣리치": "머스캣리치", "머스캣": "머스캣리치", "이온머스캣": "머스캣리치", "리치": "머스캣리치" };
const SP_GIFT = 33378;

let OV_GROUPS = [];
let SP_COST = {};

function shipRateForCans(cans, mixed, pn) {
  if (/1\.5\s*l|1\.5\s*리터/i.test(pn || "") && cans === 6) return 2843;
  if (mixed && cans === 24) return 4047;
  if (OV_SHIP[cans] != null) return OV_SHIP[cans];
  let n = +cans || 0, t = 0; if (n <= 0) return 0;
  const n24 = Math.floor(n / 24); t += n24 * OV_SHIP[24]; n -= n24 * 24;
  const n12 = Math.floor(n / 12); t += n12 * OV_SHIP[12]; n -= n12 * 12;
  if (n > 0) t += Math.ceil(n / 6) * OV_SHIP[6];
  return t;
}
function ovSizeOf(s) { const m = String(s).match(/(\d+)\s*ml/i); if (m) return +m[1]; const l = String(s).match(/(\d+(?:\.\d+)?)\s*l\b/i) || String(s).match(/(\d+(?:\.\d+)?)\s*리터/); if (l) return Math.round(parseFloat(l[1]) * 1000); return null; }
function parseCost(values) {
  const OV_COST = ((values || [])).filter(r => r && r.length >= 7 && typeof r[6] === "number" && +r[6] > 0 && r[3] && !/쉬링크|신유통XO/.test(String(r[3]))).map(r => {
    const pet = /페트/.test(String(r[3]));
    let t = String(r[3]).replace(/^\[[^\]]*\]/, "").replace(/^클룹_/, "").replace(/^페트\s*,?\s*/, "").trim();
    const segs = t.split(",").map(x => x.trim());
    return { price: +r[6], pet, grp: segs[0].replace(/\(.*?\)/g, "").replace(/\s/g, ""), ml: ovSizeOf(t), flav: (segs[1] || "").replace(/\(.*?\)/g, "").replace(/제로/g, "").replace(/\s/g, "").trim() };
  });
  buildSpCost(values || []);
  return OV_COST;
}
function buildSpCost(vals) {
  SP_COST = {}; (vals || []).forEach(r => {
    if (!(r && r.length >= 7 && typeof r[6] === "number" && +r[6] > 0 && r[3])) return; const raw = String(r[3]);
    if (!/스프린트|에반게리온|퍼포먼스이온|진격/.test(raw)) return; if (/신유통XO|쉬링크/.test(raw)) return;
    let t = raw.replace(/^\[[^\]]*\]/, "").replace(/^클룹_/, "").trim(); const segs = t.split(",").map(x => x.trim());
    const ml = (t.match(/(\d+)\s*ml/i) || [])[1]; const size = ml ? +ml : 500;
    let colab; if (segs[0] === "스프린트에너지") colab = "에너지"; else { const c = segs[1] || ""; colab = /퍼포먼스이온/.test(c) ? "퍼포먼스이온" : /진격/.test(c) ? "진격거" : /에반/.test(c) ? "에반게리온" : c; }
    const flav = (segs[segs.length - 2] || "").replace(/제로/g, "").replace(/\s/g, "").trim();
    SP_COST[colab] = SP_COST[colab] || {}; SP_COST[colab][flav] = SP_COST[colab][flav] || {}; SP_COST[colab][flav][size] = +r[6];
  });
}
function spFlavorCost(pn, fl, ctx) {
  const szc = String(ctx || pn); const size = /250/.test(szc) ? 250 : 500;
  const perf = /퍼포먼스이온|퍼포먼스|이온/.test(String(pn)) || /이온/.test(String(fl));
  if (perf) { const f = SP_ALIAS_P[fl] || fl; const m = SP_COST["퍼포먼스이온"] || {}; return (m[f] && (m[f][500] || m[f][250])) || 0; }
  const f = SP_ALIAS_E[fl] || fl; if (size === 250) { const m = SP_COST["에너지"] || {}; return (m[f] && m[f][250]) || 0; }
  const j = SP_COST["진격거"] || {}, e = SP_COST["에너지"] || {}; return (j[f] && j[f][500]) || (e[f] && e[f][500]) || 0;
}
function spBoxCost(pn, on) {
  const ctx = String(pn) + " " + String(on);
  if (/기프트박스/.test(ctx)) return { boxCost: SP_GIFT, cans: 1, pieces: 1 };
  // 맛+수량 토큰 직접 추출 — order_items 옵션 형식 다양(개입수=.., 구성 선택=애플48+레몬24 / 맛 선택 ④=자몽 (24개입) / 이온 머스캣6).
  //   키구조 파싱(ovPieces) '개입수=' 오염·괄호수량 미인식으로 스프린트 원가 붕괴(4.4%)하던 문제 해소. 대시보드 배포본(index.html) 이식.
  { const toks = []; const reF = /(이온\s*)?(애플블라스트|애플|레몬부스트|레몬|오렌지임팩트|오렌지|자몽|사우어베리|시트러스버스트|시트러스|머스캣리치|머스캣|리치)\s*\(?\s*(\d+)/g; let mm;
    while ((mm = reF.exec(String(on)))) { toks.push({ fl: (mm[1] ? "이온" : "") + mm[2], cnt: +mm[3] }); }
    if (toks.length) { let bc = 0, cans = 0; toks.forEach(t => { bc += t.cnt * (spFlavorCost(pn, t.fl, ctx) || 0); cans += t.cnt; }); return { boxCost: bc, cans, pieces: toks.length }; }
  }
  const par = ctx.match(/\(([^)]*[가-힣][^)]*)\)/);
  if (par && !/개입|원|%/.test(par[1])) {
    const flavs = par[1].split("/").map(s => s.trim()).filter(Boolean);
    const m = ctx.match(/(\d+)\s*개입/); const ip = m ? +m[1] : 6;
    let sum = 0, n = 0; flavs.forEach(fl => { const c = spFlavorCost(pn, fl, ctx); if (c) { sum += c; n++; } });
    return { boxCost: ip * (n ? sum / n : 0), cans: ip, pieces: flavs.length };
  }
  const ps = ovPieces(pn, on); if (!ps.length) return null;
  let bc = 0, cans = 0; ps.forEach(p => { bc += (p.cnt || 0) * (spFlavorCost(pn, p.fl, ctx) || 0); cans += (p.cnt || 0); });
  return { boxCost: bc, cans, pieces: ps.length };
}
function ovPre(on) { return String(on).replace(/\[[^\]]*\]/g, " ").replace(/\(\s*개당[^)]*\)/g, " ").replace(/\(\s*[+~][^)]*\)/g, " ").replace(/\(\s*[^)]*(?:원|%|off)[^)]*\)/gi, " ").replace(/\d[\d,]*\s*원\s*[대!~]?/g, " ").replace(/~?\s*\d+\s*%\s*off/gi, " ").replace(/맛\s*선택\s*[①②③④⑤⑥⑦⑧⑨0-9]*\s*[:：]?/g, "/").replace(/개입\s*수\s*[:：]/g, " ").replace(/[⭐️🔥✨]/g, " "); }
function ovCleanFl(p) { let f = String(p).replace(/\([^)]*\)/g, " ").replace(/[\[\]()*]/g, " ").replace(/[①②③④⑤⑥⑦⑧⑨]/g, " ").replace(OV_LABELS, " ").replace(/\d+\s*개입/g, " ").replace(/개입/g, " ").replace(/\d+\.\d+\s*(ml|l)?/gi, " ").replace(/\d+\s*(ml|l|종)\b/gi, " ").replace(/제로/g, "").replace(/[:：,·\-!?\/]/g, " ").replace(/\d+/g, " ").trim().replace(/\s+/g, ""); OV_GROUPS.forEach(g => { f = f.split(g).join(""); }); return f; }
function ovIpName(pn) { const m = String(pn).match(/(\d+)\s*개입/); return m ? +m[1] : null; }
function ovGrpOf(pn) { const pns = String(pn).replace(/\s/g, ""); return OV_GROUPS.find(g => pns.includes(g)) || null; }
function ovGroups(cost) { OV_GROUPS = [...new Set(cost.map(c => c.grp).concat(OV_OVERRIDE.map(o => o.g)))].filter(Boolean).sort((a, b) => b.length - a.length); return OV_GROUPS; }
function ovPieces(pn, on) {
  on = ovPre(on); const parts = on.split(/[\/+]/).map(x => x.trim()).filter(Boolean), res = [];
  parts.forEach(p => { const cm = p.match(/\((\d+)\s*개입\)/) || p.match(/(\d+)\s*개입/) || p.match(/(\d+)\s*$/) || p.match(/\((\d+)\)/) || p.match(/(\d+)/); let cnt = cm ? +cm[1] : null; const fl = ovCleanFl(p); if (!fl) return; res.push({ fl, cnt }); });
  if (!res.length) return [];
  if (res.length === 1 && res[0].cnt == null) res[0].cnt = ovIpName(pn) || 1;
  res.forEach(r => { if (r.cnt == null) r.cnt = 1; });
  return res;
}
function ovFlavorCost(pn, fl, cost, sp) {
  const grp = ovGrpOf(pn); if (!grp) return null;
  if (sp) return spFlavorCost(pn, fl);
  const f = OV_ALIAS[fl] || fl; const pns = String(pn).replace(/\s/g, "");
  const ov = OV_OVERRIDE.find(o => pns.includes(o.g) && f && (f.includes(o.f) || o.f.includes(f))); if (ov) return ov.p;
  let cand = cost.filter(c => c.grp === grp); const psz = ovSizeOf(pn); if (psz) { const z = cand.filter(c => c.ml === psz); if (z.length) cand = z; }
  if (!cand.length) return 0;
  const hits = cand.filter(c => c.flav && f && (f.includes(c.flav) || c.flav.includes(f)));
  if (hits.length) { const np = hits.filter(c => !c.pet); return (np[0] || hits[0]).price; }
  return Math.round(cand.reduce((a, b) => a + b.price, 0) / cand.length);
}
function ovBoxCost(pn, on, cost, sp) {
  if (!ovGrpOf(pn)) return null;
  if (sp) return spBoxCost(pn, on);
  const ps = ovPieces(pn, on);
  if (!ps.length) {
    const grp = ovGrpOf(pn); let cand = cost.filter(c => c.grp === grp);
    const psz = ovSizeOf(pn); if (psz) { const z = cand.filter(c => c.ml === psz); if (z.length) cand = z; else if (psz >= 1000) return null; }
    const ipm = String(on).match(/(\d+)\s*개입/) || String(pn).match(/(\d+)\s*개입/) || String(on).match(/(\d+)\s*$/); const ip = ipm ? +ipm[1] : null;
    if (!cand.length || !ip) return null;
    const per = Math.round(cand.reduce((a, b) => a + b.price, 0) / cand.length);
    return { boxCost: per * ip, cans: ip, pieces: 1 };
  }
  let boxCost = 0, cans = 0; ps.forEach(p => { boxCost += (p.cnt || 0) * (ovFlavorCost(pn, p.fl, cost, sp) || 0); cans += (p.cnt || 0); });
  return { boxCost, cans, pieces: ps.length };
}

// ===== 날짜 (KST) =====
function kstDateStr(d) {
  const k = new Date(d.getTime() + 9 * 3600 * 1000);
  return k.toISOString().slice(0, 10);
}
function addDaysStr(str, n) {
  const d = new Date(str + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10);
}

// ===== 데이터 소스 =====
async function readCostSheet() {
  const auth = new google.auth.GoogleAuth({ scopes: ["https://www.googleapis.com/auth/spreadsheets.readonly"] });
  const sheets = google.sheets({ version: "v4", auth: await auth.getClient() });
  const res = await sheets.spreadsheets.values.get({
    spreadsheetId: OV_COST_SHEET_ID, range: OV_COST_RANGE, valueRenderOption: "UNFORMATTED_VALUE",
  });
  return (res.data && res.data.values) || [];
}
async function optionRows(bq, start, end) {
  const sql =
    "SELECT report_date AS date, mall AS mallId, product_name AS productName, " +
    "JSON_VALUE(raw_json,'$.option_value') AS optionName, " +
    "SUM(quantity) AS saleCount " +
    "FROM `" + SRC_TABLE + "` " +
    "WHERE mall IN ('cloop','sprint') AND report_date BETWEEN '" + start + "' AND '" + end + "' " +
    "AND IFNULL(JSON_VALUE(raw_json,'$.status_code'),'') NOT LIKE 'C%' " +
    "GROUP BY date, mallId, productName, optionName";
  // 날짜는 자체 계산 YYYY-MM-DD(주입위험 없음). 명명 DATE 파라미터가 Node 클라이언트에서 0행 매칭되던 이슈 회피용 인라인.
  const [rows] = await bq.query({ query: sql, location: BQ_LOCATION });
  return rows;
}

// ===== 원가엔진 실행 → 일별·몰별 cogs/ship =====
function computeDaily(orows, cost) {
  const acc = {}; // key = date|mall → {cogs, ship}
  (orows || []).forEach(r => {
    const pn = String(r.productName || ""), on = String(r.optionName || "");
    const box = +r.saleCount || 0; if (box === 0) return; // 음수(취소/환불)도 포함해 차감(현재는 C% 제외라 양수)
    const mall = String(r.mallId || "");
    const d = (r.date && r.date.value) ? String(r.date.value).slice(0, 10) : String(r.date || "").slice(0, 10);
    const sp = mall === "sprint";
    const bc = ovBoxCost(pn, on, cost, sp); if (bc == null) return; // 제품군 미인식 → 원가 미산입(정상 1~2%)
    const rate = shipRateForCans(bc.cans, bc.pieces > 1, pn);
    const k = d + "|" + mall; const b = acc[k] || (acc[k] = { date: d, mall, cogs: 0, ship: 0 });
    b.cogs += box * bc.boxCost; b.ship += box * rate;
  });
  return Object.values(acc).map(b => ({ report_date: b.date, mall: b.mall, cogs: Math.round(b.cogs), ship: Math.round(b.ship) }));
}

// ===== 적재 (기간만 원자적 재적재) =====
async function loadRange(bq, start, end, rows) {
  const target = "`" + GCP_PROJECT + "." + DEST_DATASET + "." + DEST_TABLE + "`";
  let stmts = "BEGIN TRANSACTION;\n";
  stmts += "DELETE FROM " + target + " WHERE report_date BETWEEN '" + start + "' AND '" + end + "';\n";
  if (rows.length) {
    const vals = rows.map(r => "('" + r.report_date + "','" + r.mall + "'," + r.cogs + "," + r.ship + ")").join(",");
    stmts += "INSERT INTO " + target + " (report_date, mall, cogs, ship) VALUES " + vals + ";\n";
  }
  stmts += "COMMIT TRANSACTION;";
  await bq.query({ query: stmts, location: BQ_LOCATION });
}

async function main() {
  const argv = process.argv.slice(2);
  const bi = argv.indexOf("--backfill");
  const bq = new BigQuery({ projectId: GCP_PROJECT, location: BQ_LOCATION });

  const todayStr = kstDateStr(new Date());
  const yesterday = addDaysStr(todayStr, -1);
  const span = bi >= 0 ? parseInt(argv[bi + 1] || "0", 10) : LOOKBACK_DAYS;
  const start = addDaysStr(yesterday, -(span - 1)); // [start, 어제]
  const end = yesterday;
  console.log(`[cost] 범위 ${start} ~ ${end} (${span}일, ${bi >= 0 ? "backfill" : "daily"})`);

  const cost = parseCost(await readCostSheet());
  ovGroups(cost);
  console.log(`[cost] 원가시트 ${cost.length}행, 제품군 ${OV_GROUPS.length}종`);

  const orows = await optionRows(bq, start, end);
  console.log(`[cost] order_items 옵션행 ${orows.length}`);

  const rows = computeDaily(orows, cost);
  const byMall = {}; rows.forEach(r => { byMall[r.mall] = (byMall[r.mall] || 0) + 1; });
  const totC = rows.reduce((a, r) => a + r.cogs, 0), totS = rows.reduce((a, r) => a + r.ship, 0);
  console.log(`[cost] 산출 ${rows.length}행 (${JSON.stringify(byMall)}) · cogs합 ${totC.toLocaleString()} · ship합 ${totS.toLocaleString()}`);

  await loadRange(bq, start, end, rows);
  console.log(`[cost] mart.mart_cost_daily ${start}~${end} 재적재 완료 (${rows.length}행)`);
}

main().catch(e => { console.error("[cost] 실패:", e && e.stack || e); process.exit(1); });
