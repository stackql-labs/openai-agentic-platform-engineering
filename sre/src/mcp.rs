//! The embedded StackQL MCP server (crates.io `stackql-mcp`) and the model-facing tool filter.
//!
//! `start_read_only` is the only constructor this module exports: every server a model can reach
//! runs in `read_only` mode, and `model_tools` additionally strips the mutation and admin tools
//! from the list the model sees. The one `full_access` server in the program is constructed in
//! `gate.rs` (see the test there that proves it stays that way).
//!
//! The crate launches the server with its own audit disabled, so this module keeps a client-side
//! statement log instead: one JSON line per tool call at `STACKQL_MCP_AUDIT_LOG`.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, bail, Result};
use serde_json::{json, Map, Value};
use rmcp::model::{CallToolRequestParams, CallToolResult, ReadResourceRequestParams, ResourceContents, Tool};
use stackql_mcp::{Builder, Mode, RunningServer, StackqlMcp};

use crate::config::Settings;
use crate::cost::utc_now_iso;

/// The tools a model may hold: SELECT execution and validation, the query library, and the
/// discovery tools it uses to find resources and their IO contracts itself.
pub const MODEL_TOOL_ALLOWLIST: &[&str] = &[
    "run_select_query",
    "validate_select_query",
    "query_library_search",
    "query_library_get",
    "list_providers",
    "list_services",
    "list_resources",
    "list_methods",
    "describe_resource",
    "describe_method",
];

/// The MCP resource the server publishes with its own usage guidance.
pub const INSTRUCTIONS_RESOURCE: &str = "stackql://docs/instructions";

/// Never exposed to a model, whatever the server mode.
pub const MUTATION_TOOLS: &[&str] = &["run_mutation_query", "run_lifecycle_operation"];
pub const ADMIN_TOOLS: &[&str] = &["pull_provider", "reload_credentials"];

/// Pure filter: keep only allow-listed tools, and never a mutation or admin tool even if a
/// server advertised one under an allow-listed name by mistake.
pub fn filter_model_tools(tools: &[Tool]) -> Vec<Tool> {
    tools
        .iter()
        .filter(|t| {
            let n: &str = &t.name;
            MODEL_TOOL_ALLOWLIST.contains(&n) && !MUTATION_TOOLS.contains(&n) && !ADMIN_TOOLS.contains(&n)
        })
        .cloned()
        .collect()
}

#[derive(Debug, Clone)]
pub struct ToolOutput {
    pub text: String,
    pub structured: Option<Value>,
    pub is_error: bool,
}

impl ToolOutput {
    fn from_result(r: CallToolResult) -> Self {
        let text = r
            .content
            .iter()
            .filter_map(|c| c.as_text().map(|t| t.text.clone()))
            .collect::<Vec<_>>()
            .join("\n");
        ToolOutput {
            text,
            structured: r.structured_content,
            is_error: r.is_error.unwrap_or(false),
        }
    }

    /// `structuredContent.rows`, else the text parsed as JSON (`{rows: [...]}` or an array).
    pub fn rows(&self) -> Vec<Value> {
        fn extract(v: &Value) -> Option<Vec<Value>> {
            match v {
                Value::Array(a) => Some(a.clone()),
                Value::Object(o) => o.get("rows").and_then(|r| r.as_array()).cloned(),
                _ => None,
            }
        }
        if let Some(rows) = self.structured.as_ref().and_then(extract) {
            return rows;
        }
        let t = self.text.trim();
        if t.starts_with('{') || t.starts_with('[') {
            if let Ok(v) = serde_json::from_str::<Value>(t) {
                if let Some(rows) = extract(&v) {
                    return rows;
                }
            }
        }
        Vec::new()
    }
}

pub struct Server {
    inner: RunningServer,
    mode: Mode,
    audit_log: PathBuf,
}

/// Shared builder configuration (approot from settings). Mode is set by the caller.
pub(crate) fn builder(settings: &Settings, mode: Mode) -> Builder {
    let mut b = StackqlMcp::builder().mode(mode);
    if let Some(root) = &settings.approot {
        b = b.approot(root.clone());
    }
    b
}

/// The read-only server every model-facing step uses.
pub async fn start_read_only(settings: &Settings) -> Result<Server> {
    let inner = builder(settings, Mode::ReadOnly)
        .start()
        .await
        .map_err(|e| anyhow!("starting the read-only StackQL MCP server: {e}"))?;
    Ok(Server::wrap(inner, Mode::ReadOnly, settings))
}

impl Server {
    pub(crate) fn wrap(inner: RunningServer, mode: Mode, settings: &Settings) -> Self {
        Server {
            inner,
            mode,
            audit_log: settings.audit_log.clone(),
        }
    }

