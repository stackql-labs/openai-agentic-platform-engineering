//! The approval gate. This module is the only place in the program that:
//!
//!   - constructs a StackQL MCP server in `full_access` mode
//!   - calls `run_mutation_query`
//!
//! and it never hands either to a model. The model discovers the scale mutation's IO contract
//! itself (`list_methods` / `describe_method`) and proposes the exact statement in its structured
//! output; this module decides whether that statement may run, and code runs it.
//!
//! Sequence, all of which must succeed in order:
//!
//!   1. `prepare_proposed` checks the proposed statement against the allowlist (`check_statement`:
//!      one statement, verb UPDATE or REPLACE, resource `k8s.apps.deployments_scale` or
//!      `k8s.apps.deployments`, WHERE pinned to K8S_NAMESPACE, the target name, cluster_addr and
//!      protocol, no OR, no other verb, the replica count it carries equal to the proposed one) and
//!      mints a nonce. `prepare_reset` renders the code-owned `sre/reset_scale` for `--reset` and
//!      runs it through the same check.
//!   2. An `Approval` is produced only by the terminal prompt (exact phrase `approve <id>`) or
//!      the explicit `--approve` flag, and carries that nonce.
//!   3. `execute_approved` re-checks the nonce, starts the executor server, asserts the target
//!      with the code-owned SELECT (`sre/assert_demo_target`) on that server - it must exist in
//!      K8S_NAMESPACE, carry the DEMO_PREFIX name (or be the configured target) and the demo label -
//!      then sends exactly one `run_mutation_query`.
//!
//! The tests below prove the gate refuses without a matching approval, that the allowlist rejects
//! anything but the one permitted shape, and that `Mode::FullAccess` appears nowhere else.

use std::io::{self, BufRead, Write};

use anyhow::{anyhow, bail, Context, Result};
use serde::Serialize;
use serde_json::{Map, Value};
use stackql_mcp::Mode;

use crate::config::Settings;
use crate::cost::{short_id, StepUsage};
use crate::mcp::{self, Server};
use crate::queries::load_query;

pub const RESET_QUERY_ID: &str = "sre/reset_scale";
pub const ASSERT_QUERY_ID: &str = "sre/assert_demo_target";

/// The allowlist: (verb, provider.service.resource) pairs a proposed statement may use.
pub const ALLOWED_VERBS: &[&str] = &["UPDATE", "REPLACE"];
pub const ALLOWED_RESOURCES: &[&str] = &["k8s.apps.deployments_scale", "k8s.apps.deployments"];

/// Any of these as a token outside a string literal, other than the leading verb, rejects the
/// statement: it is either a second statement, a subquery, or a widening of the WHERE clause.
const FORBIDDEN_TOKENS: &[&str] = &[
    "SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE", "EXEC", "DROP", "CREATE", "ALTER", "UNION", "WITH", "OR",
];

/// Where a pending statement came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    /// Discovered and written by the model, checked by `check_statement`.
    ModelProposed,
    /// Rendered from `sre/queries/reset_scale.sql` for `sre run --reset` (no model involved).
    CodeOwned,
}

impl Source {
    pub fn as_str(self) -> &'static str {
        match self {
            Source::ModelProposed => "model-proposed",
            Source::CodeOwned => "code-owned",
        }
    }
}

/// What `check_statement` established about a statement.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StatementCheck {
    pub verb: String,
    pub resource: String,
    pub replicas: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct PendingMutation {
    pub proposal_id: String,
    pub action: String,
    pub source: String,
    pub verb: String,
    pub resource: String,
    pub target: String,
    pub namespace: String,
    pub replicas: i64,
    pub sql: String,
    /// The model-proposed verification SELECT once it passed `validate_select_query`; None means
    /// the poll uses the code-owned `sre/verify_replicas`.
    pub verification_sql: Option<String>,
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
    pub source: String,
    pub resource: String,
    pub sql: String,
    pub approved_by: String,
    pub method: String,
    pub server_response: String,
}

