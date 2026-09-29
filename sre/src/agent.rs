//! The model steps, on the OpenAI Responses API (`async-openai`): a function-calling loop whose
//! tools are bridged from the read-only embedded server (filtered to SELECT and discovery), and
//! structured outputs via a `json_schema` text format. The model gets an intent prompt and
//! discovers resources, columns and mutation contracts through the StackQL tools; it proposes a
//! statement, it never executes one.

use anyhow::{anyhow, bail, Context, Result};
use async_openai::config::OpenAIConfig;
use async_openai::types::responses::{
    CreateResponseArgs, FunctionCallOutput, FunctionCallOutputItemParam, InputItem, InputParam, Item,
    OutputItem, Reasoning, ReasoningEffort, ResponseFormatJsonSchema, ResponseTextParam, Status,
    TextResponseFormatConfiguration, Tool,
};
use async_openai::Client;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::cost::StepUsage;
use crate::mcp::{to_function_tool, Server, MODEL_TOOL_ALLOWLIST};

/// Bound on what one tool result feeds back to the model.
const MAX_TOOL_OUTPUT_CHARS: usize = 24_000;

pub fn effort(s: &str) -> ReasoningEffort {
    match s.trim().to_ascii_lowercase().as_str() {
        "none" => ReasoningEffort::None,
        "minimal" => ReasoningEffort::Minimal,
        "low" => ReasoningEffort::Low,
        "high" => ReasoningEffort::High,
        "xhigh" => ReasoningEffort::Xhigh,
        "max" => ReasoningEffort::Max,
        _ => ReasoningEffort::Medium,
    }
}

/// A structured-output contract: schema name plus a strict JSON schema.
pub struct OutputSchema {
    pub name: &'static str,
    pub schema: Value,
}

pub struct Step<'a> {
    pub label: &'a str,
    pub model: &'a str,
    pub effort: &'a str,
    pub instructions: &'a str,
    pub input: String,
    /// When set, the model holds the filtered tools of this server and the loop executes calls.
    pub server: Option<&'a Server>,
    pub output: Option<OutputSchema>,
    pub max_turns: usize,
}

pub struct StepResult {
    pub text: String,
    pub usage: StepUsage,
}

/// Run one step to completion: create -> execute function calls -> continue with
/// `previous_response_id` until the model answers without a call.
pub async fn run_step(client: &Client<OpenAIConfig>, step: Step<'_>) -> Result<StepResult> {
    let mut usage = StepUsage::new(step.label, step.model);
    let tools: Vec<Tool> = match step.server {
        Some(s) => s.model_tools().await?.iter().map(to_function_tool).collect(),
        None => Vec::new(),
    };
    for t in &tools {
        if let Tool::Function(f) = t {
            if !MODEL_TOOL_ALLOWLIST.contains(&f.name.as_str()) {
                bail!("refusing to hand the model tool {}", f.name);
            }
        }
    }
    let text_format = step.output.as_ref().map(|o| ResponseTextParam {
        format: TextResponseFormatConfiguration::JsonSchema(ResponseFormatJsonSchema {
            description: None,
            name: o.name.to_string(),
            schema: o.schema.clone(),
            strict: Some(true),
        }),
        verbosity: None,
    });

    let mut input = InputParam::Text(step.input.clone());
    let mut previous: Option<String> = None;
    for _turn in 0..step.max_turns.max(1) {
        let mut args = CreateResponseArgs::default();
        args.model(step.model)
            .instructions(step.instructions)
            .input(input.clone())
            .reasoning(Reasoning {
                effort: Some(effort(step.effort)),
                summary: None,
                mode: None,
                context: None,
            })
            .store(true);
        if !tools.is_empty() {
            args.tools(tools.clone());
            args.parallel_tool_calls(false);
        }
        if let Some(t) = &text_format {
            args.text(t.clone());
        }
        if let Some(p) = &previous {
            args.previous_response_id(p.clone());
        }
        let request = args.build().context("building the Responses request")?;
        let resp = client
            .responses()
            .create(request)
            .await
            .with_context(|| format!("{}: responses.create", step.label))?;

        usage.requests += 1;
        usage.response_ids.push(resp.id.clone());
        if let Some(u) = &resp.usage {
            usage.input_tokens += u.input_tokens;
            usage.cached_tokens += u.input_tokens_details.cached_tokens;
            usage.output_tokens += u.output_tokens;
            usage.reasoning_tokens += u.output_tokens_details.reasoning_tokens;
        }
        if let Some(e) = &resp.error {
            bail!("{}: response {} failed: {}", step.label, resp.id, e.message);
        }
        if matches!(resp.status, Status::Failed | Status::Cancelled) {
            bail!("{}: response {} ended with status {:?}", step.label, resp.id, resp.status);
        }

        let calls: Vec<(String, String, String)> = resp
            .output
            .iter()
            .filter_map(|item| match item {
                OutputItem::FunctionCall(fc) => Some((fc.call_id.clone(), fc.name.clone(), fc.arguments.clone())),
                _ => None,
            })
            .collect();

        if calls.is_empty() {
            let text = resp
                .output_text()
                .ok_or_else(|| anyhow!("{}: response {} carried no text output (status {:?})", step.label, resp.id, resp.status))?;
            return Ok(StepResult { text, usage });
        }

        let server = step
            .server
            .ok_or_else(|| anyhow!("{}: the model issued a function call without tools", step.label))?;
        let mut outputs: Vec<InputItem> = Vec::with_capacity(calls.len());
        for (call_id, name, arguments) in calls {
            usage.note_tool_call(&name);
            let result_text = execute_call(server, &name, &arguments).await;
            println!("  tool {name}: {}", summarise_call(&arguments, &result_text));
            outputs.push(InputItem::Item(Item::FunctionCallOutput(FunctionCallOutputItemParam {
                call_id: Some(call_id),
                output: FunctionCallOutput::Text(result_text),
                id: None,
                status: None,
                name: None,
                namespace: None,
                caller: None,
            })));
        }
        previous = Some(resp.id.clone());
        input = InputParam::Items(outputs);
    }
    bail!("{}: exceeded {} turns without a final answer", step.label, step.max_turns)
}