    pub fn mode(&self) -> Mode {
        self.mode
    }

    pub fn binary_path(&self) -> &Path {
        self.inner.binary_path()
    }

    pub async fn tools(&self) -> Result<Vec<Tool>> {
        self.inner
            .list_all_tools()
            .await
            .map_err(|e| anyhow!("list_tools: {e}"))
    }

    /// The filtered tool list a model is allowed to hold.
    pub async fn model_tools(&self) -> Result<Vec<Tool>> {
        Ok(filter_model_tools(&self.tools().await?))
    }

    fn audit(&self, tool: &str, args: &Map<String, Value>, out: Option<&ToolOutput>, err: Option<&str>) {
        let line = json!({
            "ts": utc_now_iso(),
            "mode": self.mode.as_str(),
            "tool": tool,
            "sql": args.get("sql").and_then(|v| v.as_str()),
            "args": args.iter().filter(|(k, _)| *k != "sql").map(|(k, v)| (k.clone(), v.clone())).collect::<Map<_, _>>(),
            "ok": err.is_none() && !out.map(|o| o.is_error).unwrap_or(false),
            "error": err,
        });
        if let Some(dir) = self.audit_log.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&self.audit_log) {
            let _ = writeln!(f, "{line}");
        }
    }

    /// One tool call, logged. This is the only path to the server for the whole program.
    pub async fn call(&self, tool: &str, args: Map<String, Value>) -> Result<ToolOutput> {
        let params = CallToolRequestParams::new(tool.to_string()).with_arguments(args.clone());
        match self.inner.call_tool(params).await {
            Ok(r) => {
                let out = ToolOutput::from_result(r);
                self.audit(tool, &args, Some(&out), None);
                Ok(out)
            }
            Err(e) => {
                let msg = e.to_string();
                self.audit(tool, &args, None, Some(&msg));
                Err(anyhow!("{tool}: {msg}"))
            }
        }
    }

    /// `run_select_query` with `format: json`, rows parsed.
    pub async fn select(&self, sql: &str) -> Result<Vec<Value>> {
        let mut args = Map::new();
        args.insert("sql".into(), Value::String(sql.to_string()));
        args.insert("format".into(), Value::String("json".into()));
        let out = self.call("run_select_query", args).await?;
        if out.is_error {
            bail!("run_select_query failed: {}", out.text.chars().take(600).collect::<String>());
        }
        Ok(out.rows())
    }

    /// `validate_select_query` -> (valid, errors).
    pub async fn validate(&self, sql: &str) -> Result<(bool, Vec<String>)> {
        let mut args = Map::new();
        args.insert("sql".into(), Value::String(sql.to_string()));
        args.insert("format".into(), Value::String("json".into()));
        let out = self.call("validate_select_query", args).await?;
        let body = out
            .structured
            .clone()
            .or_else(|| serde_json::from_str(out.text.trim()).ok())
            .unwrap_or(Value::Null);
        let valid = body.get("valid").and_then(|v| v.as_bool());
        let errors: Vec<String> = body
            .get("errors")
            .and_then(|e| e.as_array())
            .map(|a| a.iter().map(|x| x.as_str().map(str::to_string).unwrap_or_else(|| x.to_string())).collect())
            .unwrap_or_default();
        match valid {
            Some(v) => Ok((v && !out.is_error, errors)),
            None => {
                let lowered = out.text.to_ascii_lowercase();
                let ok = !out.is_error && !lowered.contains("\"valid\": false") && !lowered.contains("error");
                Ok((ok, if ok { vec![] } else { vec![out.text.chars().take(400).collect()] }))
            }
        }
    }

    /// Read an MCP resource (`resources/read`) and return its text contents joined.
    pub async fn read_resource(&self, uri: &str) -> Result<String> {
        let r = self
            .inner
            .read_resource(ReadResourceRequestParams::new(uri.to_string()))
            .await
            .map_err(|e| anyhow!("read_resource {uri}: {e}"))?;
        let text = r
            .contents
            .iter()
            .filter_map(|c| match c {
                ResourceContents::TextResourceContents { text, .. } => Some(text.as_str()),
                _ => None,
            })
            .collect::<Vec<_>>()
            .join("\n");
        if text.trim().is_empty() {
            bail!("read_resource {uri}: no text contents");
        }
        Ok(text)
    }

    /// The server's own instructions (`stackql://docs/instructions`), appended to every prompt.
    /// A failed read is reported on the console and the run continues without it.
    pub async fn instructions(&self) -> Option<String> {
        match self.read_resource(INSTRUCTIONS_RESOURCE).await {
            Ok(t) => {
                println!("read {} ({} chars) - appended to the discovery briefing", INSTRUCTIONS_RESOURCE, t.chars().count());
                Some(t)
            }
            Err(e) => {
                println!("could not read {INSTRUCTIONS_RESOURCE} ({e}) - continuing without the server instructions");
                None
            }
        }
    }

    pub async fn server_info(&self) -> Result<String> {
        let out = self.call("server_info", Map::new()).await?;
        Ok(out
            .structured
            .map(|s| serde_json::to_string_pretty(&s).unwrap_or_default())
            .filter(|s| !s.is_empty())
            .unwrap_or(out.text))
    }

    pub async fn pull_provider(&self, provider: &str) -> Result<String> {
        let mut args = Map::new();
        args.insert("provider".into(), Value::String(provider.into()));
        let out = self.call("pull_provider", args).await?;
        if out.is_error {
            bail!("pull_provider {provider} failed: {}", out.text);
        }
        Ok(out.text)
    }

    pub async fn shutdown(self) -> Result<()> {
        self.inner
            .shutdown()
            .await
            .map_err(|e| anyhow!("shutdown: {e}"))
    }
}

