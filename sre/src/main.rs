//! `sre` - agentic SRE on Kubernetes: closed-loop incident triage with an approval-gated
//! remediation. Built on the OpenAI Responses API (`async-openai`) with the embedded StackQL MCP
//! server (`stackql-mcp`) as the tool surface.
//!
//!   sre setup                       pull the k8s provider, print server_info, the tool lists and
//!                                   the server's stackql://docs/instructions resource
//!   sre alert [--symptom "..."]     write runs/alert.json - the event trigger
//!   sre run [--approve|--decline]   diagnose -> propose -> gate -> execute -> verify -> close
//!   sre run --reset                 scale back to 1 through the same gate (no model)
//!   sre validate-queries            validate_select_query over the code-owned and example SELECTs
//!
//! The model steps work from the intent prompts in `sre/prompts/` and discover resources and IO
//! contracts through the StackQL discovery tools and the query library; no query pack is handed to
//! them. The gate checks the statement the model proposes against an allowlist before a human sees it.

mod agent;
mod alert;
mod config;
mod cost;
mod gate;
mod mcp;
mod prompts;
mod queries;

use std::time::{Duration, Instant};

use anyhow::{bail, Result};
use async_openai::config::OpenAIConfig;
use async_openai::Client;
use clap::{Parser, Subcommand};
use serde_json::{json, Map, Value};

use crate::agent::{run_step, Closeout, Diagnosis, Proposal, Step};
use crate::config::Settings;
use crate::cost::{Ledger, Pricing, StepUsage};
use crate::mcp::Server;
use crate::prompts::{compose, load_prompt, DISCOVERY_PROMPT};
use crate::queries::{list_queries, load_query};

#[derive(Parser)]
#[command(name = "sre", version, about = "Agentic SRE: gated incident triage on Kubernetes via StackQL and the OpenAI Responses API")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Pull the k8s provider into the approot; print server_info, the tool lists and the server's
    /// own instructions resource
    Setup,
    /// Fire the synthetic alert (writes runs/alert.json) - the event trigger, not a prompt
    Alert {
        /// Override the symptom text
        #[arg(long)]
        symptom: Option<String>,
    },
    /// Run the loop: diagnose -> propose -> approval gate -> one mutation -> verify -> close-out
    Run {
        /// Approve at the gate without the terminal prompt (unattended / rehearsal)
        #[arg(long, conflicts_with = "decline")]
        approve: bool,
        /// Decline at the gate: proves the abort path with zero mutations
        #[arg(long)]
        decline: bool,
        /// Scale the target back to 1 replica through the same gate (no model calls)
        #[arg(long)]
        reset: bool,
    },
    /// Run validate_select_query over the code-owned and example SELECTs under sre/queries/
    ValidateQueries,
}

#[tokio::main]
async fn main() {
    if let Err(e) = dispatch(Cli::parse()).await {
        eprintln!("error: {e:#}");
        std::process::exit(1);
    }
}

async fn dispatch(cli: Cli) -> Result<()> {
    match cli.command {
        Command::Setup => setup().await,
        Command::Alert { symptom } => {
            let s = Settings::load(false)?;
            let a = alert::fire(&s, symptom.as_deref())?;
            println!("=== alert fired ===");
            println!("{}", serde_json::to_string_pretty(&a)?);
            println!("written to {}", alert::alert_path(&s).display());
            Ok(())
        }
        Command::Run { approve, decline, reset } => {
            if reset {
                run_reset(approve, decline).await
            } else {
                run(approve, decline).await
            }
        }
        Command::ValidateQueries => validate_queries().await,
    }
}

fn banner(title: &str, sub: &str) {
    println!("=== {title} ===");
    println!("{sub}");
}

