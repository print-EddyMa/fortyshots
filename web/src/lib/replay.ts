// Folds a run's ledger (events.jsonl) into the state the console shows at a given cursor.
// Pure and cheap: the console recomputes it from scratch whenever the cursor moves.
import type { DftRow, HeaderPayload, Pick, Role, RunEvent, SummaryPayload, Timings, Tokens } from './types';

export interface ToolLine {
  call_id: string;
  name: string;
  args: any;
  done: boolean;
  ok?: boolean;
  summary?: string;
  dur_ms?: number | null;
  image?: string | null;
  seq: number;
}

export interface LlmStep {
  type: 'llm';
  key: string;
  seq: number;
  t: string;
  round: number;
  role: Role;
  call_id: string;
  model: string;
  strength: string;
  schema: string | null;
  n_images: number;
  finish: string;
  reasoning?: string;
  message?: string;
  tokens: Tokens | null;
  timings: Timings | null;
  dur_ms: number | null;
  tools: ToolLine[];
}

export interface NoteItem {
  type: 'note';
  key: string;
  seq: number;
  t: string;
  round: number;
  role: Role;
  kind: string;
  call_id: string | null;
  payload: any;
  tool?: ToolLine; // a tool call with no preceding LLM step (e.g. request_dft issued by the harness)
}

export type TraceItem = LlmStep | NoteItem;

export interface Candidate {
  id: string;
  round: number;
  p_hit: number | null;
  rationale: string;
  evidence: string[];
  vetoed: string | null;
  order: number | null; // purchase index, 1-based
  E_CO2: number | null;
  E_H2O: number | null;
  hit: boolean | null;
  revealSeq: number | null;
  S: number | null;
}

export interface RoundState {
  round: number;
  startT: string | null;
  endT: string | null;
  spent: number;
  hits: number;
  status: 'pending' | 'running' | 'done';
  calibration: { n: number; spearman: number | null; auc: number | null; image: string } | null;
  visual_rho: number | null;
}

export interface LlmCallStat {
  seq: number;
  t: string;
  role: Role;
  round: number;
  tps: number;
  pps: number;
  cache_n: number;
  prompt_n: number;
  predicted_n: number;
  dur_ms: number;
}

export type Phase = 'setup' | 'plan' | 'propose' | 'critique' | 'approve' | 'buy' | 'calibrate' | 'report' | 'done';

export interface RunState {
  header: HeaderPayload | null;
  n: number;
  lastSeq: number;
  t: string | null;
  round: number;
  phase: Phase;
  activeRole: Role | null;
  budget: number;
  spent: number;
  hits: number;
  curve: number[]; // hits after k lookups, k = 0..spent
  cands: Candidate[];
  rows: Record<string, any>; // descriptor rows seen in tool results, keyed by id
  rowsSource: Record<string, string>; // id -> call_id that first surfaced it
  rounds: RoundState[];
  trace: TraceItem[];
  lastReveal: { seq: number; round: number; ids: string[]; hits: number } | null;
  report: { text: string; seq: number } | null;
  claimcheck: { n_claims: number; verified: number; flagged: { claim: string; reason: string }[] } | null;
  summary: SummaryPayload | null;
  errors: { seq: number; where: string; message: string; recovered: boolean }[];
  llm: LlmCallStat[];
  schemaFailures: number;
  pending: { round: number; picks: Pick[]; seq: number; decided: boolean } | null; // proposal awaiting a human / dft_order
  humans: { seq: number; round: number; action: string; ids: string[]; note: string }[];
  mutations: { seq: number; payload: any }[];
}

const dacS = (co2: number, h2o: number) => co2 + Math.max(0, co2 - h2o + 0.1);

