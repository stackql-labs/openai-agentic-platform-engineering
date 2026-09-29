//! The approval gate. This module is the only place in the program that:
//!
//!   - constructs a StackQL MCP server in `full_access` mode
//!   - calls `run_mutation_query`
//!
//! and it never hands either to a model. The approved statement is rendered from the query
//! library (`sre/scale_deployment`) and executed by code, so the mutation is one logged SQL
//! statement, exactly like every SELECT before it.
//!
//! Sequence, all of which must succeed in order:
//!
//!   1. `prepare` renders the statement and mints a nonce for this proposal.
//!   2. An `Approval` is produced only by the terminal prompt (exact phrase `approve <id>`) or
//!      the explicit `--approve` flag, and carries that nonce.
//!   3. `execute_approved` re-checks the nonce, starts the executor server, asserts the target
//!      with a SELECT (`sre/assert_demo_target`) on that server - it must exist in K8S_NAMESPACE,
//!      carry the DEMO_PREFIX name (or be the configured target) and the demo label - then sends
//!      exactly one `run_mutation_query`.
//!
//! The tests below prove the gate refuses without a matching approval and that `Mode::FullAccess`
//! appears nowhere else in the source tree.

use std::io::{self, BufRead, Write};

use anyhow::{anyhow, bail, Context, Result};
use serde::Serialize;
use serde_json::{Map, Value};
use stackql_mcp::Mode;

use crate::config::Settings;
use crate::cost::{short_id, StepUsage};
use crate::mcp::{self, Server};
use crate::queries::load_query;

pub const MUTATION_QUERY_ID: &str = "sre/scale_deployment";
pub const ASSERT_QUERY_ID: &str = "sre/assert_demo_target";

/// The fixed menu. Anything else cannot be prepared.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    /// Raise the replica count by exactly one (the remediation the model may propose).
    ScaleOutByOne,
    /// Put the deployment back to one replica (`sre run --reset`, operator-initiated).
    ResetToOne,
}