/// One lexical item of a statement. String literals keep their content (with `''` unescaped) so
/// a predicate value is compared as a whole and text inside a literal never counts as syntax.
#[derive(Debug, Clone, PartialEq, Eq)]
enum Tok {
    Word(String),
    Lit(String),
    Sym(char),
}

/// Lex a statement into words (identifiers, keywords, numbers, dotted names), single-quoted
/// literals and single-character symbols. Comments and unterminated literals are errors.
fn lex(sql: &str) -> Result<Vec<Tok>> {
    let chars: Vec<char> = sql.chars().collect();
    let mut toks = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_whitespace() {
            i += 1;
        } else if c == '\'' {
            let mut lit = String::new();
            i += 1;
            loop {
                match chars.get(i) {
                    None => bail!("statement check: unterminated string literal"),
                    Some('\'') if chars.get(i + 1) == Some(&'\'') => {
                        lit.push('\'');
                        i += 2;
                    }
                    Some('\'') => {
                        i += 1;
                        break;
                    }
                    Some(ch) => {
                        lit.push(*ch);
                        i += 1;
                    }
                }
            }
            toks.push(Tok::Lit(lit));
        } else if c.is_ascii_alphanumeric() || c == '_' || c == '.' {
            let start = i;
            while i < chars.len() && (chars[i].is_ascii_alphanumeric() || chars[i] == '_' || chars[i] == '.') {
                i += 1;
            }
            toks.push(Tok::Word(chars[start..i].iter().collect()));
        } else if (c == '-' && chars.get(i + 1) == Some(&'-')) || (c == '/' && chars.get(i + 1) == Some(&'*')) {
            bail!("statement check: comments are not accepted in a statement");
        } else {
            toks.push(Tok::Sym(c));
            i += 1;
        }
    }
    Ok(toks)
}

/// `key = 'value'` present as a whole predicate: a literal token equal to `value` preceded by `=`
/// and the key word (case-insensitive).
fn pins(toks: &[Tok], key: &str, value: &str) -> bool {
    toks.windows(3).any(|w| match (&w[0], &w[1], &w[2]) {
        (Tok::Word(k), Tok::Sym('='), Tok::Lit(v)) => k.eq_ignore_ascii_case(key) && v == value,
        _ => false,
    })
}

/// The replica count the statement carries (`"replicas": N` inside a literal).
fn replicas_in(toks: &[Tok]) -> Option<i64> {
    toks.iter().find_map(|t| match t {
        Tok::Lit(l) => {
            let idx = l.find("\"replicas\"")?;
            let rest = l[idx + "\"replicas\"".len()..].trim_start();
            let rest = rest.strip_prefix(':')?.trim_start();
            let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
            digits.parse().ok()
        }
        _ => None,
    })
}

