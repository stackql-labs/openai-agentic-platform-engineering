//! Query library loader. All SQL lives in `sre/queries/`, one file per query with a header:
//!
//! ```text
//! -- id: sre/deployment_state
//! -- providers: k8s
//! -- params: sre_target_deployment, k8s_namespace, kube_cluster_addr, kube_protocol
//! -- expected_columns: name, namespace, spec_replicas, ...
//! -- description: one line
//! <SQL with {{ param }} placeholders>
//! ```
//!
//! Placeholders resolve from explicit code overrides, then the settings context, then the
//! upper-cased environment variable of the same name. A missing value fails naming the variable.
//! Agent code never carries inline SQL: it loads by id and renders.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};

#[derive(Debug, Clone)]
pub struct Query {
    pub id: String,
    pub path: PathBuf,
    pub providers: Vec<String>,
    pub params: Vec<String>,
    pub expected_columns: Vec<String>,
    pub description: String,
    pub sql: String,
}

impl Query {
    /// True for UPDATE / REPLACE / INSERT / DELETE / EXEC templates: never handed to a model,
    /// never sent to `validate_select_query`.
    pub fn is_mutation(&self) -> bool {
        let head = self
            .sql
            .split_whitespace()
            .next()
            .unwrap_or("")
            .to_ascii_uppercase();
        matches!(head.as_str(), "UPDATE" | "REPLACE" | "INSERT" | "DELETE" | "EXEC")
    }

    /// Substitute every `{{ param }}`. `overrides` win over `ctx`, which wins over the
    /// environment (`param` -> `PARAM`).
    pub fn render(&self, ctx: &BTreeMap<String, String>, overrides: &[(&str, &str)]) -> Result<String> {
        let mut out = String::with_capacity(self.sql.len());
        let mut rest = self.sql.as_str();
        while let Some(start) = rest.find("{{") {
            out.push_str(&rest[..start]);
            let after = &rest[start + 2..];
            let end = after
                .find("}}")
                .with_context(|| format!("query {}: unterminated placeholder", self.id))?;
            let key = after[..end].trim();
            if key.is_empty() || !key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
                bail!("query {}: bad placeholder name {key:?}", self.id);
            }
            let value = overrides
                .iter()
                .find(|(k, _)| *k == key)
                .map(|(_, v)| (*v).to_string())
                .or_else(|| ctx.get(key).cloned())
                .or_else(|| std::env::var(key.to_ascii_uppercase()).ok())
                .filter(|v| !v.trim().is_empty())
                .with_context(|| {
                    format!(
                        "query {}: no value for {{{{ {key} }}}} - set {} in the repo-root .env",
                        self.id,
                        key.to_ascii_uppercase()
                    )
                })?;
            out.push_str(&value);
            rest = &after[end + 2..];
        }
        out.push_str(rest);
        Ok(out.trim().trim_end_matches(';').trim().to_string())
    }
}

fn split_list(v: &str) -> Vec<String> {
    v.split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

fn placeholders(sql: &str) -> Vec<String> {
    let mut found = Vec::new();
    let mut rest = sql;
    while let Some(start) = rest.find("{{") {
        let after = &rest[start + 2..];
        match after.find("}}") {
            Some(end) => {
                let key = after[..end].trim().to_string();
                if !found.contains(&key) {
                    found.push(key);
                }
                rest = &after[end + 2..];
            }
            None => break,
        }
    }
    found
}

/// Parse one query file. `fallback_id` is used when the header carries no `id`.
pub fn parse(text: &str, path: &Path, fallback_id: &str) -> Query {
    let mut meta: BTreeMap<String, String> = BTreeMap::new();
    let mut body: Vec<&str> = Vec::new();
    let mut in_header = true;
    for line in text.lines() {
        if in_header {
            let t = line.trim();
            if let Some(rest) = t.strip_prefix("--") {
                if let Some((k, v)) = rest.split_once(':') {
                    let k = k.trim();
                    if !k.is_empty() && k.chars().all(|c| c.is_ascii_lowercase() || c == '_') {
                        meta.insert(k.to_string(), v.trim().to_string());
                        continue;
                    }
                }
                continue; // a plain comment line inside the header
            }
            if t.is_empty() {
                continue;
            }
            in_header = false;
        }
        body.push(line);
    }
    let sql = body.join("\n").trim().trim_end_matches(';').trim().to_string();
    let declared = meta.get("params").map(|p| split_list(p)).unwrap_or_default();
    Query {
        id: meta.get("id").cloned().unwrap_or_else(|| fallback_id.to_string()),
        path: path.to_path_buf(),
        providers: meta.get("providers").map(|p| split_list(p)).unwrap_or_default(),
        params: if declared.is_empty() { placeholders(&sql) } else { declared },
        expected_columns: meta
            .get("expected_columns")
            .map(|p| split_list(p))
            .unwrap_or_default(),
        description: meta.get("description").cloned().unwrap_or_default(),
        sql,
    }
}

/// Every `*.sql` under `dir`, sorted by file name.
pub fn list_queries(dir: &Path) -> Result<Vec<Query>> {
    let mut paths: Vec<PathBuf> = fs::read_dir(dir)
        .with_context(|| format!("reading query directory {}", dir.display()))?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().map(|x| x == "sql").unwrap_or(false))
        .collect();
    paths.sort();
    let mut out = Vec::new();
    for p in paths {
        let text = fs::read_to_string(&p).with_context(|| format!("reading {}", p.display()))?;
        let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("query");
        out.push(parse(&text, &p, &format!("sre/{stem}")));
    }
    Ok(out)
}