/// Execute one model-issued call against the read-only server. A name outside the allow list is
/// answered with an error string and never reaches the server.
async fn execute_call(server: &Server, name: &str, arguments: &str) -> String {
    if !MODEL_TOOL_ALLOWLIST.contains(&name) {
        return json!({"error": format!("tool {name} is not available to this agent")}).to_string();
    }
    let args: Map<String, Value> = match serde_json::from_str::<Value>(arguments) {
        Ok(Value::Object(m)) => m,
        Ok(_) => Map::new(),
        Err(e) => return json!({"error": format!("arguments were not a JSON object: {e}")}).to_string(),
    };
    match server.call(name, args).await {
        Ok(out) => {
            let body = match &out.structured {
                Some(s) => s.to_string(),
                None => out.text.clone(),
            };
            let body = if out.is_error { json!({"error": body}).to_string() } else { body };
            if body.chars().count() > MAX_TOOL_OUTPUT_CHARS {
                let cut: String = body.chars().take(MAX_TOOL_OUTPUT_CHARS).collect();
                format!("{cut}\n... [truncated by the agent at {MAX_TOOL_OUTPUT_CHARS} chars]")
            } else {
                body
            }
        }
        Err(e) => json!({"error": e.to_string()}).to_string(),
    }
}

fn summarise_call(arguments: &str, result: &str) -> String {
    let sql = serde_json::from_str::<Value>(arguments)
        .ok()
        .and_then(|v| v.get("sql").and_then(|s| s.as_str()).map(str::to_string))
        .map(|s| s.split_whitespace().collect::<Vec<_>>().join(" "))
        .unwrap_or_else(|| arguments.chars().take(80).collect());
    let head: String = sql.chars().take(90).collect();
    let rows = serde_json::from_str::<Value>(result)
        .ok()
        .and_then(|v| v.get("rows").and_then(|r| r.as_array()).map(|a| a.len()));
    match rows {
        Some(n) => format!("{head} -> {n} rows"),
        None => format!("{head} -> {} chars", result.len()),
    }
}

// --- structured outputs ------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
pub struct CapacityObservation {
    pub spec_replicas: i64,
    pub ready_replicas: i64,
    pub available_replicas: i64,
    pub pods_total: i64,
    pub pods_running: i64,
    pub restarts_total: i64,
}

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
pub struct Diagnosis {
    pub summary: String,
    pub capacity: CapacityObservation,
    pub warning_events: Vec<String>,
    pub evidence: Vec<String>,
    pub hypothesis: String,
}

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
pub struct Proposal {
    pub action: String,
    pub target: String,
    /// `provider.service.resource` the statement addresses, as the model discovered it.
    pub resource: String,
    pub new_replicas: i64,
    /// The exact single mutation statement (empty for no_action). Checked by the gate allowlist.
    pub statement: String,
    /// The SELECT that verifies the outcome (empty for no_action). Validated before use.
    pub verification_select: String,
    pub rollback_statement: String,
    pub blast_radius: String,
    pub rationale: String,
}