/// The allowlist check. Accepts exactly one UPDATE or REPLACE against an allowed k8s deployment
/// resource whose WHERE clause pins the demo namespace, the target name, the cluster address and
/// the protocol from settings, and whose body sets `replicas` to `expected_replicas`.
pub fn check_statement(settings: &Settings, sql: &str, target: &str, expected_replicas: i64) -> Result<StatementCheck> {
    let trimmed = sql.trim();
    if trimmed.is_empty() {
        bail!("statement check: empty statement");
    }
    let body = trimmed.trim_end_matches(';').trim_end();
    let toks = lex(body)?;
    if toks.contains(&Tok::Sym(';')) {
        bail!("statement check: more than one statement (a `;` inside the text)");
    }
    let words: Vec<&str> = toks
        .iter()
        .filter_map(|t| match t {
            Tok::Word(w) => Some(w.as_str()),
            _ => None,
        })
        .collect();
    let verb = words.first().map(|t| t.to_ascii_uppercase()).unwrap_or_default();
    if !ALLOWED_VERBS.contains(&verb.as_str()) {
        bail!("statement check: verb {verb:?} is not on the allowlist {ALLOWED_VERBS:?}");
    }
    let resource = words.get(1).map(|t| t.to_ascii_lowercase()).unwrap_or_default();
    if !ALLOWED_RESOURCES.contains(&resource.as_str()) {
        bail!("statement check: resource {resource:?} is not on the allowlist {ALLOWED_RESOURCES:?}");
    }
    for w in words.iter().skip(1) {
        let u = w.to_ascii_uppercase();
        if FORBIDDEN_TOKENS.contains(&u.as_str()) {
            bail!("statement check: {u} is not accepted inside the statement");
        }
    }
    if !words.iter().any(|w| w.eq_ignore_ascii_case("WHERE")) {
        bail!("statement check: no WHERE clause");
    }
    for (key, value) in [
        ("namespace", settings.k8s_namespace.as_str()),
        ("name", target),
        ("cluster_addr", settings.kube_cluster_addr.as_str()),
        ("protocol", settings.kube_protocol.as_str()),
    ] {
        if !pins(&toks, key, value) {
            bail!("statement check: WHERE does not pin {key} = '{value}'");
        }
    }
    let replicas = replicas_in(&toks).ok_or_else(|| anyhow!("statement check: no \"replicas\": N in the statement body"))?;
    if replicas != expected_replicas {
        bail!("statement check: statement sets replicas to {replicas}, proposal says {expected_replicas}");
    }
    if replicas < 1 {
        bail!("statement check: refusing a scale to {replicas} replicas");
    }
    Ok(StatementCheck { verb, resource, replicas })
}

/// The model-proposed statement, after the allowlist check, with a fresh nonce.
pub fn prepare_proposed(
    settings: &Settings,
    proposal_id: &str,
    statement: &str,
    replicas: i64,
    verification_sql: Option<String>,
) -> Result<PendingMutation> {
    let target = settings.target_deployment.clone();
    let check = check_statement(settings, statement, &target, replicas)?;
    Ok(PendingMutation {
        proposal_id: proposal_id.to_string(),
        action: "scale_out_by_one".into(),
        source: Source::ModelProposed.as_str().into(),
        verb: check.verb,
        resource: check.resource,
        target,
        namespace: settings.k8s_namespace.clone(),
        replicas: check.replicas,
        sql: statement.trim().trim_end_matches(';').trim().to_string(),
        verification_sql,
        nonce: short_id(16),
    })
}

/// The code-owned reset (`sre run --reset`): render `sre/reset_scale` to one replica and run it
/// through the same allowlist check.
pub fn prepare_reset(settings: &Settings, proposal_id: &str) -> Result<PendingMutation> {
    let q = load_query(&settings.queries_dir(), RESET_QUERY_ID)?;
    if !q.is_mutation() {
        bail!("{RESET_QUERY_ID} is not a mutation template");
    }
    let sql = q.render(&settings.render_context(), &[("replicas", "1")])?;
    let check = check_statement(settings, &sql, &settings.target_deployment, 1)?;
    Ok(PendingMutation {
        proposal_id: proposal_id.to_string(),
        action: "reset_to_one".into(),
        source: Source::CodeOwned.as_str().into(),
        verb: check.verb,
        resource: check.resource,
        target: settings.target_deployment.clone(),
        namespace: settings.k8s_namespace.clone(),
        replicas: 1,
        sql,
        verification_sql: None,
        nonce: short_id(16),
    })
}

