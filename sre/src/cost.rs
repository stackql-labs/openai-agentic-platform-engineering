//! Run cost and trace summary. Every run ends by printing this so the consumption economics stay
//! in frame: per step the model, requests, tokens (cached and reasoning broken out), tool calls
//! (SELECT vs mutation) and USD from the repo-root `pricing.json`, then the response ids, the
//! statement log and the run record.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StepUsage {
    pub label: String,
    pub model: String,
    pub requests: u32,
    pub input_tokens: u32,
    pub cached_tokens: u32,
    pub output_tokens: u32,
    pub reasoning_tokens: u32,
    pub select_calls: u32,
    pub mutation_calls: u32,
    pub other_tool_calls: u32,
    pub tool_names: BTreeMap<String, u32>,
    pub response_ids: Vec<String>,
    pub cost_usd: Option<f64>,
}

impl StepUsage {
    pub fn new(label: &str, model: &str) -> Self {
        StepUsage {
            label: label.into(),
            model: model.into(),
            ..Default::default()
        }
    }

    pub fn note_tool_call(&mut self, name: &str) {
        *self.tool_names.entry(name.to_string()).or_insert(0) += 1;
        match name {
            "run_select_query" => self.select_calls += 1,
            "run_mutation_query" | "run_lifecycle_operation" => self.mutation_calls += 1,
            _ => self.other_tool_calls += 1,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Price {
    pub input: f64,
    #[serde(default)]
    pub cached_input: Option<f64>,
    pub output: f64,
}

#[derive(Debug, Clone, Default)]
pub struct Pricing {
    table: BTreeMap<String, Price>,
}

impl Pricing {
    /// `<repo root>/pricing.json` extended/overridden by `OPENAI_PRICING_JSON` (same shape).
    /// A missing file is not an error: every cost then prints as n/a.
    pub fn load(repo_root: &Path) -> Result<Self> {
        let mut table: BTreeMap<String, Price> = BTreeMap::new();
        let path = repo_root.join("pricing.json");
        if path.is_file() {
            let text = fs::read_to_string(&path)?;
            table = serde_json::from_str(&text).with_context(|| format!("parsing {}", path.display()))?;
        }
        if let Ok(raw) = std::env::var("OPENAI_PRICING_JSON") {
            if !raw.trim().is_empty() {
                let extra: BTreeMap<String, Price> =
                    serde_json::from_str(&raw).context("parsing OPENAI_PRICING_JSON")?;
                table.extend(extra);
            }
        }
        Ok(Pricing { table })
    }

    #[cfg(test)]
    pub fn from_table(table: BTreeMap<String, Price>) -> Self {
        Pricing { table }
    }

    /// Exact id, else a dated snapshot like `gpt-5.4-mini-2026-03-17` collapses to `gpt-5.4-mini`.
    pub fn price_for(&self, model: &str) -> Option<&Price> {
        if let Some(p) = self.table.get(model) {
            return Some(p);
        }
        let parts: Vec<&str> = model.split('-').collect();
        if parts.len() >= 4 && parts[parts.len() - 3..].iter().all(|p| p.chars().all(|c| c.is_ascii_digit())) {
            return self.table.get(&parts[..parts.len() - 3].join("-"));
        }
        None
    }

    pub fn cost_usd(&self, u: &StepUsage) -> Option<f64> {
        let p = self.price_for(&u.model)?;
        let uncached = u.input_tokens.saturating_sub(u.cached_tokens) as f64;
        let cached_rate = p.cached_input.unwrap_or(p.input);
        Some(
            (uncached * p.input + u.cached_tokens as f64 * cached_rate + u.output_tokens as f64 * p.output)
                / 1_000_000.0,
        )
    }
}

#[derive(Debug, Serialize)]
pub struct Ledger {
    pub use_case: String,
    pub started_at: String,
    pub entries: Vec<StepUsage>,
    pub notes: Vec<String>,
    #[serde(skip)]
    pricing: Pricing,
}

impl Ledger {
    pub fn new(use_case: &str, pricing: Pricing) -> Self {
        Ledger {
            use_case: use_case.into(),
            started_at: utc_now_iso(),
            entries: Vec::new(),
            notes: Vec::new(),
            pricing,
        }
    }

    pub fn record(&mut self, mut step: StepUsage) {
        step.cost_usd = self.pricing.cost_usd(&step);
        self.entries.push(step);
    }

    pub fn total_cost_usd(&self) -> f64 {
        self.entries.iter().filter_map(|e| e.cost_usd).fold(0.0, |a, b| a + b)
    }

    pub fn response_ids(&self) -> Vec<String> {
        self.entries.iter().flat_map(|e| e.response_ids.iter().cloned()).collect()
    }

    pub fn print_summary(&self, audit_log: &Path, record: Option<&Path>) {
        println!();
        println!("=== cost and trace ===");
        println!(
            "{:<22} {:<16} {:>4} {:>8} {:>7} {:>8} {:>7} {:>6} {:>4} {:>9}",
            "step", "model", "req", "input", "cached", "output", "reason", "select", "mut", "usd"
        );
        for e in &self.entries {
            let usd = e.cost_usd.map(|c| format!("{c:.5}")).unwrap_or_else(|| "n/a".into());
            println!(
                "{:<22} {:<16} {:>4} {:>8} {:>7} {:>8} {:>7} {:>6} {:>4} {:>9}",
                truncate(&e.label, 22),
                truncate(&e.model, 16),
                e.requests,
                e.input_tokens,
                e.cached_tokens,
                e.output_tokens,
                e.reasoning_tokens,
                e.select_calls,
                e.mutation_calls,
                usd
            );
        }
        let priced = self.entries.iter().filter(|e| e.cost_usd.is_some()).count();
        println!(
            "total estimated cost: USD {:.5} ({} of {} steps priced; unknown models print n/a)",
            self.total_cost_usd(),
            priced,
            self.entries.len()
        );
        let ids = self.response_ids();
        if ids.is_empty() {
            println!("response ids: none (no model calls)");
        } else {
            println!("response ids: {}", ids.join(", "));
        }
        for n in &self.notes {
            println!("note: {n}");
        }
        println!("statement log: {}", audit_log.display());
        if let Some(r) = record {
            println!("run record: {}", r.display());
        }
    }

    /// Write `runs/<use case>-<UTC timestamp>.json` with the payload plus this ledger.
    pub fn save(&self, runs_dir: &Path, mut payload: serde_json::Map<String, Value>) -> Result<PathBuf> {
        fs::create_dir_all(runs_dir)?;
        payload.insert("ledger".into(), serde_json::to_value(self)?);
        let path = runs_dir.join(format!("{}-{}-{}.json", self.use_case, utc_now_compact(), short_id(4)));
        fs::write(&path, serde_json::to_string_pretty(&Value::Object(payload))?)
            .with_context(|| format!("writing {}", path.display()))?;
        Ok(path)
    }
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        s.chars().take(n - 1).collect::<String>() + "~"
    }
}

/// Civil date from days since 1970-01-01 (Howard Hinnant's algorithm), so no chrono dependency.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn utc_parts() -> (i64, u32, u32, u32, u32, u32) {
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    (y, m, d, (rem / 3600) as u32, ((rem % 3600) / 60) as u32, (rem % 60) as u32)
}

pub fn utc_now_iso() -> String {
    let (y, mo, d, h, mi, s) = utc_parts();
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}Z")
}