#[derive(Debug, Clone, Deserialize, serde::Serialize)]
pub struct Closeout {
    pub outcome: String,
    pub summary: String,
}

fn obj(props: Value, required: &[&str]) -> Value {
    json!({
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": false
    })
}

pub fn diagnosis_schema() -> OutputSchema {
    OutputSchema {
        name: "diagnosis",
        schema: obj(
            json!({
                "summary": {"type": "string", "description": "Three to five sentences, matter of fact, with the numbers"},
                "capacity": obj(
                    json!({
                        "spec_replicas": {"type": "integer"},
                        "ready_replicas": {"type": "integer"},
                        "available_replicas": {"type": "integer"},
                        "pods_total": {"type": "integer"},
                        "pods_running": {"type": "integer"},
                        "restarts_total": {"type": "integer"}
                    }),
                    &["spec_replicas", "ready_replicas", "available_replicas", "pods_total", "pods_running", "restarts_total"],
                ),
                "warning_events": {"type": "array", "items": {"type": "string"}, "description": "One line per Warning event seen, or empty"},
                "evidence": {"type": "array", "items": {"type": "string"}, "description": "Query ids run and the key values each returned"},
                "hypothesis": {"type": "string", "description": "Most likely cause given the evidence"}
            }),
            &["summary", "capacity", "warning_events", "evidence", "hypothesis"],
        ),
    }
}

pub fn proposal_schema() -> OutputSchema {
    OutputSchema {
        name: "proposal",
        schema: obj(
            json!({
                "action": {"type": "string", "enum": ["scale_out_by_one", "no_action"]},
                "target": {"type": "string", "description": "namespace/deployment the mutation touches"},
                "resource": {"type": "string", "description": "provider.service.resource the statement addresses, as discovered with list_methods / describe_method (empty for no_action)"},
                "new_replicas": {"type": "integer", "description": "The replica count after the action (the current desired count when no_action)"},
                "statement": {"type": "string", "description": "The exact single StackQL mutation statement to be approved and executed by code (empty for no_action)"},
                "verification_select": {"type": "string", "description": "A flat SELECT returning spec_replicas and ready_replicas for the target, validated with validate_select_query (empty for no_action)"},
                "rollback_statement": {"type": "string", "description": "The same mutation with the previous replica count (empty for no_action)"},
                "blast_radius": {"type": "string", "description": "What else changes, and what does not"},
                "rationale": {"type": "string"}
            }),
            &["action", "target", "resource", "new_replicas", "statement", "verification_select", "rollback_statement", "blast_radius", "rationale"],
        ),
    }
}

pub fn closeout_schema() -> OutputSchema {
    OutputSchema {
        name: "closeout",
        schema: obj(
            json!({
                "outcome": {"type": "string", "enum": ["recovered", "not_recovered", "declined", "rejected", "no_action"]},
                "summary": {"type": "string", "description": "One paragraph incident note for the on-call handover"}
            }),
            &["outcome", "summary"],
        ),
    }
}

pub fn parse_output<T: for<'de> Deserialize<'de>>(label: &str, text: &str) -> Result<T> {
    serde_json::from_str(text).with_context(|| format!("{label}: structured output did not match the schema: {text}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schemas_are_strict_objects() {
        for s in [diagnosis_schema(), proposal_schema(), closeout_schema()] {
            assert_eq!(s.schema["type"], "object");
            assert_eq!(s.schema["additionalProperties"], false);
            let props = s.schema["properties"].as_object().unwrap();
            let required = s.schema["required"].as_array().unwrap();
            assert_eq!(props.len(), required.len(), "{}: every property must be required for strict mode", s.name);
        }
        assert_eq!(proposal_schema().schema["properties"]["action"]["enum"], json!(["scale_out_by_one", "no_action"]));
    }

    #[test]
    fn structured_outputs_parse() {
        let p: Proposal = parse_output(
            "propose",
            r#"{"action":"scale_out_by_one","target":"agentic-demo/agentic-demo-checkout","resource":"k8s.apps.deployments_scale","new_replicas":2,"statement":"UPDATE ...","verification_select":"SELECT ...","rollback_statement":"UPDATE ...","blast_radius":"b","rationale":"r"}"#,
        )
        .unwrap();
        assert_eq!(p.new_replicas, 2);
        assert!(parse_output::<Proposal>("propose", "{\"action\":\"x\"}").is_err());
    }

    #[test]
    fn effort_maps_env_strings() {
        assert_eq!(effort("low"), ReasoningEffort::Low);
        assert_eq!(effort("HIGH"), ReasoningEffort::High);
        assert_eq!(effort("unknown"), ReasoningEffort::Medium);
    }
}