async fn setup() -> Result<()> {
    let s = Settings::load(false)?;
    banner("sre setup", "embedded StackQL MCP server (crates.io stackql-mcp), read_only mode");
    let server = mcp::start_read_only(&s).await?;
    println!("binary: {}", server.binary_path().display());
    println!("mode: {}", server.mode().as_str());
    println!(
        "approot: {}",
        s.approot.as_ref().map(|p| p.display().to_string()).unwrap_or_else(|| "~/.stackql (crate default)".into())
    );
    println!("pulling provider k8s ...");
    let msg = server.pull_provider("k8s").await?;
    println!("{}", msg.trim());
    println!("server_info:");
    println!("{}", server.server_info().await?);
    let all = server.tools().await?;
    let model = server.model_tools().await?;
    println!("tools advertised by the server ({}): {}", all.len(), all.iter().map(|t| t.name.to_string()).collect::<Vec<_>>().join(", "));
    println!("tools a model may hold ({}): {}", model.len(), model.iter().map(|t| t.name.to_string()).collect::<Vec<_>>().join(", "));
    match server.instructions().await {
        Some(text) => {
            let head: Vec<&str> = text.lines().take(6).collect();
            println!("{}:\n  {}\n  ...", mcp::INSTRUCTIONS_RESOURCE, head.join("\n  "));
        }
        None => println!("{} was not readable; runs continue without it", mcp::INSTRUCTIONS_RESOURCE),
    }
    let ctx = s.render_context();
    for name in prompts::ROLE_PROMPTS.iter().chain([DISCOVERY_PROMPT].iter()) {
        let text = load_prompt(&s.prompts_dir(), name, &ctx)?;
        println!("prompt {name}.md renders ({} lines)", text.lines().count());
    }
    server.shutdown().await?;
    println!("setup ok");
    Ok(())
}

async fn validate_queries() -> Result<()> {
    let s = Settings::load(false)?;
    banner(
        "validate queries",
        "validate_select_query over the code-owned and example SELECTs in sre/queries/ (the code-owned mutation template is rendered only)",
    );
    let server = mcp::start_read_only(&s).await?;
    let ctx = s.render_context();
    let mut failures = 0;
    for q in list_queries(&s.queries_dir())? {
        let file = q.path.file_name().map(|f| f.to_string_lossy().to_string()).unwrap_or_default();
        println!("{} ({file}; providers {}; params {})", q.id, q.providers.join(","), q.params.join(","));
        println!("  {}", q.description);
        let sql = q.render(&ctx, &[("replicas", "1")])?;
        if q.is_mutation() {
            println!("  rendered (mutation template, not validated):");
            println!("  {}", sql.replace('\n', "\n  "));
            continue;
        }
        let (ok, errors) = server.validate(&sql).await?;
        if ok {
            println!("  pass (expected columns: {})", q.expected_columns.join(", "));
        } else {
            failures += 1;
            println!("  FAIL {}", errors.join("; "));
        }
    }
    server.shutdown().await?;
    if failures > 0 {
        bail!("{failures} quer(ies) failed validation");
    }
    println!("all SELECTs valid");
    Ok(())
}

/// The verification loop: poll a SELECT until spec_replicas == target and ready_replicas >= target,
/// or the timeout passes. The model-proposed SELECT (already validated) is used when it returns
/// those columns; otherwise the poll falls back to the code-owned `sre/verify_replicas`.
async fn poll_recovery(
    server: &Server,
    s: &Settings,
    target: i64,
    proposed: Option<&str>,
    usage: &mut StepUsage,
) -> Result<(bool, Vec<Value>)> {
    println!();
    println!("=== verify ===");
    let fallback = load_query(&s.queries_dir(), "sre/verify_replicas")?.render(&s.render_context(), &[])?;
    let mut sql = proposed.map(str::to_string).unwrap_or_else(|| fallback.clone());
    println!("  using the {} verification SELECT", if proposed.is_some() { "model-proposed" } else { "code-owned" });
    let start = Instant::now();
    let timeout = Duration::from_secs(s.verify_timeout_secs);
    let mut rows = Vec::new();
    loop {
        usage.note_tool_call("run_select_query");
        rows = server.select(&sql).await.unwrap_or_else(|e| {
            println!("  verify SELECT failed: {e}");
            rows
        });
        let has = |k: &str| rows.first().map(|r| r.get(k).is_some()).unwrap_or(false);
        if sql != fallback && !rows.is_empty() && !(has("spec_replicas") && has("ready_replicas")) {
            println!("  proposed SELECT lacks spec_replicas/ready_replicas - switching to code-owned sre/verify_replicas");
            sql = fallback.clone();
            continue;
        }
        let get = |k: &str| rows.first().and_then(|r| r.get(k)).and_then(as_i64).unwrap_or(0);
        let (spec, ready, avail) = (get("spec_replicas"), get("ready_replicas"), get("available_replicas"));
        println!("  spec={spec} ready={ready} available={avail} ({}s)", start.elapsed().as_secs());
        if spec == target && ready >= target {
            return Ok((true, rows));
        }
        if start.elapsed() >= timeout {
            return Ok((false, rows));
        }
        tokio::time::sleep(Duration::from_secs(s.verify_poll_secs.max(1))).await;
    }
}

