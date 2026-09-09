#[path = "http_policy/mod.rs"]
mod http_policy;

use std::{env, process::ExitCode, time::Duration};
use trnm_online_protocol::{
    OnlineEnforcementAppealQueueRequest, OnlineEnforcementAppealQueueView,
    OnlineEnforcementAppealResolveRequest, OnlineEnforcementAppealView, OnlineFleetAdminRequest,
    OnlineFleetAdminView, OnlineModerationActionRequest, OnlineModerationActionView,
    OnlineModerationCaseClaimRequest, OnlineModerationCaseClaimView, OnlineModerationQueueRequest,
    OnlineModerationQueueView, OnlineModerationShiftAccessRequest,
    OnlineModerationShiftStartRequest, OnlineModerationShiftView, OnlineSeasonAdminRequest,
    OnlineSeasonAdminView, OnlineSeasonAutomationRequest, OnlineSeasonAutomationView,
};

fn required(key: &str) -> Result<String, String> {
    env::var(key)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("{key} is required"))
}

async fn run() -> Result<(), String> {
    let base_url =
        env::var("TRNM_GAME_SERVER_URL").unwrap_or_else(|_| "http://127.0.0.1:7005".to_string());
    let token = required("TRNM_MODERATOR_TOKEN")?;
    let client = http_policy::client_builder(Duration::from_secs(2), Duration::from_secs(10))
        .build()
        .map_err(|error| error.to_string())?;
    let args = env::args().skip(1).collect::<Vec<_>>();
    match args.first().map(String::as_str) {
        Some("list") => {
            let status = args.get(1).map(String::as_str).unwrap_or("open");
            let response = client
                .post(format!("{base_url}/v1/operations/moderation/queue"))
                .header("x-trnm-moderator", &token)
                .json(&OnlineModerationQueueRequest {
                    status: status.to_string(),
                    limit: 100,
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("moderation queue rejected ({status_code}): {body}"));
            }
            let view: OnlineModerationQueueView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("action") if args.len() >= 6 => {
            let scope = match args[3].as_str() {
                "none" => None,
                value => Some(value.to_string()),
            };
            let hours = args[4]
                .parse::<u32>()
                .map_err(|_| "suspension hours must be an integer".to_string())?;
            let response = client
                .post(format!("{base_url}/v1/operations/moderation/action"))
                .header("x-trnm-moderator", &token)
                .json(&OnlineModerationActionRequest {
                    report_id: args[1].clone(),
                    decision: args[2].clone(),
                    resolution: args[5..].join(" "),
                    enforcement_scope: scope,
                    suspension_hours: if hours == 0 { None } else { Some(hours) },
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!(
                    "moderation action rejected ({status_code}): {body}"
                ));
            }
            let view: OnlineModerationActionView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("appeals") => {
            let status = args.get(1).map(String::as_str).unwrap_or("pending");
            let response = client
                .post(format!("{base_url}/v1/operations/moderation/appeals"))
                .header("x-trnm-moderator", &token)
                .json(&OnlineEnforcementAppealQueueRequest {
                    status: status.to_string(),
                    limit: 100,
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("appeal queue rejected ({status_code}): {body}"));
            }
            let view: OnlineEnforcementAppealQueueView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("appeal") if args.len() >= 4 => {
            let response = client
                .post(format!(
                    "{base_url}/v1/operations/moderation/appeals/resolve"
                ))
                .header("x-trnm-moderator", &token)
                .json(&OnlineEnforcementAppealResolveRequest {
                    appeal_id: args[1].clone(),
                    decision: args[2].clone(),
                    resolution: args[3..].join(" "),
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("appeal action rejected ({status_code}): {body}"));
            }
            let view: OnlineEnforcementAppealView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("season") if args.len() >= 3 => {
            let action = args[1].clone();
            if action == "auto" && args.len() == 4 {
                let automatic_activation = match args[3].as_str() {
                    "on" => true,
                    "off" => false,
                    _ => return Err("season auto state must be on or off".to_string()),
                };
                let response = client
                    .post(format!("{base_url}/v1/production/seasons/automation"))
                    .header("x-trnm-moderator", &token)
                    .json(&OnlineSeasonAutomationRequest {
                        season_id: args[2].clone(),
                        automatic_activation,
                    })
                    .send()
                    .await
                    .map_err(|error| error.to_string())?;
                let status_code = response.status();
                let body = response.text().await.map_err(|error| error.to_string())?;
                if !status_code.is_success() {
                    return Err(format!(
                        "season automation rejected ({status_code}): {body}"
                    ));
                }
                let view: OnlineSeasonAutomationView =
                    serde_json::from_str(&body).map_err(|error| error.to_string())?;
                println!(
                    "{}",
                    serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
                );
                return Ok(());
            }
            let request = if action == "create" && args.len() == 7 {
                OnlineSeasonAdminRequest {
                    action,
                    season_id: args[2].clone(),
                    display_name: Some(args[3].clone()),
                    rules_version: Some(args[4].clone()),
                    starts_at_epoch: Some(
                        args[5]
                            .parse()
                            .map_err(|_| "season start must be epoch seconds".to_string())?,
                    ),
                    ends_at_epoch: Some(
                        args[6]
                            .parse()
                            .map_err(|_| "season end must be epoch seconds".to_string())?,
                    ),
                }
            } else if matches!(action.as_str(), "activate" | "close") && args.len() == 3 {
                OnlineSeasonAdminRequest {
                    action,
                    season_id: args[2].clone(),
                    display_name: None,
                    rules_version: None,
                    starts_at_epoch: None,
                    ends_at_epoch: None,
                }
            } else {
                return Err("season usage: season create ID NAME RULES START_EPOCH END_EPOCH | season activate ID | season close ID".to_string());
            };
            let response = client
                .post(format!("{base_url}/v1/operations/seasons/admin"))
                .header("x-trnm-moderator", &token)
                .json(&request)
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("season action rejected ({status_code}): {body}"));
            }
            let view: OnlineSeasonAdminView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("fleet") if args.len() >= 4 => {
            let response = client
                .post(format!("{base_url}/v1/operations/fleet/admin"))
                .header("x-trnm-moderator", &token)
                .json(&OnlineFleetAdminRequest {
                    action: args[1].clone(),
                    instance_id: args[2].clone(),
                    reason: args[3..].join(" "),
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("fleet action rejected ({status_code}): {body}"));
            }
            let view: OnlineFleetAdminView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("shift") if args.get(1).map(String::as_str) == Some("start") && args.len() >= 5 => {
            let duration_minutes = args[3]
                .parse::<u32>()
                .map_err(|_| "shift duration must be integer minutes".to_string())?;
            let response = client
                .post(format!("{base_url}/v1/production/moderation/shifts/start"))
                .header("x-trnm-moderator", &token)
                .json(&OnlineModerationShiftStartRequest {
                    moderator_id: args[2].clone(),
                    duration_minutes,
                    note: args[4..].join(" "),
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("shift start rejected ({status_code}): {body}"));
            }
            let view: OnlineModerationShiftView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("shift")
            if matches!(args.get(1).map(String::as_str), Some("heartbeat" | "close"))
                && args.len() >= 5 =>
        {
            let action = args[1].as_str();
            let response = client
                .post(format!(
                    "{base_url}/v1/production/moderation/shifts/{action}"
                ))
                .header("x-trnm-moderator", &token)
                .json(&OnlineModerationShiftAccessRequest {
                    shift_id: args[2].clone(),
                    moderator_id: args[3].clone(),
                    note: args[4..].join(" "),
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("shift {action} rejected ({status_code}): {body}"));
            }
            let view: OnlineModerationShiftView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        Some("claim") if args.len() == 5 => {
            let response = client
                .post(format!("{base_url}/v1/production/moderation/claims"))
                .header("x-trnm-moderator", &token)
                .json(&OnlineModerationCaseClaimRequest {
                    shift_id: args[1].clone(),
                    moderator_id: args[2].clone(),
                    case_kind: args[3].clone(),
                    case_id: args[4].clone(),
                })
                .send()
                .await
                .map_err(|error| error.to_string())?;
            let status_code = response.status();
            let body = response.text().await.map_err(|error| error.to_string())?;
            if !status_code.is_success() {
                return Err(format!("case claim rejected ({status_code}): {body}"));
            }
            let view: OnlineModerationCaseClaimView =
                serde_json::from_str(&body).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&view).map_err(|error| error.to_string())?
            );
        }
        _ => {
            return Err(
                "usage: trnm-moderation-console list [status] | action REPORT_ID DECISION SCOPE HOURS RESOLUTION... | appeals [status] | appeal APPEAL_ID DECISION RESOLUTION... | season create/activate/close/auto ... | fleet ACTION INSTANCE REASON... | shift start MODERATOR MINUTES NOTE... | shift heartbeat/close SHIFT MODERATOR NOTE... | claim SHIFT MODERATOR KIND CASE"
                    .to_string(),
            );
        }
    }
    Ok(())
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> ExitCode {
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}
