// Shapes from CONTRACTS.md. Keep in sync with that file.

export type Role = 'director' | 'proposer' | 'critic' | 'reporter' | 'chemist' | 'system' | 'human';

export interface Tokens { prompt: number; completion: number; reasoning: number }
export interface Timings {
  prompt_n: number; prompt_per_second: number; predicted_n: number; predicted_per_second: number; cache_n: number;
}

export interface RunEvent {
  seq: number;
  t: string;
  run_id: string;
  round: number;
  role: Role;
  kind: string;
  payload: any;
  call_id: string | null;
  tokens: Tokens | null;
  timings: Timings | null;
  dur_ms: number | null;
}

export interface HeaderPayload {
  arm: string; pool: string; seed: number; budget: number; rounds: number; batch: number; goal: string;
  mode: 'recorded' | 'live' | 'mock';
  model: { name: string; gguf: string; sha256: string; quant: string; server_build: string; flags: string[] };
  machine: { chip: string; ram_gb: number; os: string };
  network: 'on' | 'off' | 'unknown';
  prereg_sha: string; git_sha: string;
}

export interface Pick { id: string; p_hit: number; rationale: string; evidence: string[] }
export interface DftRow { id: string; E_CO2: number; E_H2O: number; hit: boolean }

export interface SummaryPayload {
  arm: string; pool: string; seed: number; hits: number; spent: number; picks: string[]; hit_flags: boolean[];
  lookups_to_first_hit: number | null; brier: number | null; p_hits: (number | null)[]; duration_s: number;
  aborted: boolean; autofill: number;
}

export interface IndexEntry {
  run_id: string; arm: string; pool: string; seed: number; mode: string; hits: number; spent: number;
  duration_s: number | null; started: string | null; model: string | null; showcase?: boolean;
}

export interface ArmResult {
  arm: string; label: string; kind: 'baseline' | 'llm'; n_runs: number; hits_mean: number; hits_ci: [number, number];
  enrichment: number; lookups_per_hit: number | null; first_hit_mean: number;
  curve: number[]; curve_lo: number[]; curve_hi: number[];
}

export interface Results {
  generated: string;
  status: 'mock' | 'pilot' | 'final';
  prereg_sha: string;
  budget: number;
  base_rate: number;
  pools: { pool: string; n: number; hits: number }[];
  arms: ArmResult[];
  tests: { name: string; diff: number; ci: [number, number]; p: number }[];
  calibration: { brier: number; n: number; bins: { p_mean: number; hit_rate: number; n: number }[] } | null;
  critic: { visual_rho_mae: number; n: number } | null;
  honesty: {
    mace_auc: number; mace_auc_ci: [number, number]; gas_relax_auc: number; dft_init_auc: number;
    mace_mae_dft_geom: number; eqv2_mae_dft_geom: number; notes: string;
  };
  leak: { L_auc: number; L2_auc: number } | null;
  claims: { n: number; verified: number; flagged: number } | null;
  ceilings: { pool: string; oracle_hits: number; dft_init_hits: number; random_expected: number }[];
}

export interface PoolRow {
  id: string; S_mace: number; E_CO2_mace: number; E_H2O_mace: number; pld_A: number; lcd_A: number; oms: boolean;
  metals: string; cost_tier: number; hit: boolean; E_CO2: number; E_H2O: number;
}
export interface Pools { pools: Record<string, PoolRow[]> }

export interface Structure {
  id: string; cell: number[][]; numbers: number[]; ads: number[]; pos: number[][]; pos_relaxed: number[][];
}

// Prospective mode (physics/prospective.py): runs/<id>/mutants/{index.json, <mid>.json}. Never benchmark data.
export interface MutantSpec { op: 'functionalize' | 'swap_metal'; group?: string; site?: string | number; from?: string; to?: string }
export interface MutantValidity { clash_ratio: number; dV_frac: number; fmax: number; steps: number; converged: boolean; charge: string; valid: boolean }
export interface MaceBinding { E_CO2_mace: number; E_H2O_mace: number; S_mace: number }
export interface Eqv2Binding { E_CO2_eqv2?: number; E_H2O_eqv2?: number; S_eqv2?: number }

export interface MutantRow {
  id: string; proposal_seq: number; file: string | null; parent: string; parent_formula: string | null;
  parent_S: number | null; parent_hit: boolean | null; parent_mace: MaceBinding | null;
  spec: MutantSpec; spec_text: string; rationale: string; status: 'built' | 'invalid'; reason: string | null;
  validity: MutantValidity | null; formula: string | null; natoms: number | null;
  descriptors: Record<string, number | null> | null; pred_S: number | null; pred_std: number | null; rank: number | null;
  mace: MaceBinding | null; eqv2: Eqv2Binding | null; n_frames: number; steps: number | null; relax_s: number | null; label: string;
}

export interface MutantIndex {
  run_id: string; generated: string; label: string; note: string; mace_note: string;
  surrogate: { model: string; seed: number; n_train: number; n_bought: number; skipped?: string };
  n_proposed: number; n_built: number; mutants: MutantRow[]; wall_s: number;
}

export interface MutantDoc extends Structure {
  parent: string; spec: MutantSpec; label: string; frames: { cell: number[][]; pos: number[][] }[]; n_steps: number;
  validity: MutantValidity; mace: MaceBinding | null;
}