fn as_i64(v: &Value) -> Option<i64> {
    v.as_i64()
        .or_else(|| v.as_f64().map(|f| f as i64))
        .or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
}

fn openai_client() -> Result<Client<OpenAIConfig>> {
    if std::env::var("OPENAI_API_KEY").map(|v| v.trim().is_empty()).unwrap_or(true) {
        bail!("OPENAI_API_KEY is not set - add it to the repo-root .env");
    }
    Ok(Client::new())
}

/// The model-proposed verification SELECT passes `validate_select_query` or is not used.
async fn validate_proposed_select(ro: &Server, sql: &str) -> Option<String> {
    let sql = sql.trim().trim_end_matches(';').trim();
    if sql.is_empty() || !sql.to_ascii_uppercase().starts_with("SELECT") {
        println!("no usable verification SELECT proposed - the poll uses code-owned sre/verify_replicas");
        return None;
    }
    match ro.validate(sql).await {
        Ok((true, _)) => {
            println!("proposed verification SELECT validated");
            Some(sql.to_string())
        }
        Ok((false, errors)) => {
            println!("proposed verification SELECT failed validation ({}) - the poll uses code-owned sre/verify_replicas", errors.join("; "));
            None
        }
        Err(e) => {
            println!("validate_select_query failed ({e}) - the poll uses code-owned sre/verify_replicas");
            None
        }
    }
}

