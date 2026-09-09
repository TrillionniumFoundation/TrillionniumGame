mod runtime_policy;

use anyhow::{bail, Result};
use runtime_policy::{
    validate_fixture_runtime_bind, FIXTURE_RUNTIME_CLASSIFICATION, FIXTURE_RUNTIME_OPT_IN_FLAG,
    FIXTURE_RUNTIME_POLICY_CONTRACT,
};
use trnm_world_command::WorldCommand;

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1).filter(|arg| arg != "--");
    match args.next().as_deref().unwrap_or("home-json") {
        "serve" => {
            let mut bind = "127.0.0.1:8787".to_string();
            let mut actor_id = "local-player".to_string();
            let mut state_file = None;
            let mut reset_state = false;
            let mut allow_fixture_runtime = false;
            while let Some(arg) = args.next() {
                match arg.as_str() {
                    "--bind" => {
                        bind = args
                            .next()
                            .ok_or_else(|| anyhow::anyhow!("--bind requires an address"))?;
                    }
                    "--actor-id" => {
                        actor_id = args
                            .next()
                            .ok_or_else(|| anyhow::anyhow!("--actor-id requires a value"))?;
                    }
                    "--state-file" => {
                        state_file = Some(
                            args.next()
                                .ok_or_else(|| anyhow::anyhow!("--state-file requires a path"))?
                                .into(),
                        );
                    }
                    "--reset-state" => {
                        reset_state = true;
                    }
                    FIXTURE_RUNTIME_OPT_IN_FLAG => {
                        allow_fixture_runtime = true;
                    }
                    other => bail!("unknown serve option: {other}"),
                }
            }
            let validated_bind = validate_fixture_runtime_bind(allow_fixture_runtime, &bind)?;
            eprintln!(
                "classification={FIXTURE_RUNTIME_CLASSIFICATION} policy={FIXTURE_RUNTIME_POLICY_CONTRACT} public_ingress_allowed=false production_authorization=not_granted bind={validated_bind}"
            );
            trnm_world_server::serve_dev_runtime(
                &validated_bind.to_string(),
                &actor_id,
                state_file,
                reset_state,
            )?;
        }
        "fixture-runtime-policy" => {
            let response = serde_json::json!({
                "contract_version": FIXTURE_RUNTIME_POLICY_CONTRACT,
                "classification": FIXTURE_RUNTIME_CLASSIFICATION,
                "explicit_opt_in_flag": FIXTURE_RUNTIME_OPT_IN_FLAG,
                "literal_ip_socket_required": true,
                "loopback_only": true,
                "ephemeral_port_allowed": false,
                "public_ingress_allowed": false,
                "production_authorization": "not_granted",
            });
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "production-adapter-readiness" => {
            let response = trnm_world_api::world_production_adapter_readiness();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "dev-runtime-smoke" => {
            let response = trnm_world_server::build_dev_runtime_smoke_json();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "dev-runtime-repository-smoke" => {
            let state_file = args.next().unwrap_or_else(|| {
                "../acceptance/S0_world_dev_environment/latest/world-dev-runtime-state.json"
                    .to_string()
            });
            let response = trnm_world_server::build_dev_runtime_repository_smoke_json(state_file)?;
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "home-json" => {
            let response = trnm_world_server::build_home_response("local-player");
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "home-fragment" => {
            println!("{}", trnm_world_server::build_home_fragment("local-player"));
        }
        "cex-map-home-json" => {
            let response = trnm_world_server::build_cex_default_home_response("local-player");
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "move-east" => {
            let response = trnm_world_server::apply_fixture_command(WorldCommand::Move {
                actor_id: "local-player".to_string(),
                direction: "east".to_string(),
            });
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "route-target" => {
            let command = args.collect::<Vec<_>>().join(" ");
            let command = if command.trim().is_empty() {
                "/work deliver latest 成果 证据 风险 下一步 自检".to_string()
            } else {
                command
            };
            let response = trnm_world_server::build_route_command_target_response(&command);
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "route-artifacts" => {
            let response = trnm_world_server::build_route_artifacts_response();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "map-runtime-budget" => {
            let response = trnm_world_server::build_map_runtime_budget_response();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "map-pack-manifest" => {
            let response = trnm_world_server::build_map_pack_manifest_response();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "map-pack-attribution-evidence" => {
            let response = trnm_world_server::build_map_pack_attribution_evidence_response();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "map-pack-sensitive-poi-report" => {
            let response = trnm_world_server::build_map_pack_sensitive_poi_report_response();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "map-modeling-gate" => {
            let response = trnm_world_server::build_map_modeling_gate_response();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "tactics-command" => {
            let command = args.next().unwrap_or_else(|| "train_skill".to_string());
            let response = trnm_world_server::apply_fixture_tactics_command(&command);
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "adapter-readiness" => {
            let response = trnm_world_server::build_adapter_readiness_response();
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        "full-split-json" => {
            let response = trnm_world_server::build_full_split_response("local-player");
            println!("{}", serde_json::to_string_pretty(&response)?);
        }
        other => bail!("unknown trnm-world-server command: {other}"),
    }
    Ok(())
}
