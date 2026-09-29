//! Settings: the repo-root `.env` plus defaults. Nothing here is hardcoded that the
//! coordinator's `.env.example` does not document - model ids, tenancy and the target
//! deployment all come from the environment.

use std::collections::BTreeMap;
use std::env;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

#[derive(Debug, Clone)]
pub struct Settings {
    /// The `sre/` directory (`prompts/` and `queries/` live under it).
    pub sre_dir: PathBuf,
    /// The repository root (`.env`, `pricing.json`, `runs/` live there).
    pub repo_root: PathBuf,
    pub sweep_model: String,
    pub reasoning_model: String,
    pub sweep_effort: String,
    pub reasoning_effort: String,
    pub demo_prefix: String,
    pub demo_tag_key: String,
    pub demo_tag_value: String,
    /// STACKQL_APPROOT resolved against the repo root; None means the crate default (~/.stackql).
    pub approot: Option<PathBuf>,
    /// Client-side statement log (the embedded crate launches the server with its own audit off).
    pub audit_log: PathBuf,
    pub kube_cluster_addr: String,
    pub kube_protocol: String,
    pub k8s_namespace: String,
    /// The deployment the alert names and the gate may scale. Default `<DEMO_PREFIX>-checkout`.
    pub target_deployment: String,
    /// The `app` label selecting the deployment's pods.
    pub app_label: String,
    pub verify_timeout_secs: u64,
    pub verify_poll_secs: u64,
}

fn var_or(name: &str, default: &str) -> String {
    match env::var(name) {
        Ok(v) if !v.trim().is_empty() => v.trim().to_string(),
        _ => default.to_string(),
    }
}

fn var_required(name: &str) -> Result<String> {
    match env::var(name) {
        Ok(v) if !v.trim().is_empty() => Ok(v.trim().to_string()),
        _ => anyhow::bail!("{name} is not set - add it to the repo-root .env"),
    }
}

/// Locate the `sre/` directory: SRE_DIR, the cwd when it is the crate, `<cwd>/sre`, else the
/// compile-time manifest directory.
pub fn locate_sre_dir() -> PathBuf {
    if let Ok(d) = env::var("SRE_DIR") {
        if !d.trim().is_empty() {
            return PathBuf::from(d);
        }
    }
    if let Ok(cwd) = env::current_dir() {
        if cwd.join("queries").is_dir() && cwd.join("Cargo.toml").is_file() {
            return cwd;
        }
        let nested = cwd.join("sre");
        if nested.join("queries").is_dir() && nested.join("Cargo.toml").is_file() {
            return nested;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

impl Settings {
    /// Load `<repo root>/.env` (existing process variables win) and read every setting.
    /// `require_models` is false for subcommands that never call OpenAI (setup, alert, validate).
    pub fn load(require_models: bool) -> Result<Self> {
        let sre_dir = locate_sre_dir();
        let repo_root = sre_dir
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| sre_dir.clone());
        let env_file = repo_root.join(".env");
        if env_file.is_file() {
            dotenvy::from_path(&env_file)
                .with_context(|| format!("reading {}", env_file.display()))?;
        }

        let (sweep_model, reasoning_model) = if require_models {
            (var_required("SWEEP_MODEL")?, var_required("REASONING_MODEL")?)
        } else {
            (var_or("SWEEP_MODEL", ""), var_or("REASONING_MODEL", ""))
        };
        let demo_prefix = var_or("DEMO_PREFIX", "agentic-demo");
        let approot = {
            let raw = var_or("STACKQL_APPROOT", "");
            if raw.is_empty() {
                None
            } else {
                let p = PathBuf::from(&raw);
                Some(if p.is_absolute() { p } else { repo_root.join(p) })
            }
        };
        let audit_log = {
            let p = PathBuf::from(var_or("STACKQL_MCP_AUDIT_LOG", "runs/stackql-mcp-audit.jsonl"));
            if p.is_absolute() {
                p
            } else {
                repo_root.join(p)
            }
        };
        Ok(Settings {
            target_deployment: var_or("SRE_TARGET_DEPLOYMENT", &format!("{demo_prefix}-checkout")),
            app_label: var_or("SRE_APP_LABEL", "checkout"),
            sre_dir,
            repo_root,
            sweep_model,
            reasoning_model,
            sweep_effort: var_or("SWEEP_REASONING_EFFORT", "low"),
            reasoning_effort: var_or("REASONING_REASONING_EFFORT", "medium"),
            demo_prefix,
            demo_tag_key: var_or("DEMO_TAG_KEY", "purpose"),
            demo_tag_value: var_or("DEMO_TAG_VALUE", "agentic-demo"),
            approot,
            audit_log,
            kube_cluster_addr: var_or("KUBE_CLUSTER_ADDR", "localhost:8001"),
            kube_protocol: var_or("KUBE_PROTOCOL", "http"),
            k8s_namespace: var_or("K8S_NAMESPACE", "agentic-demo"),
            verify_timeout_secs: var_or("SRE_VERIFY_TIMEOUT_SECS", "120")
                .parse()
                .context("SRE_VERIFY_TIMEOUT_SECS must be an integer")?,
            verify_poll_secs: var_or("SRE_VERIFY_POLL_SECS", "5")
                .parse()
                .context("SRE_VERIFY_POLL_SECS must be an integer")?,
        })
    }

    pub fn queries_dir(&self) -> PathBuf {
        self.sre_dir.join("queries")
    }

    /// The intent prompts, read at run time so they can be edited without a rebuild.
    pub fn prompts_dir(&self) -> PathBuf {
        self.sre_dir.join("prompts")
    }

    pub fn runs_dir(&self) -> PathBuf {
        self.repo_root.join("runs")
    }

    /// The values query placeholders resolve against, keyed by lower-case param name. Code
    /// overrides (e.g. `replicas`) are layered on top by the caller; anything not here falls back
    /// to the matching upper-case environment variable.
    pub fn render_context(&self) -> BTreeMap<String, String> {
        let mut m = BTreeMap::new();
        m.insert("kube_cluster_addr".into(), self.kube_cluster_addr.clone());
        m.insert("kube_protocol".into(), self.kube_protocol.clone());
        m.insert("k8s_namespace".into(), self.k8s_namespace.clone());
        m.insert("sre_target_deployment".into(), self.target_deployment.clone());
        m.insert("sre_app_label".into(), self.app_label.clone());
        m.insert("demo_prefix".into(), self.demo_prefix.clone());
        m.insert("demo_tag_key".into(), self.demo_tag_key.clone());
        m.insert("demo_tag_value".into(), self.demo_tag_value.clone());
        m
    }
}