async fn run(approve_flag: bool, decline_flag: bool) -> Result<()> {
    let s = Settings::load(true)?;
    let client = openai_client()?;
    banner(
        "agentic sre - gated incident triage",
        "diagnose with SELECTs -> propose -> human approval -> one logged mutation -> verify -> close-out",
    );
    let alert = match alert::load(&s)? {
        Some(a) => a,
        None => {
            println!("no runs/alert.json - firing the synthetic alert");
            alert::fire(&s, None)?
        }
    };
    println!("alert {} ({}): {}", alert.alert_id, alert.source, alert.symptom);
    println!("signal: {}", alert.signal);
    println!("scope: {}/{} via {}", alert.scope.namespace, alert.scope.deployment, alert.scope.cluster_addr);

    let mut ledger = Ledger::new("sre", Pricing::load(&s.repo_root)?);
    let mut payload: Map<String, Value> = Map::new();
    payload.insert("alert".into(), serde_json::to_value(&alert)?);

    let ro = mcp::start_read_only(&s).await?;
    let ctx = s.render_context();
    let server_instructions = ro.instructions().await;
    if server_instructions.is_none() {
        ledger.notes.push(format!("{} not readable: prompts carry the discovery briefing only", mcp::INSTRUCTIONS_RESOURCE));
    }
    let discovery = load_prompt(&s.prompts_dir(), DISCOVERY_PROMPT, &ctx)?;
    let prompt = |name: &str| -> Result<String> {
        Ok(compose(&load_prompt(&s.prompts_dir(), name, &ctx)?, &discovery, server_instructions.as_deref()))
    };
    let tenancy = format!(
        "Tenancy: namespace {} on cluster_addr {} (protocol {}); target deployment {}.",
        s.k8s_namespace, s.kube_cluster_addr, s.kube_protocol, s.target_deployment
    );

    // 1. diagnose (reasoning tier, SELECT and discovery tools, no query pack)
    println!();
    println!("=== diagnose ({}) ===", s.reasoning_model);
    let diagnose_prompt = prompt("diagnose")?;
    let r1 = run_step(
        &client,
        Step {
            label: "diagnose (discover + SELECT)",
            model: &s.reasoning_model,
            effort: &s.reasoning_effort,
            instructions: &diagnose_prompt,
            input: format!("Alert:\n{}\n\n{tenancy}", serde_json::to_string_pretty(&alert)?),
            server: Some(&ro),
            output: Some(agent::diagnosis_schema()),
            max_turns: 20,
        },
    )
    .await?;
    let diag: Diagnosis = agent::parse_output("diagnose", &r1.text)?;
    ledger.record(r1.usage);
    println!("{}", diag.summary);
    println!("capacity: {}", serde_json::to_string(&diag.capacity)?);
    for w in &diag.warning_events {
        println!("  warning: {w}");
    }
    println!("hypothesis: {}", diag.hypothesis);
    payload.insert("diagnosis".into(), serde_json::to_value(&diag)?);

    // 2. propose (reasoning tier, discovery tools: it finds the mutation's IO contract itself)
    println!();
    println!("=== propose ({}) ===", s.reasoning_model);
    let propose_prompt = prompt("propose")?;
    let r2 = run_step(
        &client,
        Step {
            label: "propose (discover contract)",
            model: &s.reasoning_model,
            effort: &s.reasoning_effort,
            instructions: &propose_prompt,
            input: format!("Diagnosis:\n{}\n\n{tenancy}", serde_json::to_string_pretty(&diag)?),
            server: Some(&ro),
            output: Some(agent::proposal_schema()),
            max_turns: 16,
        },
    )
    .await?;
    let prop: Proposal = agent::parse_output("propose", &r2.text)?;
    ledger.record(r2.usage);
    println!("action: {}  target: {}  resource: {}  new_replicas: {}", prop.action, prop.target, prop.resource, prop.new_replicas);
    println!("rationale: {}", prop.rationale);
    println!("blast radius: {}", prop.blast_radius);
    println!("statement: {}", prop.statement);
    println!("verification: {}", prop.verification_select);
    println!("rollback: {}", prop.rollback_statement);
    payload.insert("proposal".into(), serde_json::to_value(&prop)?);

    // 3. gate (allowlist check, then the human) -> 4. execute -> 5. verify
    let mut outcome = "no_action";
    let mut execution: Option<gate::Execution> = None;
    let mut verification: Vec<Value> = Vec::new();
    if prop.action == "scale_out_by_one" {
        if prop.new_replicas != diag.capacity.spec_replicas + 1 {
            println!("proposal outside the menu bounds (new_replicas {} vs spec {} + 1): treated as no_action", prop.new_replicas, diag.capacity.spec_replicas);
            ledger.notes.push("proposal outside menu bounds: no mutation".into());
        } else {
            let verification_sql = validate_proposed_select(&ro, &prop.verification_select).await;
            let proposal_id = format!("prop-{}", cost::short_id(6));
            match gate::prepare_proposed(&s, &proposal_id, &prop.statement, prop.new_replicas, verification_sql) {
                Err(e) => {
                    outcome = "rejected";
                    println!();
                    println!("=== approval gate ===");
                    println!("statement rejected before approval: {e:#}");
                    println!("{}", prop.statement);
                    ledger.notes.push(format!("gate rejected the proposed statement: {e:#} (0 mutation calls)"));
                }
                Ok(pending) => {
                    payload.insert("pending".into(), serde_json::to_value(&pending)?);
                    let approval = if decline_flag {
                        gate::print_gate(&pending);
                        println!("--decline given: declining at the gate");
                        None
                    } else if approve_flag {
                        Some(gate::flag_approval(&pending))
                    } else {
                        gate::ask_terminal(&pending)
                    };
                    match approval {
                        None => {
                            outcome = "declined";
                            println!("declined - no mutation will run");
                            ledger.notes.push("gate declined: no mutation executed (0 mutation calls)".into());
                        }
                        Some(a) => {
                            let exec = gate::execute_approved(&s, &pending, Some(&a)).await?;
                            ledger.record(gate::execution_usage());
                            payload.insert("execution".into(), serde_json::to_value(&exec)?);
                            execution = Some(exec);
                            let mut v = StepUsage::new("verify (SELECT poll)", "-");
                            let (ok, rows) = poll_recovery(&ro, &s, pending.replicas, pending.verification_sql.as_deref(), &mut v).await?;
                            ledger.record(v);
                            verification = rows;
                            outcome = if ok { "recovered" } else { "not_recovered" };
                            println!("verification: {}", if ok { "ready replicas match the approved count" } else { "timed out before ready replicas matched" });
                        }
                    }
                }
            }
        }
    } else {
        println!("no_action proposed: nothing to gate");
    }
    payload.insert("verification".into(), Value::Array(verification.clone()));

    // 6. close-out (sweep tier, no tools)
    println!();
    println!("=== close-out ({}) ===", s.sweep_model);
    let facts = json!({
        "alert": alert,
        "diagnosis": diag,
        "proposal": prop,
        "execution": execution,
        "verification": verification,
        "outcome": outcome,
    });
    let closeout_prompt = load_prompt(&s.prompts_dir(), "closeout", &ctx)?;
    let r3 = run_step(
        &client,
        Step {
            label: "close-out note",
            model: &s.sweep_model,
            effort: &s.sweep_effort,
            instructions: &closeout_prompt,
            input: serde_json::to_string_pretty(&facts)?,
            server: None,
            output: Some(agent::closeout_schema()),
            max_turns: 2,
        },
    )
    .await?;
    let close: Closeout = agent::parse_output("close-out", &r3.text)?;
    ledger.record(r3.usage);
    println!("{}: {}", close.outcome, close.summary);
    payload.insert("closeout".into(), serde_json::to_value(&close)?);
    payload.insert("outcome".into(), Value::String(outcome.into()));

    ro.shutdown().await?;
    let record = ledger.save(&s.runs_dir(), payload)?;
    ledger.print_summary(&s.audit_log, Some(&record));
    Ok(())
}