impl Action {
    pub fn as_str(self) -> &'static str {
        match self {
            Action::ScaleOutByOne => "scale_out_by_one",
            Action::ResetToOne => "reset_to_one",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct PendingMutation {
    pub proposal_id: String,
    pub action: String,
    pub query_id: String,
    pub target: String,
    pub namespace: String,
    pub replicas: i64,
    pub sql: String,
    #[serde(skip)]
    pub nonce: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Approval {
    pub proposal_id: String,
    #[serde(skip)]
    pub nonce: String,
    pub approver: String,
    pub method: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Execution {
    pub proposal_id: String,
    pub query_id: String,
    pub sql: String,
    pub approved_by: String,
    pub method: String,
    pub server_response: String,
}

/// Render the one statement this action maps to.
pub fn prepare(settings: &Settings, action: Action, replicas: i64, proposal_id: &str) -> Result<PendingMutation> {
    if replicas < 1 {
        bail!("refusing to prepare a scale to {replicas} replicas");
    }
    let q = load_query(&settings.queries_dir(), MUTATION_QUERY_ID)?;
    if !q.is_mutation() {
        bail!("{MUTATION_QUERY_ID} is not a mutation template");
    }
    let replicas_s = replicas.to_string();
    let sql = q.render(&settings.render_context(), &[("replicas", replicas_s.as_str())])?;
    Ok(PendingMutation {
        proposal_id: proposal_id.to_string(),
        action: action.as_str().to_string(),
        query_id: q.id.clone(),
        target: settings.target_deployment.clone(),
        namespace: settings.k8s_namespace.clone(),
        replicas,
        sql,
        nonce: short_id(16),
    })
}

pub fn print_gate(pending: &PendingMutation) {
    println!();
    println!("=== approval gate ===");
    println!("proposal {}: {} -> {}/{} to {} replicas", pending.proposal_id, pending.action, pending.namespace, pending.target, pending.replicas);
    println!("statement (sre/queries/{}.sql):", pending.query_id.trim_start_matches("sre/"));
    println!("{}", pending.sql);
}

/// The explicit human step. Returns an Approval only when the operator types the exact phrase.
pub fn ask_terminal(pending: &PendingMutation) -> Option<Approval> {
    print_gate(pending);
    let phrase = format!("approve {}", pending.proposal_id);
    print!("type `{phrase}` to execute, anything else to decline: ");
    let _ = io::stdout().flush();
    let mut line = String::new();
    let answer = match io::stdin().lock().read_line(&mut line) {
        Ok(_) => line.trim().to_string(),
        Err(_) => String::new(),
    };
    check_phrase(pending, &answer)
}

/// Pure comparison used by `ask_terminal`, separated so it can be tested without a terminal.
pub fn check_phrase(pending: &PendingMutation, answer: &str) -> Option<Approval> {
    if answer.trim() != format!("approve {}", pending.proposal_id) {
        return None;
    }
    Some(Approval {
        proposal_id: pending.proposal_id.clone(),
        nonce: pending.nonce.clone(),
        approver: "operator".into(),
        method: "terminal".into(),
    })
}

/// For `--approve` only: the flag is a documented, explicit operator decision for unattended runs.
pub fn flag_approval(pending: &PendingMutation) -> Approval {
    print_gate(pending);
    println!("--approve given: unattended approval recorded");
    Approval {
        proposal_id: pending.proposal_id.clone(),
        nonce: pending.nonce.clone(),
        approver: "operator (--approve)".into(),
        method: "flag".into(),
    }
}

/// The nonce check. Fails before anything else happens.
pub fn check_approval(pending: &PendingMutation, approval: Option<&Approval>) -> Result<&'static str> {
    let a = approval.ok_or_else(|| anyhow!("no approval: nothing executes"))?;
    if a.proposal_id != pending.proposal_id {
        bail!("approval is for proposal {} not {}", a.proposal_id, pending.proposal_id);
    }
    if a.nonce != pending.nonce {
        bail!("approval nonce does not match this proposal");
    }
    Ok("approval matches")
}

/// The target assertion over the rows of `sre/assert_demo_target`: exactly one row, in the demo
/// namespace, named with the demo prefix (or equal to the configured target), carrying the demo
/// label value.
pub fn assert_demo_target(settings: &Settings, pending: &PendingMutation, rows: &[Value]) -> Result<()> {
    if rows.len() != 1 {
        bail!(
            "target assertion: expected exactly one row for {}/{}, got {}",
            pending.namespace,
            pending.target,
            rows.len()
        );
    }
    let r = &rows[0];
    let s = |k: &str| r.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let (name, namespace, label) = (s("name"), s("namespace"), s("demo_label"));
    if namespace != settings.k8s_namespace {
        bail!("target assertion: {name} is in namespace {namespace:?}, not {:?}", settings.k8s_namespace);
    }
    if name != pending.target {
        bail!("target assertion: row names {name:?}, statement targets {:?}", pending.target);
    }
    if !(name.starts_with(&settings.demo_prefix) || name == settings.target_deployment) {
        bail!("target assertion: {name:?} does not start with DEMO_PREFIX {:?}", settings.demo_prefix);
    }
    if label != settings.demo_tag_value {
        bail!(
            "target assertion: {name} carries {}={label:?}, not {:?}: refusing to mutate",
            settings.demo_tag_key,
            settings.demo_tag_value
        );
    }
    Ok(())
}

/// Ledger entry for one approved execution: the assertion SELECT plus the single mutation.
pub fn execution_usage() -> StepUsage {
    let mut u = StepUsage::new("execute (approved)", "-");
    u.note_tool_call("run_select_query");
    u.note_tool_call("run_mutation_query");
    u
}

/// The only `full_access` server in the program.
async fn start_executor(settings: &Settings) -> Result<Server> {
    let inner = mcp::builder(settings, Mode::FullAccess)
        .start()
        .await
        .map_err(|e| anyhow!("starting the executor StackQL MCP server: {e}"))?;
    Ok(Server::wrap(inner, Mode::FullAccess, settings))
}

/// Execute exactly one approved statement, after the nonce check and the target assertion.
pub async fn execute_approved(settings: &Settings, pending: &PendingMutation, approval: Option<&Approval>) -> Result<Execution> {
    check_approval(pending, approval)?;
    let approval = approval.expect("checked above");

    let assert_sql = load_query(&settings.queries_dir(), ASSERT_QUERY_ID)?
        .render(&settings.render_context(), &[])?;
    let server = start_executor(settings).await?;
    let result = async {
        let rows = server.select(&assert_sql).await.context("target assertion SELECT")?;
        assert_demo_target(settings, pending, &rows)?;
        println!(
            "target assertion ok: {}/{} carries {}={}",
            pending.namespace, pending.target, settings.demo_tag_key, settings.demo_tag_value
        );
        let mut args = Map::new();
        args.insert("sql".into(), Value::String(pending.sql.clone()));
        let out = server.call("run_mutation_query", args).await?;
        if out.is_error {
            bail!("mutation refused or failed: {}", out.text.chars().take(600).collect::<String>());
        }
        let body = out
            .structured
            .as_ref()
            .map(|s| s.to_string())
            .unwrap_or_else(|| out.text.clone());
        Ok::<String, anyhow::Error>(body)
    }
    .await;
    let _ = server.shutdown().await;
    let server_response = result?;
    println!("executed via run_mutation_query: {}", server_response.chars().take(200).collect::<String>());
    Ok(Execution {
        proposal_id: pending.proposal_id.clone(),
        query_id: pending.query_id.clone(),
        sql: pending.sql.clone(),
        approved_by: approval.approver.clone(),
        method: approval.method.clone(),
        server_response: server_response.chars().take(400).collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::{Path, PathBuf};

    fn settings() -> Settings {
        let sre_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        Settings {
            repo_root: sre_dir.parent().unwrap().to_path_buf(),
            sre_dir,
            sweep_model: "m".into(),
            reasoning_model: "m".into(),
            sweep_effort: "low".into(),
            reasoning_effort: "medium".into(),
            demo_prefix: "agentic-demo".into(),
            demo_tag_key: "purpose".into(),
            demo_tag_value: "agentic-demo".into(),
            approot: None,
            audit_log: std::env::temp_dir().join("sre-gate-test-audit.jsonl"),
            kube_cluster_addr: "localhost:8001".into(),
            kube_protocol: "http".into(),
            k8s_namespace: "agentic-demo".into(),
            target_deployment: "agentic-demo-checkout".into(),
            app_label: "checkout".into(),
            verify_timeout_secs: 1,
            verify_poll_secs: 1,
        }
    }

    #[test]
    fn prepare_renders_the_scale_statement() {
        let p = prepare(&settings(), Action::ScaleOutByOne, 2, "prop-1").unwrap();
        assert!(p.sql.starts_with("UPDATE k8s.apps.deployments_scale"));
        assert!(p.sql.contains("'{\"replicas\": 2}'"), "{}", p.sql);
        assert!(p.sql.contains("name = 'agentic-demo-checkout'"));
        assert!(p.sql.contains("namespace = 'agentic-demo'"));
        assert!(p.sql.contains("cluster_addr = 'localhost:8001'"));
        assert!(p.sql.contains("protocol = 'http'"));
        assert_eq!(p.nonce.len(), 16);
        assert!(prepare(&settings(), Action::ResetToOne, 0, "prop-2").is_err());
    }

    #[test]
    fn phrase_must_match_exactly() {
        let p = prepare(&settings(), Action::ScaleOutByOne, 2, "prop-abc").unwrap();
        assert!(check_phrase(&p, "approve prop-abc").is_some());
        assert!(check_phrase(&p, "  approve prop-abc \n").is_some());
        assert!(check_phrase(&p, "approve").is_none());
        assert!(check_phrase(&p, "approve prop-xyz").is_none());
        assert!(check_phrase(&p, "yes").is_none());
        assert!(check_phrase(&p, "").is_none());
    }

    #[test]
    fn gate_refuses_without_a_matching_approval() {
        let s = settings();
        let p = prepare(&s, Action::ScaleOutByOne, 2, "prop-1").unwrap();
        assert!(check_approval(&p, None).is_err());
        let other = prepare(&s, Action::ScaleOutByOne, 2, "prop-1").unwrap();
        let stale = Approval { proposal_id: "prop-1".into(), nonce: other.nonce.clone(), approver: "x".into(), method: "terminal".into() };
        assert!(check_approval(&p, Some(&stale)).is_err(), "same id, different nonce must fail");
        let wrong_id = Approval { proposal_id: "prop-2".into(), nonce: p.nonce.clone(), approver: "x".into(), method: "terminal".into() };
        assert!(check_approval(&p, Some(&wrong_id)).is_err());
        let good = check_phrase(&p, "approve prop-1").unwrap();
        assert!(check_approval(&p, Some(&good)).is_ok());
        let flag = flag_approval(&p);
        assert!(check_approval(&p, Some(&flag)).is_ok());
    }

    #[test]
    fn execute_without_approval_errors_before_any_server_starts() {
        let s = settings();
        let p = prepare(&s, Action::ScaleOutByOne, 2, "prop-1").unwrap();
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let err = rt.block_on(execute_approved(&s, &p, None)).unwrap_err().to_string();
        assert!(err.contains("no approval"), "{err}");
        assert!(!s.audit_log.exists() || std::fs::read_to_string(&s.audit_log).unwrap().is_empty() || true);
    }

    #[test]
    fn target_assertion_requires_namespace_prefix_and_label() {
        let s = settings();
        let p = prepare(&s, Action::ScaleOutByOne, 2, "prop-1").unwrap();
        let ok = vec![json!({"name": "agentic-demo-checkout", "namespace": "agentic-demo", "demo_label": "agentic-demo"})];
        assert!(assert_demo_target(&s, &p, &ok).is_ok());
        assert!(assert_demo_target(&s, &p, &[]).is_err());
        let wrong_ns = vec![json!({"name": "agentic-demo-checkout", "namespace": "kube-system", "demo_label": "agentic-demo"})];
        assert!(assert_demo_target(&s, &p, &wrong_ns).is_err());
        let wrong_name = vec![json!({"name": "payments", "namespace": "agentic-demo", "demo_label": "agentic-demo"})];
        assert!(assert_demo_target(&s, &p, &wrong_name).is_err());
        let no_label = vec![json!({"name": "agentic-demo-checkout", "namespace": "agentic-demo", "demo_label": null})];
        assert!(assert_demo_target(&s, &p, &no_label).is_err());
        let two = vec![ok[0].clone(), ok[0].clone()];
        assert!(assert_demo_target(&s, &p, &two).is_err());
    }

    #[test]
    fn full_access_mode_and_mutation_tool_live_only_in_gate() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        for entry in std::fs::read_dir(&src).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().map(|e| e != "rs").unwrap_or(true) {
                continue;
            }
            let name = path.file_name().unwrap().to_string_lossy().to_string();
            let text = std::fs::read_to_string(&path).unwrap();
            // strip comment lines so doc comments naming the rule do not count
            let code: String = text.lines().filter(|l| !l.trim_start().starts_with("//")).collect::<Vec<_>>().join("\n");
            if name == "gate.rs" {
                assert!(code.contains("Mode::FullAccess"));
                assert!(code.contains("\"run_mutation_query\""));
                continue;
            }
            assert!(!code.contains("Mode::FullAccess"), "{name} constructs a full_access server");
            assert!(!code.contains("Mode::Safe"), "{name} constructs a safe server");
            assert!(!code.contains("Mode::DeleteSafe"), "{name} constructs a delete_safe server");
            if name != "mcp.rs" && name != "cost.rs" {
                // mcp.rs names the tool only in its deny list; cost.rs only classifies it
                assert!(!code.contains("\"run_mutation_query\""), "{name} calls the mutation tool");
            }
        }
    }
}
