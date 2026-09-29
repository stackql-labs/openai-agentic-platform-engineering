//! The synthetic alert - the event trigger for the loop. `sre alert` writes `runs/alert.json` in
//! the shape a webhook receiver would hand to the agent (Alertmanager, PagerDuty, a Prometheus
//! rule). Nobody types a prompt: `sre run` picks the file up.

use std::fs;
use std::path::PathBuf;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::config::Settings;
use crate::cost::{short_id, utc_now_iso};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Alert {
    pub alert_id: String,
    pub fired_at: String,
    pub source: String,
    pub service: String,
    pub severity: String,
    pub symptom: String,
    pub signal: String,
    pub scope: AlertScope,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlertScope {
    pub cluster_addr: String,
    pub namespace: String,
    pub deployment: String,
}

pub fn alert_path(s: &Settings) -> PathBuf {
    s.runs_dir().join("alert.json")
}

pub fn fire(s: &Settings, symptom: Option<&str>) -> Result<Alert> {
    let a = Alert {
        alert_id: format!("alrt-{}", short_id(8)),
        fired_at: utc_now_iso(),
        source: "synthetic-monitor".into(),
        service: s.app_label.clone(),
        severity: "page".into(),
        symptom: symptom
            .map(str::to_string)
            .unwrap_or_else(|| "checkout p95 latency 2.4s against a 500ms SLO for 10 minutes; error rate rising".into()),
        signal: "capacity: request queue depth growing on the only ready replica".into(),
        scope: AlertScope {
            cluster_addr: s.kube_cluster_addr.clone(),
            namespace: s.k8s_namespace.clone(),
            deployment: s.target_deployment.clone(),
        },
    };
    let path = alert_path(s);
    fs::create_dir_all(path.parent().unwrap())?;
    fs::write(&path, serde_json::to_string_pretty(&a)?)
        .with_context(|| format!("writing {}", path.display()))?;
    Ok(a)
}

pub fn load(s: &Settings) -> Result<Option<Alert>> {
    let path = alert_path(s);
    if !path.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&path)?;
    Ok(Some(serde_json::from_str(&text).with_context(|| format!("parsing {}", path.display()))?))
}