/// Convert an MCP tool description into a Responses API function tool definition.
pub fn to_function_tool(t: &Tool) -> async_openai::types::responses::Tool {
    use async_openai::types::responses::{FunctionTool, Tool as OaTool};
    let mut params: Map<String, Value> = (*t.input_schema).clone();
    if !params.contains_key("type") {
        params.insert("type".into(), Value::String("object".into()));
    }
    OaTool::Function(FunctionTool {
        name: t.name.to_string(),
        parameters: Some(Value::Object(params)),
        strict: Some(false),
        description: t.description.as_ref().map(|d| d.to_string()),
        defer_loading: None,
        r#async: None,
        output_schema: None,
        allowed_callers: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn tool(name: &str) -> Tool {
        Tool::new(name.to_string(), "x", Arc::new(Map::new()))
    }

    #[test]
    fn model_tool_list_never_contains_mutation_or_admin_tools() {
        let advertised: Vec<Tool> = [
            "run_select_query",
            "validate_select_query",
            "run_mutation_query",
            "run_lifecycle_operation",
            "pull_provider",
            "reload_credentials",
            "list_providers",
            "list_services",
            "list_resources",
            "list_methods",
            "describe_resource",
            "describe_method",
            "query_library_search",
            "query_library_get",
            "server_info",
        ]
        .iter()
        .map(|n| tool(n))
        .collect();
        let kept: Vec<String> = filter_model_tools(&advertised).iter().map(|t| t.name.to_string()).collect();
        for forbidden in MUTATION_TOOLS.iter().chain(ADMIN_TOOLS.iter()) {
            assert!(!kept.iter().any(|k| k == forbidden), "{forbidden} leaked into {kept:?}");
        }
        assert!(kept.iter().any(|k| k == "run_select_query"));
        assert!(kept.iter().any(|k| k == "validate_select_query"));
        assert!(!kept.iter().any(|k| k == "server_info"));
        assert!(kept.iter().any(|k| k == "query_library_search"));
        assert!(kept.iter().any(|k| k == "query_library_get"));
        assert!(kept.iter().any(|k| k == "describe_method"));
    }

    #[test]
    fn allowlist_and_denylists_are_disjoint() {
        for n in MUTATION_TOOLS.iter().chain(ADMIN_TOOLS.iter()) {
            assert!(!MODEL_TOOL_ALLOWLIST.contains(n), "{n} is on both lists");
        }
    }

    #[test]
    fn rows_come_from_structured_content_or_text() {
        let s = ToolOutput {
            text: "ignored".into(),
            structured: Some(json!({"rows": [{"a": 1}]})),
            is_error: false,
        };
        assert_eq!(s.rows().len(), 1);
        let t = ToolOutput {
            text: "{\"rows\": [{\"a\": 1}, {\"a\": 2}]}".into(),
            structured: None,
            is_error: false,
        };
        assert_eq!(t.rows().len(), 2);
        let none = ToolOutput { text: "| a |\n| 1 |".into(), structured: None, is_error: false };
        assert!(none.rows().is_empty());
    }

    #[test]
    fn function_tool_conversion_keeps_name_and_schema() {
        let mut schema = Map::new();
        schema.insert("type".into(), Value::String("object".into()));
        schema.insert("properties".into(), json!({"sql": {"type": "string"}}));
        let mut t = tool("run_select_query");
        t.input_schema = Arc::new(schema);
        match to_function_tool(&t) {
            async_openai::types::responses::Tool::Function(f) => {
                assert_eq!(f.name, "run_select_query");
                assert_eq!(f.strict, Some(false));
                assert!(f.parameters.unwrap()["properties"]["sql"].is_object());
            }
            other => panic!("unexpected tool {other:?}"),
        }
    }
}