/// `sre run --reset`: put the deployment back to one replica through the same gate. Operator
/// tooling: no model call, the code-owned `sre/reset_scale` statement, the same allowlist check,
/// assertion and verification.
async fn run_reset(approve_flag: bool, decline_flag: bool) -> Result<()> {
    let s = Settings::load(false)?;
    banner("agentic sre - reset", "scale the target back to 1 replica through the approval gate (no model)");
    let mut ledger = Ledger::new("sre-reset", Pricing::load(&s.repo_root)?);
    let mut payload: Map<String, Value> = Map::new();
    let proposal_id = format!("reset-{}", cost::short_id(6));
    let pending = gate::prepare_reset(&s, &proposal_id)?;
    payload.insert("pending".into(), serde_json::to_value(&pending)?);
    let approval = if decline_flag {
        gate::print_gate(&pending);
        println!("--decline given: declining at the gate");
        None
    } else if approve_flag {
        Some(gate::flag_approval(&pending))
    } else {
        gate::ask_terminal(&pending)
    };
    let mut outcome = "declined";
    if let Some(a) = approval {
        let exec = gate::execute_approved(&s, &pending, Some(&a)).await?;
        ledger.record(gate::execution_usage());
        payload.insert("execution".into(), serde_json::to_value(&exec)?);
        let ro = mcp::start_read_only(&s).await?;
        let mut v = StepUsage::new("verify (SELECT poll)", "-");
        let (ok, rows) = poll_recovery(&ro, &s, 1, None, &mut v).await?;
        ledger.record(v);
        ro.shutdown().await?;
        payload.insert("verification".into(), Value::Array(rows));
        outcome = if ok { "recovered" } else { "not_recovered" };
    } else {
        println!("declined - no mutation will run");
        ledger.notes.push("gate declined: no mutation executed (0 mutation calls)".into());
    }
    payload.insert("outcome".into(), Value::String(outcome.into()));
    let record = ledger.save(&s.runs_dir(), payload)?;
    ledger.print_summary(&s.audit_log, Some(&record));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cli_parses_run_flags() {
        let c = Cli::try_parse_from(["sre", "run", "--approve"]).unwrap();
        assert!(matches!(c.command, Command::Run { approve: true, decline: false, reset: false }));
        let c = Cli::try_parse_from(["sre", "run", "--reset", "--decline"]).unwrap();
        assert!(matches!(c.command, Command::Run { approve: false, decline: true, reset: true }));
        assert!(Cli::try_parse_from(["sre", "run", "--approve", "--decline"]).is_err());
        assert!(Cli::try_parse_from(["sre", "validate-queries"]).is_ok());
        assert!(Cli::try_parse_from(["sre", "alert", "--symptom", "x"]).is_ok());
    }

    #[test]
    fn as_i64_accepts_numbers_and_strings() {
        assert_eq!(as_i64(&json!(2)), Some(2));
        assert_eq!(as_i64(&json!("3")), Some(3));
        assert_eq!(as_i64(&json!(null)), None);
    }
}