pub fn load_query(dir: &Path, id: &str) -> Result<Query> {
    list_queries(dir)?
        .into_iter()
        .find(|q| q.id == id)
        .with_context(|| format!("no query with id {id:?} under {}", dir.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "-- id: sre/sample\n-- providers: k8s\n-- params: k8s_namespace, kube_cluster_addr, kube_protocol\n-- expected_columns: a, b\n-- description: a sample\n-- a plain comment\nSELECT a, b FROM k8s.core.pods\nWHERE namespace = '{{ k8s_namespace }}'\n  AND cluster_addr = '{{ kube_cluster_addr }}'\n  AND protocol = '{{kube_protocol}}';\n";

    fn ctx() -> BTreeMap<String, String> {
        let mut m = BTreeMap::new();
        m.insert("k8s_namespace".to_string(), "agentic-demo".to_string());
        m.insert("kube_cluster_addr".to_string(), "localhost:8001".to_string());
        m.insert("kube_protocol".to_string(), "http".to_string());
        m
    }

    #[test]
    fn parses_header_and_body() {
        let q = parse(SAMPLE, Path::new("sample.sql"), "sre/fallback");
        assert_eq!(q.id, "sre/sample");
        assert_eq!(q.providers, vec!["k8s"]);
        assert_eq!(q.params, vec!["k8s_namespace", "kube_cluster_addr", "kube_protocol"]);
        assert_eq!(q.expected_columns, vec!["a", "b"]);
        assert_eq!(q.description, "a sample");
        assert!(q.sql.starts_with("SELECT a, b"));
        assert!(!q.sql.ends_with(';'));
        assert!(!q.is_mutation());
    }

    #[test]
    fn renders_placeholders_with_override_precedence() {
        let q = parse(SAMPLE, Path::new("sample.sql"), "sre/fallback");
        let sql = q.render(&ctx(), &[("k8s_namespace", "other")]).unwrap();
        assert!(sql.contains("namespace = 'other'"));
        assert!(sql.contains("cluster_addr = 'localhost:8001'"));
        assert!(sql.contains("protocol = 'http'"));
        assert!(!sql.contains("{{"), "no placeholder survives rendering: {sql}");
    }

    #[test]
    fn missing_param_names_the_variable() {
        let q = parse(SAMPLE, Path::new("sample.sql"), "sre/fallback");
        let mut c = ctx();
        c.remove("kube_protocol");
        std::env::remove_var("KUBE_PROTOCOL");
        let err = q.render(&c, &[]).unwrap_err().to_string();
        assert!(err.contains("KUBE_PROTOCOL"), "{err}");
    }

    #[test]
    fn params_default_to_placeholders_found_in_sql() {
        let q = parse("-- id: sre/x\nSELECT 1 FROM t WHERE a = '{{ alpha }}' AND b = '{{ beta }}'", Path::new("x.sql"), "sre/x");
        assert_eq!(q.params, vec!["alpha", "beta"]);
    }

    #[test]
    fn mutation_templates_are_detected() {
        let q = parse("-- id: sre/m\nUPDATE k8s.apps.deployments_scale SET spec = '{\"replicas\": {{ replicas }}}' WHERE name = 'x'", Path::new("m.sql"), "sre/m");
        assert!(q.is_mutation());
        let sql = q.render(&BTreeMap::new(), &[("replicas", "2")]).unwrap();
        assert!(sql.contains("'{\"replicas\": 2}'"), "{sql}");
    }

    #[test]
    fn committed_queries_load_and_render() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("queries");
        let all = list_queries(&dir).unwrap();
        let ids: Vec<&str> = all.iter().map(|q| q.id.as_str()).collect();
        for want in [
            "sre/deployment_state",
            "sre/pods_for_deployment",
            "sre/warning_events",
            "sre/verify_replicas",
            "sre/assert_demo_target",
            "sre/scale_deployment",
        ] {
            assert!(ids.contains(&want), "missing {want} in {ids:?}");
        }
        let mut c = ctx();
        c.insert("sre_target_deployment".into(), "agentic-demo-checkout".into());
        c.insert("sre_app_label".into(), "checkout".into());
        c.insert("demo_tag_key".into(), "purpose".into());
        for q in &all {
            assert_eq!(q.providers, vec!["k8s"], "{}", q.id);
            let sql = q.render(&c, &[("replicas", "2")]).unwrap();
            assert!(sql.contains("cluster_addr = 'localhost:8001'"), "{}: {sql}", q.id);
            assert!(sql.contains("protocol = 'http'"), "{}: {sql}", q.id);
            assert_eq!(q.is_mutation(), q.id == "sre/scale_deployment", "{}", q.id);
        }
    }
}
