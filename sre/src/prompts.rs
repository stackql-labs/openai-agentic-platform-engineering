//! Prompt loader. Each model-facing step reads its instructions from a markdown file under
//! `sre/prompts/` at run time (edit the file, no rebuild), substitutes `{{ placeholders }}` from
//! the settings context and appends the shared discovery briefing plus, when the server served it,
//! the text of the MCP resource `stackql://docs/instructions`.
//!
//! The prompts state intent, scope, output contract and guardrails in prose. They never carry
//! resource names, columns or SQL: the agent discovers those through the StackQL tools.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};

use crate::queries::substitute;

/// The role prompts a run needs, by file stem.
pub const ROLE_PROMPTS: &[&str] = &["diagnose", "propose", "closeout"];
pub const DISCOVERY_PROMPT: &str = "discovery";

/// Read `<dir>/<name>.md` and substitute its placeholders. A missing file or a placeholder without
/// a value is an error naming the file or the variable.
pub fn load_prompt(dir: &Path, name: &str, ctx: &BTreeMap<String, String>) -> Result<String> {
    let path = dir.join(format!("{name}.md"));
    let text = fs::read_to_string(&path).with_context(|| format!("reading prompt {}", path.display()))?;
    let out = substitute(&text, ctx, &[], &format!("prompt {}", path.display()))?;
    Ok(out.trim().to_string())
}

/// A role prompt with the discovery briefing and the server's own instructions appended.
pub fn compose(role: &str, discovery: &str, server_instructions: Option<&str>) -> String {
    let mut out = String::with_capacity(role.len() + discovery.len() + 512);
    out.push_str(role.trim());
    out.push_str("\n\n");
    out.push_str(discovery.trim());
    if let Some(s) = server_instructions.map(str::trim).filter(|s| !s.is_empty()) {
        out.push_str("\n\n# Server instructions (stackql://docs/instructions, read at startup)\n\n");
        out.push_str(s);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("prompts")
    }

    fn ctx() -> BTreeMap<String, String> {
        let mut m = BTreeMap::new();
        for (k, v) in [
            ("kube_cluster_addr", "localhost:8001"),
            ("kube_protocol", "http"),
            ("k8s_namespace", "agentic-demo"),
            ("sre_target_deployment", "agentic-demo-checkout"),
            ("sre_app_label", "checkout"),
            ("demo_prefix", "agentic-demo"),
            ("demo_tag_key", "purpose"),
            ("demo_tag_value", "agentic-demo"),
        ] {
            m.insert(k.to_string(), v.to_string());
        }
        m
    }

    #[test]
    fn every_role_prompt_loads_and_renders() {
        for name in ROLE_PROMPTS.iter().chain([DISCOVERY_PROMPT].iter()) {
            let text = load_prompt(&dir(), name, &ctx()).unwrap();
            assert!(!text.contains("{{"), "{name}: unrendered placeholder in {text}");
            assert!(text.lines().count() <= 60, "{name}: keep role prompts under ~60 lines");
            assert!(text.is_ascii(), "{name}: plain ASCII only");
        }
        let d = load_prompt(&dir(), "diagnose", &ctx()).unwrap();
        assert!(d.contains("namespace `agentic-demo`"));
        assert!(d.contains("agentic-demo-checkout"));
        assert!(d.contains("purpose=agentic-demo"));
        let p = load_prompt(&dir(), "propose", &ctx()).unwrap();
        assert!(p.contains("localhost:8001"));
        assert!(p.contains("describe_method"));
    }

    #[test]
    fn prompts_carry_no_sql() {
        for name in ROLE_PROMPTS.iter().chain([DISCOVERY_PROMPT].iter()) {
            let text = load_prompt(&dir(), name, &ctx()).unwrap();
            // a statement shape (FROM <provider.service.resource>, SET ... WHERE) or a resource name
            // would be the old query pack creeping back in; prose that mentions the keywords is fine
            let upper = text.to_ascii_uppercase();
            let statement_shape = upper.split_whitespace().collect::<Vec<_>>().windows(2).any(|w| {
                (w[0] == "FROM" || w[0] == "UPDATE" || w[0] == "REPLACE") && w[1].matches('.').count() == 2
            });
            assert!(!statement_shape, "{name}: SQL statement in prompt");
            assert!(!text.contains("k8s.apps.") && !text.contains("k8s.core."), "{name}: resource names belong to discovery, not the prompt");
            assert!(!text.contains("json_extract(metadata") && !text.contains("readyReplicas"), "{name}: column paths belong to discovery, not the prompt");
        }
    }

    #[test]
    fn missing_placeholder_names_the_variable() {
        let mut c = ctx();
        c.remove("k8s_namespace");
        std::env::remove_var("K8S_NAMESPACE");
        let err = load_prompt(&dir(), "diagnose", &c).unwrap_err().to_string();
        assert!(err.contains("K8S_NAMESPACE"), "{err}");
        assert!(err.contains("diagnose.md"), "{err}");
    }

    #[test]
    fn missing_file_names_the_path() {
        let err = load_prompt(&dir(), "nope", &ctx()).unwrap_err().to_string();
        assert!(err.contains("nope.md"), "{err}");
    }

    #[test]
    fn compose_appends_discovery_and_server_instructions() {
        let s = compose("role", "discovery", Some("server says"));
        assert!(s.starts_with("role\n\ndiscovery"));
        assert!(s.contains("stackql://docs/instructions"));
        assert!(s.ends_with("server says"));
        let without = compose("role", "discovery", None);
        assert!(!without.contains("stackql://docs/instructions"));
        assert_eq!(compose("role", "discovery", Some("  ")), without);
    }
}