pub fn utc_now_compact() -> String {
    let (y, mo, d, h, mi, s) = utc_parts();
    format!("{y:04}{mo:02}{d:02}T{h:02}{mi:02}{s:02}Z")
}

/// A short hex id from the clock and the process id - enough to make proposals and nonces
/// distinct within a run; not a security token.
pub fn short_id(n: usize) -> String {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let mut x = (nanos as u64) ^ ((std::process::id() as u64) << 32) ^ 0x9E37_79B9_7F4A_7C15;
    // splitmix64 finaliser
    x = (x ^ (x >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    x ^= x >> 31;
    let hex = format!("{x:016x}{:016x}", x.rotate_left(17).wrapping_mul(0x2545_F491_4F6C_DD1D));
    hex.chars().take(n).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table() -> Pricing {
        let mut t = BTreeMap::new();
        t.insert("gpt-5.4-mini".to_string(), Price { input: 0.75, cached_input: Some(0.075), output: 4.5 });
        Pricing::from_table(t)
    }

    #[test]
    fn prices_exact_and_dated_snapshots() {
        let p = table();
        assert!(p.price_for("gpt-5.4-mini").is_some());
        assert!(p.price_for("gpt-5.4-mini-2026-03-17").is_some());
        assert!(p.price_for("gpt-unknown").is_none());
    }

    #[test]
    fn cost_breaks_out_cached_tokens() {
        let p = table();
        let mut u = StepUsage::new("x", "gpt-5.4-mini");
        u.input_tokens = 1_000_000;
        u.cached_tokens = 500_000;
        u.output_tokens = 100_000;
        let usd = p.cost_usd(&u).unwrap();
        // 0.5M * 0.75 + 0.5M * 0.075 + 0.1M * 4.5 = 0.375 + 0.0375 + 0.45
        assert!((usd - 0.8625).abs() < 1e-9, "{usd}");
        let mut unknown = StepUsage::new("x", "nope");
        unknown.input_tokens = 10;
        assert!(p.cost_usd(&unknown).is_none());
    }

    #[test]
    fn tool_calls_are_classified() {
        let mut u = StepUsage::new("x", "m");
        u.note_tool_call("run_select_query");
        u.note_tool_call("describe_resource");
        u.note_tool_call("run_mutation_query");
        assert_eq!((u.select_calls, u.mutation_calls, u.other_tool_calls), (1, 1, 1));
    }

    #[test]
    fn empty_ledger_totals_to_positive_zero() {
        let l = Ledger::new("sre", table());
        assert_eq!(format!("{:.5}", l.total_cost_usd()), "0.00000");
    }

    #[test]
    fn timestamps_are_utc_iso() {
        let s = utc_now_iso();
        assert_eq!(s.len(), 20);
        assert!(s.ends_with('Z'));
        assert_eq!(&s[4..5], "-");
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_723), (2024, 1, 1));
    }
}