pub fn print_gate(pending: &PendingMutation) {
    println!();
    println!("=== approval gate ===");
    println!(
        "proposal {}: {} -> {}/{} to {} replicas ({} {}, {})",
        pending.proposal_id, pending.action, pending.namespace, pending.target, pending.replicas, pending.verb, pending.resource, pending.source
    );
    println!("statement (allowlist check passed):");
    println!("{}", pending.sql);
    match &pending.verification_sql {
        Some(v) => println!("verification (model-proposed, validated):\n{v}"),
        None => println!("verification: code-owned sre/verify_replicas"),
    }
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

/// Execute exactly one approved statement, after the nonce check, a repeat of the allowlist check
/// and the target assertion.
pub async fn execute_approved(settings: &Settings, pending: &PendingMutation, approval: Option<&Approval>) -> Result<Execution> {
    check_approval(pending, approval)?;
    let approval = approval.expect("checked above");
    check_statement(settings, &pending.sql, &pending.target, pending.replicas)?;

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
        source: pending.source.clone(),
        resource: pending.resource.clone(),
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

    const GOOD: &str = "UPDATE k8s.apps.deployments_scale SET spec = '{\"replicas\": 2}' WHERE name = 'agentic-demo-checkout' AND namespace = 'agentic-demo' AND cluster_addr = 'localhost:8001' AND protocol = 'http'";

    fn good(replicas: i64) -> String {
        GOOD.replace("\"replicas\": 2", &format!("\"replicas\": {replicas}"))
    }

    #[test]
    fn allowlist_accepts_the_permitted_shapes() {
        let s = settings();
        let c = check_statement(&s, GOOD, "agentic-demo-checkout", 2).unwrap();
        assert_eq!(c, StatementCheck { verb: "UPDATE".into(), resource: "k8s.apps.deployments_scale".into(), replicas: 2 });
        // trailing semicolon, mixed case, tight spacing and multi-line layout are fine
        let variant = "update K8S.APPS.DEPLOYMENTS_SCALE\n  set spec='{\"replicas\":2}'\n  where name='agentic-demo-checkout'\n    and namespace='agentic-demo' and cluster_addr='localhost:8001' and protocol='http';";
        assert!(check_statement(&s, variant, "agentic-demo-checkout", 2).is_ok());
        // REPLACE on the deployment resource itself
        let replace = "REPLACE k8s.apps.deployments SET spec = '{\"replicas\": 2, \"selector\": {\"matchLabels\": {\"app\": \"checkout\"}}}' WHERE name = 'agentic-demo-checkout' AND namespace = 'agentic-demo' AND cluster_addr = 'localhost:8001' AND protocol = 'http'";
        let c = check_statement(&s, replace, "agentic-demo-checkout", 2).unwrap();
        assert_eq!((c.verb.as_str(), c.resource.as_str()), ("REPLACE", "k8s.apps.deployments"));
    }

    #[test]
    fn allowlist_rejects_everything_else() {
        let s = settings();
        let t = "agentic-demo-checkout";
        let rejects = |sql: &str, why: &str| {
            let err = check_statement(&s, sql, t, 2).map(|_| ()).unwrap_err().to_string();
            assert!(err.contains(why), "{sql}\n  expected {why:?} in {err}");
        };
        rejects("", "empty");
        rejects(&GOOD.replace("UPDATE k8s.apps.deployments_scale", "SELECT * FROM k8s.apps.deployments_scale"), "verb");
        rejects(&GOOD.replace("UPDATE", "DELETE FROM"), "verb");
        rejects(&GOOD.replace("UPDATE", "INSERT INTO"), "verb");
        rejects(&GOOD.replace("k8s.apps.deployments_scale", "k8s.core.pods"), "resource");
        rejects(&GOOD.replace("k8s.apps.deployments_scale", "k8s.apps.replica_sets"), "resource");
        rejects(&GOOD.replace("k8s.apps.deployments_scale", "aws.ec2.instances"), "resource");
        rejects(&format!("{GOOD}; DELETE FROM k8s.apps.deployments WHERE name = 'x'"), "more than one statement");
        rejects(&format!("{GOOD} OR namespace = 'kube-system'"), "OR");
        rejects(&GOOD.replace("namespace = 'agentic-demo'", "namespace = 'kube-system'"), "pin namespace");
        rejects(&GOOD.replace("name = 'agentic-demo-checkout'", "name = 'payments'"), "pin name");
        rejects(&GOOD.replace(" AND namespace = 'agentic-demo'", ""), "pin namespace");
        rejects(&GOOD.replace(" AND cluster_addr = 'localhost:8001'", ""), "pin cluster_addr");
        rejects(&GOOD.replace(" AND protocol = 'http'", ""), "pin protocol");
        rejects(&GOOD.replace("\"replicas\": 2", "\"replicas\": 3"), "proposal says");
        rejects(&GOOD.replace("'{\"replicas\": 2}'", "'{}'"), "replicas");
        rejects(&format!("{GOOD} -- comment"), "comments");
        rejects("UPDATE k8s.apps.deployments_scale SET spec = '{\"replicas\": 2}'", "WHERE");
        // the name predicate must be its own predicate, not a substring of another value
        rejects(
            &GOOD.replace("name = 'agentic-demo-checkout'", "name = 'other' AND label = 'name = ''agentic-demo-checkout'''"),
            "pin name",
        );
    }

    #[test]
    fn extra_and_predicates_keep_the_pins() {
        // an extra AND predicate narrows, never widens: still one pinned UPDATE
        let s = settings();
        let sql = GOOD.replace("WHERE", "WHERE 1 = 1 AND");
        assert!(check_statement(&s, &sql, "agentic-demo-checkout", 2).is_ok());
        // a literal containing a quote or a semicolon is data, not syntax
        let sql = format!("{GOOD} AND note = 'it''s; fine'");
        assert!(check_statement(&s, &sql, "agentic-demo-checkout", 2).is_ok());
    }

    #[test]
    fn prepare_proposed_keeps_the_statement_and_mints_a_nonce() {
        let s = settings();
        let p = prepare_proposed(&s, "prop-1", &format!("  {GOOD};\n"), 2, Some("SELECT 1".into())).unwrap();
        assert_eq!(p.sql, GOOD);
        assert_eq!(p.source, "model-proposed");
        assert_eq!(p.replicas, 2);
        assert_eq!(p.nonce.len(), 16);
        assert!(prepare_proposed(&s, "prop-2", &good(0), 0, None).is_err());
        assert!(prepare_proposed(&s, "prop-3", "SELECT 1", 2, None).is_err());
    }

    #[test]
    fn reset_is_code_owned_and_passes_the_same_check() {
        let p = prepare_reset(&settings(), "reset-1").unwrap();
        assert_eq!(p.source, "code-owned");
        assert_eq!(p.replicas, 1);
        assert!(p.sql.starts_with("UPDATE k8s.apps.deployments_scale"));
        assert!(p.sql.contains("'{\"replicas\": 1}'"), "{}", p.sql);
        assert!(p.verification_sql.is_none());
    }

    #[test]
    fn phrase_must_match_exactly() {
        let p = prepare_proposed(&settings(), "prop-abc", GOOD, 2, None).unwrap();
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
        let p = prepare_proposed(&s, "prop-1", GOOD, 2, None).unwrap();
        assert!(check_approval(&p, None).is_err());
        let other = prepare_proposed(&s, "prop-1", GOOD, 2, None).unwrap();
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
        let p = prepare_proposed(&s, "prop-1", GOOD, 2, None).unwrap();
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let err = rt.block_on(execute_approved(&s, &p, None)).unwrap_err().to_string();
        assert!(err.contains("no approval"), "{err}");
    }

    #[test]
    fn execute_rechecks_the_statement_after_approval() {
        let s = settings();
        let mut p = prepare_proposed(&s, "prop-1", GOOD, 2, None).unwrap();
        let a = flag_approval(&p);
        p.sql = format!("{GOOD}; DELETE FROM k8s.apps.deployments WHERE name = 'x'");
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let err = rt.block_on(execute_approved(&s, &p, Some(&a))).unwrap_err().to_string();
        assert!(err.contains("more than one statement"), "{err}");
    }

    #[test]
    fn target_assertion_requires_namespace_prefix_and_label() {
        let s = settings();
        let p = prepare_proposed(&s, "prop-1", GOOD, 2, None).unwrap();
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