export function derive(events: RunEvent[], upto: number = events.length): RunState {
  const s: RunState = {
    header: null, n: 0, lastSeq: 0, t: null, round: 0, phase: 'setup', activeRole: null, budget: 40, spent: 0,
    hits: 0, curve: [0], cands: [], rows: {}, rowsSource: {}, rounds: [], trace: [], lastReveal: null, report: null,
    claimcheck: null, summary: null, errors: [], llm: [], schemaFailures: 0, pending: null, humans: [], mutations: [],
  };
  const candById = new Map<string, Candidate>();
  const stepByCall = new Map<string, LlmStep>();
  const toolByCall = new Map<string, ToolLine>();
  let lastStepOfRole = new Map<Role, LlmStep>();
  const roundOf = (r: number): RoundState => {
    let rs = s.rounds.find((x) => x.round === r);
    if (!rs) {
      rs = { round: r, startT: null, endT: null, spent: 0, hits: 0, status: 'pending', calibration: null, visual_rho: null };
      s.rounds.push(rs);
      s.rounds.sort((a, b) => a.round - b.round);
    }
    return rs;
  };
  const absorbRows = (result: any, callId: string | null) => {
    let arr: any[] | null = Array.isArray(result) ? result : Array.isArray(result?.rows) ? result.rows : null;
    if (!arr) return;
    // Table-shaped results are {columns, rows: [[...], ...]} (CONTRACTS addenda).
    const cols: string[] | null = Array.isArray(result?.columns) ? result.columns : null;
    if (cols) arr = arr.map((r) => (Array.isArray(r) ? Object.fromEntries(cols.map((c, i) => [c, r[i]])) : r));
    for (const r of arr) {
      if (r && typeof r === 'object' && typeof r.id === 'string') {
        s.rows[r.id] = { ...(s.rows[r.id] ?? {}), ...r };
        if (callId && !s.rowsSource[r.id]) s.rowsSource[r.id] = callId;
      }
    }
  };

  const end = Math.min(upto, events.length);
  for (let i = 0; i < end; i++) {
    const e = events[i];
    s.n = i + 1;
    s.lastSeq = e.seq;
    s.t = e.t;
    const nRounds = s.header?.rounds ?? 4;
    if (e.round > 0 && e.round <= nRounds && e.kind !== 'summary') {
      const rs = roundOf(e.round);
      rs.startT ??= e.t;
      if (rs.status === 'pending') rs.status = 'running';
      for (const other of s.rounds) if (other.round < e.round && other.status === 'running') other.status = 'done';
    }
    if (e.round > s.round && e.round <= nRounds) s.round = e.round;
    const p = e.payload ?? {};
    switch (e.kind) {
      case 'header': {
        s.header = p as HeaderPayload;
        s.budget = p.budget ?? 40;
        const rounds = p.rounds ?? 4;
        for (let r = 1; r <= rounds; r++) roundOf(r);
        break;
      }
      case 'llm_call': {
        const step: LlmStep = {
          type: 'llm', key: `s${e.seq}`, seq: e.seq, t: e.t, round: e.round, role: e.role, call_id: e.call_id ?? `s${e.seq}`,
          model: p.model ?? '', strength: p.reasoning_strength ?? '', schema: p.schema ?? null, n_images: p.n_images ?? 0,
          finish: p.finish_reason ?? '', tokens: e.tokens, timings: e.timings, dur_ms: e.dur_ms, tools: [],
        };
        s.trace.push(step);
        if (e.call_id) stepByCall.set(e.call_id, step);
        lastStepOfRole.set(e.role, step);
        s.activeRole = e.role;
        if (e.timings) {
          s.llm.push({
            seq: e.seq, t: e.t, role: e.role, round: e.round, tps: e.timings.predicted_per_second,
            pps: e.timings.prompt_per_second, cache_n: e.timings.cache_n, prompt_n: e.timings.prompt_n,
            predicted_n: e.timings.predicted_n, dur_ms: e.dur_ms ?? 0,
          });
        }
        if (e.role === 'director') s.phase = 'plan';
        else if (e.role === 'proposer') s.phase = 'propose';
        else if (e.role === 'critic') s.phase = 'critique';
        else if (e.role === 'reporter') s.phase = 'report';
        break;
      }
      case 'reasoning':
      case 'message': {
        const step = e.call_id ? stepByCall.get(e.call_id) : undefined;
        if (step) {
          if (e.kind === 'reasoning') step.reasoning = p.text;
          else step.message = p.text;
        } else {
          s.trace.push({ type: 'note', key: `n${e.seq}`, seq: e.seq, t: e.t, round: e.round, role: e.role, kind: e.kind, call_id: e.call_id, payload: p });
        }
        break;
      }
      case 'tool_call': {
        const tl: ToolLine = { call_id: e.call_id ?? `t${e.seq}`, name: p.name, args: p.args, done: false, seq: e.seq };
        toolByCall.set(tl.call_id, tl);
        const step = lastStepOfRole.get(e.role);
        if (step && step.round === e.round && p.name !== 'request_dft') step.tools.push(tl);
        else s.trace.push({ type: 'note', key: `n${e.seq}`, seq: e.seq, t: e.t, round: e.round, role: e.role, kind: 'tool', call_id: e.call_id, payload: p, tool: tl });
        if (p.name === 'calibrate_oracle') s.phase = 'calibrate';
        break;
      }
      case 'tool_result': {
        const tl = e.call_id ? toolByCall.get(e.call_id) : undefined;
        if (tl) {
          tl.done = true;
          tl.ok = p.ok;
          tl.summary = p.summary;
          tl.dur_ms = e.dur_ms;
          tl.image = p.image ?? null;
        }
        if (p.name !== 'request_dft') absorbRows(p.result, e.call_id);
        break;
      }
      case 'proposal': {
        const picks: Pick[] = p.picks ?? [];
        // A revised proposal replaces the round's unpurchased picks.
        for (const c of [...candById.values()]) {
          if (c.round === e.round && c.order == null && !c.vetoed && !picks.some((pk) => pk.id === c.id)) candById.delete(c.id);
        }
        for (const pk of picks) {
          const c = candById.get(pk.id);
          if (c) {
            c.p_hit = pk.p_hit; c.rationale = pk.rationale; c.evidence = pk.evidence ?? []; c.vetoed = null;
          } else {
            candById.set(pk.id, {
              id: pk.id, round: e.round, p_hit: pk.p_hit, rationale: pk.rationale, evidence: pk.evidence ?? [], vetoed: null,
              order: null, E_CO2: null, E_H2O: null, hit: null, revealSeq: null, S: null,
            });
          }
        }
        s.pending = { round: e.round, picks, seq: e.seq, decided: false };
        s.phase = 'propose';
        s.trace.push(note(e));
        break;
      }
      case 'critique': {
        for (const v of p.vetoes ?? []) {
          const c = candById.get(v.id);
          if (c) c.vetoed = v.reason;
        }
        roundOf(e.round).visual_rho = p.visual_rho ?? null;
        s.phase = 'critique';
        s.trace.push(note(e));
        break;
      }
      case 'human': {
        s.humans.push({ seq: e.seq, round: e.round, action: p.action, ids: p.ids ?? [], note: p.note ?? '' });
        if (s.pending && s.pending.round === e.round) s.pending.decided = true;
        s.phase = 'approve';
        s.trace.push(note(e));
        break;
      }
      case 'dft_order': {
        let k = s.spent;
        for (const id of p.ids ?? []) {
          let c = candById.get(id);
          if (!c) {
            c = { id, round: e.round, p_hit: null, rationale: '', evidence: [], vetoed: null, order: null, E_CO2: null, E_H2O: null, hit: null, revealSeq: null, S: null };
            candById.set(id, c);
          }
          c.order = ++k;
        }
        // Vetoed picks that were never bought drop out of the table once the batch is ordered.
        for (const c of [...candById.values()]) if (c.round === e.round && c.order == null) candById.delete(c.id);
        s.pending = null;
        s.phase = 'buy';
        s.trace.push(note(e));
        break;
      }
      case 'dft_result': {
        const res: DftRow[] = p.results ?? [];
        let nh = 0;
        for (const r of res) {
          const c = candById.get(r.id);
          if (c) {
            c.E_CO2 = r.E_CO2; c.E_H2O = r.E_H2O; c.hit = r.hit; c.revealSeq = e.seq; c.S = dacS(r.E_CO2, r.E_H2O);
          }
          if (r.hit) nh++;
        }
        const ordered = [...candById.values()].filter((c) => c.order != null).sort((a, b) => a.order! - b.order!);
        s.curve = [0];
        for (const c of ordered) if (c.hit != null) s.curve.push(s.curve[s.curve.length - 1] + (c.hit ? 1 : 0));
        s.spent = p.spent ?? s.spent + res.length;
        s.hits = p.hits_total ?? s.hits + nh;
        const rs = roundOf(e.round);
        rs.spent += res.length;
        rs.hits += nh;
        rs.status = 'done';
        rs.endT = e.t;
        s.lastReveal = { seq: e.seq, round: e.round, ids: res.map((r) => r.id), hits: nh };
        s.phase = 'buy';
        s.trace.push(note(e));
        break;
      }
      case 'calibration': {
        roundOf(e.round).calibration = { n: p.n, spearman: p.spearman, auc: p.auc, image: p.image };
        s.phase = 'calibrate';
        s.trace.push(note(e));
        break;
      }
      case 'error': {
        s.errors.push({ seq: e.seq, where: p.where, message: p.message, recovered: !!p.recovered });
        if (String(p.where ?? '').includes('schema')) s.schemaFailures++;
        s.trace.push(note(e));
        break;
      }
      case 'report':
        s.report = { text: p.text, seq: e.seq };
        s.phase = 'report';
        s.trace.push(note(e));
        break;
      case 'claimcheck':
        s.claimcheck = p;
        s.trace.push(note(e));
        break;
      case 'mutation':
        s.mutations.push({ seq: e.seq, payload: p });
        s.trace.push(note(e));
        break;
      case 'summary':
        s.summary = p as SummaryPayload;
        s.phase = 'done';
        s.activeRole = null;
        for (const r of s.rounds) if (r.status === 'running') r.status = 'done';
        s.trace.push(note(e));
        break;
      default:
        s.trace.push(note(e));
    }
  }
  s.cands = [...candById.values()];
  return s;

  function note(e: RunEvent): NoteItem {
    return { type: 'note', key: `n${e.seq}`, seq: e.seq, t: e.t, round: e.round, role: e.role, kind: e.kind, call_id: e.call_id, payload: e.payload };
  }
}

/** Compressed replay timeline in ms: long idle gaps are capped so replays keep moving. */
export function timeline(events: RunEvent[], capMs = 20000, minMs = 220): number[] {
  const out: number[] = [];
  let acc = 0;
  let prev: number | null = null;
  for (const e of events) {
    const t = Date.parse(e.t);
    // Gaps are capped (idle waits) and floored (mock runs finish in seconds; each step should still be seen).
    if (prev != null && Number.isFinite(t)) acc += Math.min(Math.max(minMs, t - prev), capMs);
    if (Number.isFinite(t)) prev = t;
    out.push(acc);
  }
  return out;
}

/** Index of the first event of a kind (optionally in a round), or -1. */
export function findEvent(events: RunEvent[], pred: (e: RunEvent) => boolean): number {
  return events.findIndex(pred);
}

export function bestCandidate(cands: Candidate[]): Candidate | null {
  const revealed = cands.filter((c) => c.S != null);
  if (!revealed.length) return null;
  const pool = revealed.some((c) => c.hit) ? revealed.filter((c) => c.hit) : revealed;
  return pool.reduce((a, b) => (b.S! < a.S! ? b : a));
}
