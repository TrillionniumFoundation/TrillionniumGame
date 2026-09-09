#![recursion_limit = "256"]

#[path = "http_policy/mod.rs"]
mod http_policy;

use reqwest::Client;
use serde::{de::DeserializeOwned, Serialize};
use serde_json::{json, Value};
use std::{
    net::TcpStream,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::{process::Command, sync::Barrier};
use trnm_online_protocol::{
    apply_snapshot_delta, OnlineCampaignConnectRequest, OnlineCampaignView, OnlineCommandReceipt,
    OnlineCommandSubmitRequest, OnlineMatchAccessRequest, OnlineMatchCreateRequest,
    OnlineMatchJoinRequest, OnlineMatchPhase, OnlineMatchStartRequest, OnlineMatchView,
    OnlineReconnectRequest, OnlineReconnectResponse, OnlineSnapshotResponse,
    OnlineStreamConnectRequest, OnlineStreamServerMessage, ONLINE_AUTHORITY_BUILD,
    ONLINE_AUTHORITY_PROTOCOL, ONLINE_STREAM_PROTOCOL,
};
use trnm_rts_protocol::{RtsFrameOrder, RtsOrderKind, RtsOrderSource, RtsTile};
use trnm_rts_sim::MissionSimV1;
use tungstenite::{
    client::IntoClientRequest,
    http::{header::SEC_WEBSOCKET_PROTOCOL, HeaderValue},
    stream::MaybeTlsStream,
    Message, WebSocket,
};

const SNAPSHOT_RECOVERABLE_RETRY_TIMEOUT: Duration = Duration::from_secs(10);
const SNAPSHOT_RECOVERABLE_RETRY_INTERVAL: Duration = Duration::from_secs(1);

#[derive(Clone)]
struct Identity {
    player_id: String,
    account_id: String,
    session: String,
}

struct OnlineClient {
    base_url: String,
    http: Client,
    non_idempotent_http: Client,
    command_ack_ms: Mutex<Vec<u64>>,
}

struct CommandSpec {
    command_id: String,
    kind: RtsOrderKind,
    subjects: Vec<String>,
    target: RtsTile,
    queued: bool,
}

#[derive(Clone, Copy)]
struct ObjectiveMove {
    target: RtsTile,
    kind: RtsOrderKind,
    require_all_arrived: bool,
}

struct StreamSmokeCursor {
    actor_generation: String,
    state_sequence: u64,
    snapshot_hash: String,
    authoritative_tick: u64,
    snapshot: Value,
}

fn connect_stream_smoke(
    client: &OnlineClient,
    identity: &Identity,
    match_id: &str,
    snapshot: &OnlineSnapshotResponse,
) -> Result<(WebSocket<MaybeTlsStream<TcpStream>>, StreamSmokeCursor, u64), String> {
    let mut url = reqwest::Url::parse(&client.base_url)
        .map_err(|error| format!("state stream URL: {error}"))?;
    if url.scheme() != "http" {
        return Err(
            "online E2E state stream currently requires a local HTTP authority".to_string(),
        );
    }
    url.set_scheme("ws")
        .map_err(|()| "state stream URL scheme".to_string())?;
    let base_path = url.path().trim_end_matches('/').to_string();
    url.set_path(&format!("{base_path}/v1/online/matches/{match_id}/stream"));
    url.set_query(None);
    url.set_fragment(None);
    let connect = OnlineStreamConnectRequest {
        protocol_version: ONLINE_STREAM_PROTOCOL.to_string(),
        build_id: ONLINE_AUTHORITY_BUILD.to_string(),
        player_id: identity.player_id.clone(),
        account_id: identity.account_id.clone(),
        next_receipt_sequence: snapshot.view.next_sequence,
        last_snapshot_hash: snapshot.view.snapshot_hash.clone(),
    };
    url.query_pairs_mut()
        .append_pair("protocol_version", &connect.protocol_version)
        .append_pair("build_id", &connect.build_id)
        .append_pair("player_id", &connect.player_id)
        .append_pair("account_id", &connect.account_id)
        .append_pair(
            "next_receipt_sequence",
            &connect.next_receipt_sequence.to_string(),
        )
        .append_pair("last_snapshot_hash", &connect.last_snapshot_hash);
    if url.as_str().contains(&identity.session) {
        return Err("state stream URL leaked the player session".to_string());
    }
    let mut request = url
        .as_str()
        .into_client_request()
        .map_err(|error| format!("state stream request: {error}"))?;
    request.headers_mut().insert(
        "x-trnm-player-session",
        HeaderValue::from_str(&identity.session)
            .map_err(|error| format!("state stream session header: {error}"))?,
    );
    request.headers_mut().insert(
        SEC_WEBSOCKET_PROTOCOL,
        HeaderValue::from_static(ONLINE_STREAM_PROTOCOL),
    );
    let (mut socket, response) = tungstenite::client::connect_with_config(request, None, 0)
        .map_err(|error| format!("connect state stream: {error}"))?;
    if response
        .headers()
        .get(SEC_WEBSOCKET_PROTOCOL)
        .and_then(|value| value.to_str().ok())
        != Some(ONLINE_STREAM_PROTOCOL)
    {
        return Err("state stream server selected an unexpected subprotocol".to_string());
    }
    match socket.get_mut() {
        MaybeTlsStream::Plain(stream) => stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .map_err(|error| format!("state stream read timeout: {error}"))?,
        _ => return Err("online E2E state stream received an unexpected TLS socket".to_string()),
    }
    let (cursor, next_sequence) = read_initial_stream_snapshot(&mut socket, match_id)?;
    Ok((socket, cursor, next_sequence))
}

fn read_initial_stream_snapshot(
    socket: &mut WebSocket<MaybeTlsStream<TcpStream>>,
    match_id: &str,
) -> Result<(StreamSmokeCursor, u64), String> {
    loop {
        match socket
            .read()
            .map_err(|error| format!("read initial state stream snapshot: {error}"))?
        {
            Message::Text(text) => {
                let message = serde_json::from_str::<OnlineStreamServerMessage>(&text)
                    .map_err(|error| format!("decode initial state stream snapshot: {error}"))?;
                if let OnlineStreamServerMessage::FullSnapshot {
                    actor_generation,
                    state_sequence,
                    next_receipt_sequence,
                    view,
                    snapshot,
                } = message
                {
                    if view.match_id != match_id || next_receipt_sequence != view.next_sequence {
                        return Err("initial state stream authority cursor mismatch".to_string());
                    }
                    validate_stream_smoke_snapshot(&view, &snapshot)?;
                    return Ok((
                        StreamSmokeCursor {
                            actor_generation,
                            state_sequence,
                            snapshot_hash: view.snapshot_hash,
                            authoritative_tick: view.authoritative_tick,
                            snapshot,
                        },
                        view.next_sequence,
                    ));
                }
                return Err("state stream did not begin with a full snapshot".to_string());
            }
            Message::Ping(_) => socket
                .flush()
                .map_err(|error| format!("flush state stream pong: {error}"))?,
            Message::Pong(_) => {}
            other => return Err(format!("unexpected initial state stream frame: {other:?}")),
        }
    }
}

fn wait_for_stream_total_order(
    socket: &mut WebSocket<MaybeTlsStream<TcpStream>>,
    cursor: &mut StreamSmokeCursor,
    match_id: &str,
    expected_next_sequence: u64,
) -> Result<(), String> {
    let mut saw_delta = false;
    for _ in 0..256 {
        match socket
            .read()
            .map_err(|error| format!("read state stream update: {error}"))?
        {
            Message::Text(text) => {
                let message = serde_json::from_str::<OnlineStreamServerMessage>(&text)
                    .map_err(|error| format!("decode state stream update: {error}"))?;
                let next_sequence = match message {
                    OnlineStreamServerMessage::FullSnapshot {
                        actor_generation,
                        state_sequence,
                        next_receipt_sequence,
                        view,
                        snapshot,
                    } => {
                        if actor_generation != cursor.actor_generation
                            || state_sequence < cursor.state_sequence
                            || view.match_id != match_id
                            || next_receipt_sequence != view.next_sequence
                        {
                            return Err("state stream full snapshot cursor regressed".to_string());
                        }
                        validate_stream_smoke_snapshot(&view, &snapshot)?;
                        cursor.state_sequence = state_sequence;
                        cursor.snapshot_hash = view.snapshot_hash.clone();
                        cursor.authoritative_tick = view.authoritative_tick;
                        cursor.snapshot = snapshot;
                        view.next_sequence
                    }
                    OnlineStreamServerMessage::SnapshotDelta {
                        actor_generation,
                        state_sequence,
                        base_state_sequence,
                        view,
                        delta,
                    } => {
                        saw_delta = true;
                        if actor_generation != cursor.actor_generation
                            || base_state_sequence != cursor.state_sequence
                            || state_sequence <= base_state_sequence
                            || view.match_id != match_id
                        {
                            return Err("state stream delta cursor mismatch".to_string());
                        }
                        apply_snapshot_delta(
                            &mut cursor.snapshot,
                            &cursor.snapshot_hash,
                            cursor.authoritative_tick,
                            &delta,
                        )?;
                        validate_stream_smoke_snapshot(&view, &cursor.snapshot)?;
                        cursor.state_sequence = state_sequence;
                        cursor.snapshot_hash = delta.snapshot_hash;
                        cursor.authoritative_tick = delta.authoritative_tick;
                        view.next_sequence
                    }
                    OnlineStreamServerMessage::ResyncRequired { reason, .. } => {
                        return Err(format!("state stream requested resync: {reason}"));
                    }
                };
                if next_sequence >= expected_next_sequence && saw_delta {
                    return Ok(());
                }
            }
            Message::Ping(_) => socket
                .flush()
                .map_err(|error| format!("flush state stream pong: {error}"))?,
            Message::Pong(_) => {}
            Message::Close(frame) => {
                return Err(format!(
                    "state stream closed before command update: {frame:?}"
                ));
            }
            other => return Err(format!("unexpected state stream frame: {other:?}")),
        }
    }
    Err("state stream did not reach the expected total-order cursor".to_string())
}

fn validate_stream_smoke_snapshot(view: &OnlineMatchView, snapshot: &Value) -> Result<(), String> {
    if view.protocol_version != ONLINE_AUTHORITY_PROTOCOL || view.build_id != ONLINE_AUTHORITY_BUILD
    {
        return Err("state stream authority contract mismatch".to_string());
    }
    let simulation = serde_json::from_value::<MissionSimV1>(snapshot.clone())
        .map_err(|error| format!("state stream mission decode: {error}"))?;
    if simulation.tick != view.authoritative_tick
        || simulation
            .snapshot_hash()
            .map_err(|error| format!("state stream mission hash: {error}"))?
            != view.snapshot_hash
    {
        return Err("state stream mission snapshot hash/tick mismatch".to_string());
    }
    Ok(())
}

fn summarize_milliseconds(mut samples: Vec<u64>) -> (Vec<u64>, u64, u64) {
    samples.sort_unstable();
    if samples.is_empty() {
        return (samples, 0, 0);
    }
    let p95_index = (samples.len() * 95).div_ceil(100).saturating_sub(1);
    let p95_ms = samples[p95_index];
    let max_ms = *samples.last().unwrap_or(&0);
    (samples, p95_ms, max_ms)
}

impl OnlineClient {
    fn new(base_url: String) -> Result<Self, String> {
        Ok(Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            http: http_policy::client_builder(Duration::from_secs(2), Duration::from_secs(4))
                .build()
                .map_err(|error| error.to_string())?,
            non_idempotent_http: http_policy::client_builder(
                Duration::from_secs(2),
                Duration::from_secs(30),
            )
            .build()
            .map_err(|error| error.to_string())?,
            command_ack_ms: Mutex::new(Vec::new()),
        })
    }

    async fn post_one_shot_non_idempotent<T: Serialize, R: DeserializeOwned>(
        &self,
        identity: &Identity,
        path: &str,
        body: &T,
    ) -> Result<R, String> {
        // Create and join have no request ID, so a timeout cannot be retried
        // without risking a second mutation. Keep them on a long, one-shot
        // request path until the protocol exposes an idempotency key.
        let response = self
            .non_idempotent_http
            .post(format!("{}{}", self.base_url, path))
            .header("x-trnm-player-session", &identity.session)
            .json(body)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let status = response.status();
        let bytes = response.bytes().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!(
                "POST {path} returned {status}: {}",
                String::from_utf8_lossy(&bytes)
            ));
        }
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())
    }

    async fn start_with_lost_response_retry(
        &self,
        identity: &Identity,
        match_id: &str,
        body: &OnlineMatchStartRequest,
    ) -> Result<OnlineMatchView, String> {
        let path = format!("/v1/online/matches/{match_id}/start");
        let request = self
            .http
            .post(format!("{}{}", self.base_url, path))
            .header("x-trnm-player-session", &identity.session)
            .json(body);
        let response = send_with_lost_response_retry(request).await?;
        let status = response.status();
        let bytes = response.bytes().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!(
                "POST {path} returned {status}: {}",
                String::from_utf8_lossy(&bytes)
            ));
        }
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())
    }

    async fn post<T: Serialize, R: DeserializeOwned>(
        &self,
        identity: &Identity,
        path: &str,
        body: &T,
    ) -> Result<R, String> {
        let request = self
            .http
            .post(format!("{}{}", self.base_url, path))
            .header("x-trnm-player-session", &identity.session)
            .json(body);
        let started = Instant::now();
        let response = send_with_retry(request).await?;
        let status = response.status();
        let bytes = response.bytes().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!(
                "POST {path} returned {status}: {}",
                String::from_utf8_lossy(&bytes)
            ));
        }
        if path.ends_with("/commands") {
            let elapsed_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
            if let Ok(mut samples) = self.command_ack_ms.lock() {
                samples.push(elapsed_ms);
            }
        }
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())
    }

    async fn tick_interval_ms(&self) -> Result<f64, String> {
        let response = send_with_retry(
            self.http
                .get(format!("{}/v1/online/readiness", self.base_url)),
        )
        .await?;
        let status = response.status();
        let value = response
            .json::<Value>()
            .await
            .map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("readiness returned {status}: {value}"));
        }
        value["tick_interval_ms"]
            .as_f64()
            .filter(|interval| *interval > 0.0)
            .ok_or_else(|| "readiness did not expose a valid tick_interval_ms".to_string())
    }

    fn command_ack_summary(&self) -> (Vec<u64>, u64, u64) {
        let samples = self
            .command_ack_ms
            .lock()
            .map(|samples| samples.clone())
            .unwrap_or_default();
        summarize_milliseconds(samples)
    }

    async fn post_status<T: Serialize>(
        &self,
        identity: &Identity,
        path: &str,
        body: &T,
    ) -> Result<(u16, Value), String> {
        let request = self
            .http
            .post(format!("{}{}", self.base_url, path))
            .header("x-trnm-player-session", &identity.session)
            .json(body);
        let response = send_with_retry(request).await?;
        let status = response.status().as_u16();
        let value = response
            .json::<Value>()
            .await
            .map_err(|error| error.to_string())?;
        Ok((status, value))
    }

    async fn snapshot(
        &self,
        identity: &Identity,
        match_id: &str,
    ) -> Result<OnlineSnapshotResponse, String> {
        let path = format!("/v1/online/matches/{match_id}/snapshot");
        let request = OnlineMatchAccessRequest {
            protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
            build_id: ONLINE_AUTHORITY_BUILD.to_string(),
            player_id: identity.player_id.clone(),
            account_id: identity.account_id.clone(),
        };
        let retry_started = Instant::now();
        let response = loop {
            let (status, body) = self.post_status(identity, &path, &request).await?;
            if (200..300).contains(&status) {
                break serde_json::from_value::<OnlineSnapshotResponse>(body)
                    .map_err(|error| error.to_string())?;
            }
            if publication_transition_is_recoverable(status, &body)
                && retry_started.elapsed() < SNAPSHOT_RECOVERABLE_RETRY_TIMEOUT
            {
                // The Authority advertises Retry-After: 1 while a running
                // tuple crosses its durable terminal publication barrier.
                // Honor that contract without hiding non-recoverable errors or
                // allowing an unbounded terminal wait.
                tokio::time::sleep(SNAPSHOT_RECOVERABLE_RETRY_INTERVAL).await;
                continue;
            }
            let status = reqwest::StatusCode::from_u16(status)
                .map(|status| status.to_string())
                .unwrap_or_else(|_| status.to_string());
            return Err(format!("POST {path} returned {status}: {body}"));
        };
        if let Some(snapshot_tick) = response.snapshot.get("tick").and_then(Value::as_u64) {
            if response.view.authoritative_tick != snapshot_tick {
                return Err(format!(
                    "snapshot/view authoritative tick mismatch: view={} snapshot={snapshot_tick}",
                    response.view.authoritative_tick
                ));
            }
        }
        Ok(response)
    }

    async fn reconnect(
        &self,
        identity: &Identity,
        match_id: &str,
        last_acknowledged_sequence: u64,
        last_snapshot_hash: String,
    ) -> Result<OnlineReconnectResponse, String> {
        let path = format!("/v1/online/matches/{match_id}/reconnect");
        let request = OnlineReconnectRequest {
            protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
            build_id: ONLINE_AUTHORITY_BUILD.to_string(),
            player_id: identity.player_id.clone(),
            account_id: identity.account_id.clone(),
            last_acknowledged_sequence,
            last_snapshot_hash,
            next_receipt_sequence: Some(last_acknowledged_sequence),
        };
        let retry_started = Instant::now();
        loop {
            let (status, body) = self.post_status(identity, &path, &request).await?;
            if (200..300).contains(&status) {
                return serde_json::from_value(body).map_err(|error| error.to_string());
            }
            if publication_transition_is_recoverable(status, &body)
                && retry_started.elapsed() < SNAPSHOT_RECOVERABLE_RETRY_TIMEOUT
            {
                tokio::time::sleep(SNAPSHOT_RECOVERABLE_RETRY_INTERVAL).await;
                continue;
            }
            let status = reqwest::StatusCode::from_u16(status)
                .map(|status| status.to_string())
                .unwrap_or_else(|_| status.to_string());
            return Err(format!("POST {path} returned {status}: {body}"));
        }
    }

    async fn submit(
        &self,
        identity: &Identity,
        match_id: &str,
        snapshot: &OnlineSnapshotResponse,
        spec: CommandSpec,
    ) -> Result<(OnlineCommandReceipt, OnlineCommandSubmitRequest), String> {
        let target_tick = snapshot.view.authoritative_tick;
        let input_sequence = snapshot
            .view
            .members
            .iter()
            .find(|member| member.player_id == identity.player_id)
            .map(|member| member.next_input_sequence)
            .ok_or_else(|| "snapshot is missing the submitting member".to_string())?;
        let mut order = RtsFrameOrder::new(
            u32::try_from(target_tick).map_err(|_| "target tick overflow".to_string())?,
            &identity.player_id,
            spec.subjects,
            spec.kind,
            RtsOrderSource::LocalInput,
        );
        order.target_tile = Some(spec.target);
        order.queued = spec.queued;
        if spec.queued {
            order.queue_id = Some(format!("queue-{}", spec.command_id));
        }
        match spec.kind {
            RtsOrderKind::Attack | RtsOrderKind::FocusFire => {
                order.target_actor_id = Some("relay_beacon".to_string());
            }
            RtsOrderKind::Ability => {
                order.target_rule_id = Some("party_signature".to_string());
            }
            _ => {}
        }
        let mut request = OnlineCommandSubmitRequest {
            protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
            build_id: ONLINE_AUTHORITY_BUILD.to_string(),
            player_id: identity.player_id.clone(),
            account_id: identity.account_id.clone(),
            command_id: spec.command_id,
            sequence: snapshot.view.next_sequence,
            input_sequence: Some(input_sequence),
            expected_match_revision: snapshot.view.match_revision,
            target_tick,
            client_observed_tick: Some(snapshot.view.authoritative_tick),
            order,
        };
        let path = format!("/v1/online/matches/{match_id}/commands");
        let receipt = match self.post(identity, &path, &request).await {
            Ok(receipt) => receipt,
            Err(error) if error.contains("expected player input sequence") => {
                let fresh = self.snapshot(identity, match_id).await?;
                request.sequence = fresh.view.next_sequence;
                request.input_sequence = fresh
                    .view
                    .members
                    .iter()
                    .find(|member| member.player_id == identity.player_id)
                    .map(|member| member.next_input_sequence);
                request.expected_match_revision = fresh.view.match_revision;
                request.target_tick = fresh.view.authoritative_tick;
                request.client_observed_tick = Some(fresh.view.authoritative_tick);
                request.order.frame = u32::try_from(request.target_tick)
                    .map_err(|_| "target tick overflow".to_string())?;
                self.post(identity, &path, &request).await?
            }
            Err(error) => return Err(error),
        };
        Ok((receipt, request))
    }
}

fn publication_transition_is_recoverable(status: u16, body: &Value) -> bool {
    status == reqwest::StatusCode::SERVICE_UNAVAILABLE.as_u16()
        && body["recoverable"].as_bool() == Some(true)
}

async fn send_with_retry(request: reqwest::RequestBuilder) -> Result<reqwest::Response, String> {
    send_with_retry_inner(request, false).await
}

async fn send_with_lost_response_retry(
    request: reqwest::RequestBuilder,
) -> Result<reqwest::Response, String> {
    send_with_retry_inner(request, true).await
}

async fn send_with_retry_inner(
    request: reqwest::RequestBuilder,
    mut lose_first_successful_response: bool,
) -> Result<reqwest::Response, String> {
    let mut transport_failures = 0_u64;
    let last_error = loop {
        let retry = request
            .try_clone()
            .ok_or_else(|| "request cannot be safely retried".to_string())?;
        match retry.send().await {
            Ok(response) if lose_first_successful_response && response.status().is_success() => {
                // The server has committed the mutation, but the simulated
                // client never observes the response. Draining then discarding
                // it keeps the connection healthy before resending the exact
                // cloned request and exercising the server's idempotent branch.
                let _ = response.bytes().await;
                lose_first_successful_response = false;
            }
            Ok(response) => return Ok(response),
            Err(error) => {
                if transport_failures >= 3 {
                    break error;
                }
                transport_failures += 1;
                tokio::time::sleep(Duration::from_millis(100 * transport_failures)).await;
            }
        }
    };
    Err(last_error.to_string())
}

fn env_identity(prefix: &str) -> Result<Identity, String> {
    Ok(Identity {
        player_id: std::env::var(format!("{prefix}_PLAYER_ID"))
            .map_err(|_| format!("{prefix}_PLAYER_ID is required"))?,
        account_id: std::env::var(format!("{prefix}_ACCOUNT_ID"))
            .map_err(|_| format!("{prefix}_ACCOUNT_ID is required"))?,
        session: std::env::var(format!("{prefix}_SESSION"))
            .map_err(|_| format!("{prefix}_SESSION is required"))?,
    })
}

async fn wait_for(
    client: &OnlineClient,
    identity: &Identity,
    match_id: &str,
    timeout: Duration,
    predicate: impl Fn(&OnlineSnapshotResponse) -> bool,
) -> Result<OnlineSnapshotResponse, String> {
    let started = Instant::now();
    while started.elapsed() < timeout {
        let snapshot = client.snapshot(identity, match_id).await?;
        if predicate(&snapshot) {
            return Ok(snapshot);
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    Err(format!("online condition timed out after {timeout:?}"))
}

fn phase_timeout() -> Duration {
    Duration::from_secs(
        std::env::var("TRNM_ONLINE_E2E_PHASE_TIMEOUT_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(240)
            .clamp(45, 900),
    )
}

fn completion_timeout() -> Duration {
    Duration::from_secs(
        std::env::var("TRNM_ONLINE_E2E_COMPLETION_TIMEOUT_SECONDS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(360)
            .clamp(60, 1_200),
    )
}

fn point(snapshot: &OnlineSnapshotResponse, key: &str) -> Result<RtsTile, String> {
    let value = snapshot
        .snapshot
        .pointer(&format!("/seed/map/{key}"))
        .ok_or_else(|| format!("snapshot is missing map point {key}"))?;
    Ok(RtsTile::new(
        value["x"]
            .as_i64()
            .ok_or_else(|| "point.x missing".to_string())? as i32,
        value["y"]
            .as_i64()
            .ok_or_else(|| "point.y missing".to_string())? as i32,
    ))
}

fn controlled(
    snapshot: &OnlineSnapshotResponse,
    identity: &Identity,
) -> Result<Vec<String>, String> {
    let assigned = snapshot
        .view
        .members
        .iter()
        .find(|member| member.player_id == identity.player_id)
        .map(|member| member.controlled_unit_ids.clone())
        .ok_or_else(|| "identity is missing from match view".to_string())?;
    let living = snapshot.snapshot["party"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|unit| unit["hp"].as_i64().unwrap_or_default() > 0)
        .filter_map(|unit| unit["unit_id"].as_str())
        .collect::<std::collections::BTreeSet<_>>();
    Ok(assigned
        .into_iter()
        .filter(|unit_id| living.contains(unit_id.as_str()))
        .collect())
}

fn select_largest_living_squad_prefer_guest<'a>(
    host: &'a Identity,
    host_units: Vec<String>,
    guest: &'a Identity,
    guest_units: Vec<String>,
) -> Option<(&'a Identity, Vec<String>)> {
    let selected = if guest_units.len() >= host_units.len() {
        (guest, guest_units)
    } else {
        (host, host_units)
    };
    (!selected.1.is_empty()).then_some(selected)
}

#[cfg(test)]
fn first_living_enemy_target(snapshot: &Value) -> Option<(String, RtsTile)> {
    let enemy = snapshot["enemies"]
        .as_array()?
        .iter()
        .find(|enemy| enemy["hp"].as_i64().unwrap_or_default() > 0)?;
    let enemy_id = enemy["unit_id"].as_str()?.to_string();
    let x = i32::try_from(enemy["position"]["x"].as_i64()?).ok()?;
    let y = i32::try_from(enemy["position"]["y"].as_i64()?).ok()?;
    Some((enemy_id, RtsTile::new(x, y)))
}

fn enemy_is_alive(snapshot: &Value, enemy_id: &str) -> bool {
    snapshot["enemies"]
        .as_array()
        .into_iter()
        .flatten()
        .any(|enemy| {
            enemy["unit_id"].as_str() == Some(enemy_id)
                && enemy["hp"].as_i64().unwrap_or_default() > 0
        })
}

fn living_selected_count(snapshot: &Value, selected: &std::collections::BTreeSet<String>) -> usize {
    snapshot["party"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|unit| unit["hp"].as_i64().unwrap_or_default() > 0)
        .filter_map(|unit| unit["unit_id"].as_str())
        .filter(|unit_id| selected.contains(*unit_id))
        .count()
}

fn selected_signature_ability_ready(
    snapshot: &Value,
    selected: &std::collections::BTreeSet<String>,
) -> bool {
    snapshot["party"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|unit| unit["hp"].as_i64().unwrap_or_default() > 0)
        .filter(|unit| {
            unit["unit_id"]
                .as_str()
                .is_some_and(|unit_id| selected.contains(unit_id))
        })
        .any(|unit| {
            let skills = unit["skill_ids"].as_array();
            let has_skill = |skill: &str| {
                skills
                    .into_iter()
                    .flatten()
                    .any(|candidate| candidate.as_str() == Some(skill))
            };
            let energy_cost = if has_skill("field_mend") {
                26
            } else if has_skill("relay_overcharge") {
                24
            } else if has_skill("inner_flame") {
                28
            } else if has_skill("wind_step") {
                22
            } else {
                18
            };
            unit["ability_cooldown_ticks"].as_u64().unwrap_or_default() == 0
                && unit["energy"].as_i64().unwrap_or_default() >= energy_cost
        })
}

async fn clear_living_enemies(
    client: &OnlineClient,
    host: &Identity,
    guest: &Identity,
    match_id: &str,
    snapshot: OnlineSnapshotResponse,
    objective: RtsTile,
    command_prefix: &str,
) -> Result<OnlineSnapshotResponse, String> {
    let living_enemies = snapshot.snapshot["enemies"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|enemy| enemy["hp"].as_i64().unwrap_or_default() > 0)
        .filter_map(|enemy| enemy["unit_id"].as_str().map(str::to_string))
        .collect::<Vec<_>>();
    if living_enemies.is_empty() {
        return Ok(snapshot);
    }

    let host_units = controlled(&snapshot, host)?;
    let initial_guest_units = controlled(&snapshot, guest)?;
    if host_units.is_empty() && initial_guest_units.is_empty() {
        return Err(format!(
            "{command_prefix} has living enemies but no living player squad"
        ));
    }

    let host_attack_issued = !host_units.is_empty();
    let mut attack_snapshot = snapshot;
    if host_attack_issued {
        let _ = client
            .submit(
                host,
                match_id,
                &attack_snapshot,
                CommandSpec {
                    command_id: format!("{command_prefix}-host-attack"),
                    kind: RtsOrderKind::Attack,
                    subjects: host_units,
                    target: objective,
                    queued: false,
                },
            )
            .await?;
        attack_snapshot = client.snapshot(guest, match_id).await?;
    }
    let guest_units = controlled(&attack_snapshot, guest)?;
    if !guest_units.is_empty() {
        let _ = client
            .submit(
                guest,
                match_id,
                &attack_snapshot,
                CommandSpec {
                    command_id: format!("{command_prefix}-guest-attack"),
                    kind: RtsOrderKind::Attack,
                    subjects: guest_units,
                    target: objective,
                    queued: host_attack_issued,
                },
            )
            .await?;
    }

    let cleared = wait_for(client, host, match_id, phase_timeout(), |candidate| {
        candidate.snapshot["phase"] == "complete"
            || living_enemies
                .iter()
                .all(|enemy_id| !enemy_is_alive(&candidate.snapshot, enemy_id))
    })
    .await?;
    if cleared.snapshot["phase"] == "complete" {
        return Err(format!(
            "{command_prefix} reached terminal outcome {} before the wave cleared",
            cleared.snapshot["outcome"]
        ));
    }
    Ok(cleared)
}

async fn move_squad_to_objective(
    client: &OnlineClient,
    identity: &Identity,
    units: Vec<String>,
    match_id: &str,
    snapshot: &OnlineSnapshotResponse,
    movement: ObjectiveMove,
    command_prefix: &str,
) -> Result<OnlineSnapshotResponse, String> {
    if units.is_empty() {
        return Err(format!("{command_prefix} has no living objective holder"));
    }
    let _ = client
        .submit(
            identity,
            match_id,
            snapshot,
            CommandSpec {
                command_id: format!("{command_prefix}-move"),
                kind: movement.kind,
                subjects: units.clone(),
                target: movement.target,
                queued: false,
            },
        )
        .await?;
    let units = units.into_iter().collect::<std::collections::BTreeSet<_>>();
    let moved = wait_for(client, identity, match_id, phase_timeout(), |candidate| {
        candidate.snapshot["phase"] == "complete"
            || candidate.snapshot["party"].as_array().is_some_and(|party| {
                let (selected_count, arrived_count) = party
                    .iter()
                    .filter(|unit| {
                        unit["hp"].as_i64().unwrap_or_default() > 0
                            && unit["unit_id"]
                                .as_str()
                                .is_some_and(|unit_id| units.contains(unit_id))
                    })
                    .fold((0_usize, 0_usize), |(selected, arrived), unit| {
                        let is_arrived = (unit["position"]["x"].as_i64().unwrap_or_default()
                            - i64::from(movement.target.x))
                        .abs()
                            + (unit["position"]["y"].as_i64().unwrap_or_default()
                                - i64::from(movement.target.y))
                            .abs()
                            <= 2;
                        (
                            selected.saturating_add(1),
                            arrived.saturating_add(usize::from(is_arrived)),
                        )
                    });
                selected_count > 0
                    && if movement.require_all_arrived {
                        arrived_count == selected_count
                    } else {
                        arrived_count > 0
                    }
            })
    })
    .await?;
    if moved.snapshot["phase"] == "complete" {
        return Err(format!(
            "{command_prefix} reached terminal outcome {} before objective arrival",
            moved.snapshot["outcome"]
        ));
    }
    Ok(moved)
}

async fn move_largest_squad_to_objective<'a>(
    client: &OnlineClient,
    host: &'a Identity,
    guest: &'a Identity,
    match_id: &str,
    snapshot: &OnlineSnapshotResponse,
    objective: RtsTile,
    command_prefix: &str,
) -> Result<(&'a Identity, OnlineSnapshotResponse), String> {
    let host_units = controlled(snapshot, host)?;
    let guest_units = controlled(snapshot, guest)?;
    let Some((identity, units)) =
        select_largest_living_squad_prefer_guest(host, host_units, guest, guest_units)
    else {
        return Err(format!("{command_prefix} has no living objective holder"));
    };
    let moved = move_squad_to_objective(
        client,
        identity,
        units,
        match_id,
        snapshot,
        ObjectiveMove {
            target: objective,
            kind: RtsOrderKind::AttackMove,
            require_all_arrived: true,
        },
        command_prefix,
    )
    .await?;
    Ok((identity, moved))
}

async fn move_largest_squad_to_objective_and_hold(
    client: &OnlineClient,
    host: &Identity,
    guest: &Identity,
    match_id: &str,
    snapshot: &OnlineSnapshotResponse,
    objective: RtsTile,
    command_prefix: &str,
) -> Result<OnlineSnapshotResponse, String> {
    let (capture_identity, moved) = move_largest_squad_to_objective(
        client,
        host,
        guest,
        match_id,
        snapshot,
        objective,
        command_prefix,
    )
    .await?;
    let hold_units = controlled(&moved, capture_identity)?;
    if hold_units.is_empty() {
        return Err(format!("{command_prefix} objective squad died before hold"));
    }
    let _ = client
        .submit(
            capture_identity,
            match_id,
            &moved,
            CommandSpec {
                command_id: format!("{command_prefix}-hold"),
                kind: RtsOrderKind::Hold,
                subjects: hold_units,
                target: objective,
                queued: false,
            },
        )
        .await?;
    Ok(moved)
}

async fn restart_server(base_url: &str) -> Result<(), String> {
    let status = Command::new("systemctl")
        .args(["--user", "restart", "trnm-game-server.service"])
        .status()
        .await
        .map_err(|error| format!("restart game server: {error}"))?;
    if !status.success() {
        return Err("systemd rejected game-server restart".to_string());
    }
    let http = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|error| error.to_string())?;
    let started = Instant::now();
    while started.elapsed() < Duration::from_secs(30) {
        if http
            .get(format!(
                "{}/v1/online/readiness",
                base_url.trim_end_matches('/')
            ))
            .send()
            .await
            .is_ok_and(|response| response.status().is_success())
        {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    Err("game server did not recover after restart".to_string())
}

async fn run() -> Result<Value, String> {
    let base_url = std::env::var("TRNM_GAME_SERVER_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:7005".to_string());
    let host = env_identity("TRNM_ONLINE_HOST")?;
    let guest = env_identity("TRNM_ONLINE_GUEST")?;
    let client = Arc::new(OnlineClient::new(base_url.clone())?);
    let tick_interval_ms = client.tick_interval_ms().await?;
    let run_id = format!("online-e2e-{}", chrono::Utc::now().timestamp_millis());
    let slot_key = std::env::var("TRNM_ONLINE_SLOT_KEY").unwrap_or_else(|_| run_id.clone());

    let campaign: OnlineCampaignView = client
        .post(
            &host,
            "/v1/online/campaigns/connect",
            &OnlineCampaignConnectRequest {
                protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
                build_id: ONLINE_AUTHORITY_BUILD.to_string(),
                player_id: host.player_id.clone(),
                account_id: host.account_id.clone(),
                slot_key: slot_key.clone(),
            },
        )
        .await?;
    let guest_campaign: OnlineCampaignView = client
        .post(
            &guest,
            "/v1/online/campaigns/connect",
            &OnlineCampaignConnectRequest {
                protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
                build_id: ONLINE_AUTHORITY_BUILD.to_string(),
                player_id: guest.player_id.clone(),
                account_id: guest.account_id.clone(),
                slot_key,
            },
        )
        .await?;
    let (created, started, start_lost_response_retry_verified) =
        if let Ok(match_id) = std::env::var("TRNM_ONLINE_EXISTING_MATCH_ID") {
            let snapshot = client.snapshot(&host, &match_id).await?;
            (snapshot.view.clone(), snapshot.view, false)
        } else {
            let created: OnlineMatchView = client
                .post_one_shot_non_idempotent(
                    &host,
                    "/v1/online/matches",
                    &OnlineMatchCreateRequest {
                        protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
                        build_id: ONLINE_AUTHORITY_BUILD.to_string(),
                        campaign_id: campaign.campaign_id.clone(),
                        map_id: "first_contact".to_string(),
                        expected_campaign_revision: campaign.campaign_revision,
                    },
                )
                .await?;
            let _: OnlineMatchView = client
                .post_one_shot_non_idempotent(
                    &guest,
                    "/v1/online/matches/join",
                    &OnlineMatchJoinRequest {
                        protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
                        build_id: ONLINE_AUTHORITY_BUILD.to_string(),
                        player_id: guest.player_id.clone(),
                        account_id: guest.account_id.clone(),
                        campaign_id: guest_campaign.campaign_id.clone(),
                        join_code: created.join_code.clone(),
                    },
                )
                .await?;
            let started = client
                .start_with_lost_response_retry(
                    &host,
                    &created.match_id,
                    &OnlineMatchStartRequest {
                        protocol_version: ONLINE_AUTHORITY_PROTOCOL.to_string(),
                        build_id: ONLINE_AUTHORITY_BUILD.to_string(),
                        player_id: host.player_id.clone(),
                        account_id: host.account_id.clone(),
                        expected_match_revision: 0,
                    },
                )
                .await?;
            (created, started, true)
        };
    if started.phase != OnlineMatchPhase::Running || started.members.len() != 2 {
        return Err("two-client match did not enter running phase".to_string());
    }
    let initial = client.snapshot(&host, &created.match_id).await?;
    let initial_guest = client.snapshot(&guest, &created.match_id).await?;
    if initial.view.match_revision != initial_guest.view.match_revision {
        return Err("initial two-player snapshots did not share one match revision".to_string());
    }
    let match_clock_started = Instant::now();
    let initial_authoritative_tick = initial.view.authoritative_tick;
    let approach = point(&initial, "approach_point")?;
    let objective = point(&initial, "objective")?;
    let host_units = controlled(&initial, &host)?;
    let guest_units = controlled(&initial_guest, &guest)?;
    // Only the legacy tungstenite smoke path is synchronous. Keep its work
    // outside Tokio scheduler workers; the HTTP and race paths stay async.
    let (mut state_stream, mut state_stream_cursor, streamed_initial_sequence) =
        tokio::task::block_in_place(|| {
            connect_stream_smoke(&client, &host, &created.match_id, &initial)
        })?;
    if streamed_initial_sequence != initial.view.next_sequence {
        return Err(
            "state stream initial total-order cursor diverged from HTTP snapshot".to_string(),
        );
    }

    let websocket_authoritative_effect_started = Instant::now();
    let host_submit = {
        let client = Arc::clone(&client);
        let host = host.clone();
        let match_id = created.match_id.clone();
        let snapshot = initial.clone();
        let command_id = format!("{run_id}-host-move");
        let subjects = host_units.clone();
        tokio::spawn(async move {
            client
                .submit(
                    &host,
                    &match_id,
                    &snapshot,
                    CommandSpec {
                        command_id,
                        kind: RtsOrderKind::Move,
                        subjects,
                        target: approach,
                        queued: true,
                    },
                )
                .await
        })
    };
    let guest_submit = {
        let client = Arc::clone(&client);
        let guest = guest.clone();
        let match_id = created.match_id.clone();
        let snapshot = initial_guest;
        let command_id = format!("{run_id}-guest-concurrent-move");
        let subjects = guest_units.clone();
        tokio::spawn(async move {
            client
                .submit(
                    &guest,
                    &match_id,
                    &snapshot,
                    CommandSpec {
                        command_id,
                        kind: RtsOrderKind::Move,
                        subjects,
                        target: approach,
                        queued: true,
                    },
                )
                .await
        })
    };
    let (first, first_request) = host_submit
        .await
        .map_err(|_| "host concurrent submit panicked".to_string())??;
    let (guest_concurrent, _) = guest_submit
        .await
        .map_err(|_| "guest concurrent submit panicked".to_string())??;
    let mut total_orders = [first.sequence, guest_concurrent.sequence];
    total_orders.sort_unstable();
    if total_orders[1] != total_orders[0].saturating_add(1)
        || first.input_sequence != 0
        || guest_concurrent.input_sequence != 0
    {
        return Err("two-player concurrent input did not receive independent input cursors and contiguous server total order".to_string());
    }
    tokio::task::block_in_place(|| {
        wait_for_stream_total_order(
            &mut state_stream,
            &mut state_stream_cursor,
            &created.match_id,
            total_orders[1].saturating_add(1),
        )
    })?;
    let websocket_authoritative_effect_ms =
        u64::try_from(websocket_authoritative_effect_started.elapsed().as_millis())
            .unwrap_or(u64::MAX);
    let websocket_authoritative_effect_sample_count =
        std::env::var("TRNM_ONLINE_E2E_EFFECT_SAMPLES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(20);
    let mut websocket_authoritative_effect_samples_ms =
        Vec::with_capacity(websocket_authoritative_effect_sample_count);
    for sample in 0..websocket_authoritative_effect_sample_count {
        let effect_identity = &host;
        let effect_units = &host_units;
        let effect_snapshot = client.snapshot(effect_identity, &created.match_id).await?;
        let effect_started = Instant::now();
        let (receipt, _) = client
            .submit(
                effect_identity,
                &created.match_id,
                &effect_snapshot,
                CommandSpec {
                    command_id: format!("{run_id}-effect-sample-{sample}"),
                    kind: RtsOrderKind::Move,
                    subjects: effect_units.clone(),
                    target: approach,
                    queued: false,
                },
            )
            .await?;
        tokio::task::block_in_place(|| {
            wait_for_stream_total_order(
                &mut state_stream,
                &mut state_stream_cursor,
                &created.match_id,
                receipt.sequence.saturating_add(1),
            )
        })?;
        websocket_authoritative_effect_samples_ms
            .push(u64::try_from(effect_started.elapsed().as_millis()).unwrap_or(u64::MAX));
    }
    let (
        websocket_authoritative_effect_samples_ms,
        websocket_authoritative_effect_p95_ms,
        websocket_authoritative_effect_max_ms,
    ) = summarize_milliseconds(websocket_authoritative_effect_samples_ms);
    if let Some(maximum_p95_ms) = std::env::var("TRNM_ONLINE_E2E_MAX_EFFECT_P95_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
    {
        if websocket_authoritative_effect_p95_ms > maximum_p95_ms {
            let (_, command_ack_p95_ms, command_ack_max_ms) = client.command_ack_summary();
            return Err(format!(
                "WebSocket authoritative effect p95 {}ms exceeded {}ms; samples_ms={:?}; command_ack_p95_ms={}; command_ack_max_ms={}",
                websocket_authoritative_effect_p95_ms,
                maximum_p95_ms,
                websocket_authoritative_effect_samples_ms,
                command_ack_p95_ms,
                command_ack_max_ms,
            ));
        }
    }
    let _ = tokio::task::block_in_place(|| state_stream.close(None));
    let reconnect_command_race_rounds = 32_u64;
    let reconnect_command_race_pipeline_depth = 4_usize;
    let reconnect_cursor = 0_u64;
    let reconnect_hash = "stale-race-snapshot".to_string();
    let mut reconnect_threads = Vec::with_capacity(reconnect_command_race_pipeline_depth);
    let validate_reconnect = |reconnect_thread: tokio::task::JoinHandle<
        Result<OnlineReconnectResponse, String>,
    >| async move {
        let raced_reconnect = reconnect_thread
            .await
            .map_err(|_| "reconnect race request panicked".to_string())??;
        if raced_reconnect.next_receipt_sequence < reconnect_cursor
            || raced_reconnect.view.next_sequence < raced_reconnect.next_receipt_sequence
        {
            return Err(
                "reconnect race returned a regressed or impossible replay cursor".to_string(),
            );
        }
        Ok(())
    };
    for round in 0..reconnect_command_race_rounds {
        let command_identity = host.clone();
        let subjects = host_units.clone();
        let command_snapshot = client
            .snapshot(&command_identity, &created.match_id)
            .await?;
        let barrier = Arc::new(Barrier::new(2));
        let race_command_id = format!("{run_id}-reconnect-race-{round}");
        let command_thread = {
            let client = Arc::clone(&client);
            let match_id = created.match_id.clone();
            let barrier = Arc::clone(&barrier);
            tokio::spawn(async move {
                barrier.wait().await;
                client
                    .submit(
                        &command_identity,
                        &match_id,
                        &command_snapshot,
                        CommandSpec {
                            command_id: race_command_id,
                            kind: RtsOrderKind::Move,
                            subjects,
                            target: approach,
                            queued: false,
                        },
                    )
                    .await
            })
        };
        let reconnect_thread = {
            let client = Arc::clone(&client);
            let reconnect_identity = guest.clone();
            let match_id = created.match_id.clone();
            let barrier = Arc::clone(&barrier);
            let last_snapshot_hash = reconnect_hash.clone();
            tokio::spawn(async move {
                barrier.wait().await;
                client
                    .reconnect(
                        &reconnect_identity,
                        &match_id,
                        reconnect_cursor,
                        last_snapshot_hash,
                    )
                    .await
            })
        };
        command_thread
            .await
            .map_err(|_| "reconnect race command panicked".to_string())??;
        reconnect_threads.push(reconnect_thread);
        if reconnect_threads.len() >= reconnect_command_race_pipeline_depth {
            validate_reconnect(reconnect_threads.remove(0)).await?;
        }
    }
    for reconnect_thread in reconnect_threads {
        validate_reconnect(reconnect_thread).await?;
    }
    let duplicate: OnlineCommandReceipt = client
        .post(
            &host,
            &format!("/v1/online/matches/{}/commands", created.match_id),
            &first_request,
        )
        .await?;
    if !duplicate.duplicate
        || duplicate.sequence != first.sequence
        || duplicate.input_sequence != first.input_sequence
    {
        return Err("duplicate command did not return the stored receipt".to_string());
    }
    let mut tampered_duplicate = first_request.clone();
    tampered_duplicate.target_tick = tampered_duplicate.target_tick.saturating_add(1);
    let (status, _) = client
        .post_status(
            &host,
            &format!("/v1/online/matches/{}/commands", created.match_id),
            &tampered_duplicate,
        )
        .await?;
    if status != 409 {
        return Err(format!(
            "tampered duplicate returned HTTP {status}, expected 409"
        ));
    }
    let mut skipped = first_request.clone();
    skipped.command_id = format!("{run_id}-sequence-skip");
    skipped.input_sequence = skipped.input_sequence.map(|value| value.saturating_add(2));
    skipped.expected_match_revision = first.match_revision;
    let (status, _) = client
        .post_status(
            &host,
            &format!("/v1/online/matches/{}/commands", created.match_id),
            &skipped,
        )
        .await?;
    if status != 409 {
        return Err(format!(
            "input sequence skip returned HTTP {status}, expected 409"
        ));
    }
    let after_first = client.snapshot(&guest, &created.match_id).await?;
    let mut theft = skipped;
    theft.player_id = guest.player_id.clone();
    theft.account_id = guest.account_id.clone();
    theft.command_id = format!("{run_id}-control-theft");
    theft.sequence = after_first.view.next_sequence;
    theft.input_sequence = after_first
        .view
        .members
        .iter()
        .find(|member| member.player_id == guest.player_id)
        .map(|member| member.next_input_sequence);
    theft.expected_match_revision = after_first.view.match_revision;
    theft.target_tick = after_first.view.authoritative_tick;
    theft.client_observed_tick = Some(after_first.view.authoritative_tick);
    theft.order.frame = u32::try_from(theft.target_tick)
        .map_err(|_| "control-theft target tick overflow".to_string())?;
    theft.order.subject_actor_ids = host_units.clone();
    let (status, _) = client
        .post_status(
            &guest,
            &format!("/v1/online/matches/{}/commands", created.match_id),
            &theft,
        )
        .await?;
    if status != 403 {
        return Err(format!(
            "control theft returned HTTP {status}, expected 403"
        ));
    }
    let mut old_build = theft;
    old_build.command_id = format!("{run_id}-old-build");
    old_build.build_id = "trnm-online-old-build".to_string();
    let (status, _) = client
        .post_status(
            &guest,
            &format!("/v1/online/matches/{}/commands", created.match_id),
            &old_build,
        )
        .await?;
    if status != 426 {
        return Err(format!("old build returned HTTP {status}, expected 426"));
    }

    let before_restart = client.snapshot(&host, &created.match_id).await?;
    let order_count_before = before_restart.snapshot["order_count"]
        .as_u64()
        .unwrap_or_default();
    let restart_recovery = std::env::var("TRNM_ONLINE_E2E_RESTART_SERVER")
        .map(|value| value != "0")
        .unwrap_or(true);
    if restart_recovery {
        restart_server(&base_url).await?;
    }
    let reconnected = client
        .reconnect(
            &guest,
            &created.match_id,
            0,
            "stale-client-snapshot".to_string(),
        )
        .await?;
    if reconnected.reconnect_count < reconnect_command_race_rounds.saturating_add(1)
        || !reconnected.full_snapshot_required
        || reconnected.replayed_commands.len() != before_restart.view.next_sequence as usize
        || reconnected.replay_truncated
        || reconnected.next_receipt_sequence != before_restart.view.next_sequence
    {
        return Err(
            "authenticated reconnect did not replay the authoritative command gap".to_string(),
        );
    }
    let after_restart = OnlineSnapshotResponse {
        view: reconnected.view,
        snapshot: reconnected.snapshot,
    };
    if after_restart.view.seed_hash != before_restart.view.seed_hash
        || after_restart.view.next_sequence != before_restart.view.next_sequence
        || after_restart.snapshot["order_count"]
            .as_u64()
            .unwrap_or_default()
            != order_count_before
        || after_restart.view.authoritative_tick < before_restart.view.authoritative_tick
    {
        return Err("restart did not preserve authoritative match/command state".to_string());
    }

    let contact = wait_for(
        &client,
        &host,
        &created.match_id,
        phase_timeout(),
        |snapshot| snapshot.snapshot["phase"] == "contact",
    )
    .await?;
    let host_relay_units = controlled(&contact, &host)?;
    let host_relay_units = host_relay_units
        .into_iter()
        .collect::<std::collections::BTreeSet<_>>();
    let host_relay_count = host_relay_units.len();
    if host_relay_count == 0 {
        return Err("relay prepositioning has no living host squad".to_string());
    }
    let host_ready = wait_for(
        &client,
        &host,
        &created.match_id,
        phase_timeout(),
        |snapshot| {
            if snapshot.snapshot["phase"] == "complete" {
                return true;
            }
            let living_count = living_selected_count(&snapshot.snapshot, &host_relay_units);
            let arrived_count = snapshot.snapshot["party"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|unit| unit["hp"].as_i64().unwrap_or_default() > 0)
                .filter(|unit| {
                    unit["unit_id"]
                        .as_str()
                        .is_some_and(|unit_id| host_relay_units.contains(unit_id))
                })
                .filter(|unit| {
                    (unit["position"]["x"].as_i64().unwrap_or_default() - i64::from(approach.x))
                        .abs()
                        + (unit["position"]["y"].as_i64().unwrap_or_default()
                            - i64::from(approach.y))
                        .abs()
                        <= 2
                })
                .count();
            living_count < host_relay_count || arrived_count == host_relay_count
        },
    )
    .await?;
    if host_ready.snapshot["phase"] == "complete"
        || living_selected_count(&host_ready.snapshot, &host_relay_units) < host_relay_count
    {
        return Err(format!(
            "relay prepositioning lost a host unit or reached terminal outcome {}",
            host_ready.snapshot["outcome"]
        ));
    }
    let guest_ready = client.snapshot(&guest, &created.match_id).await?;
    let _ = client
        .submit(
            &guest,
            &created.match_id,
            &guest_ready,
            CommandSpec {
                command_id: format!("{run_id}-guest-ability"),
                kind: RtsOrderKind::Ability,
                subjects: controlled(&guest_ready, &guest)?,
                target: objective,
                queued: false,
            },
        )
        .await?;
    // The mission actor accepts a primary order plus queued co-op subjects.
    // Keep both authority partitions active: the unqueued host order starts
    // the assault and the queued guest order joins it without replacing it.
    let relay_attack = client.snapshot(&host, &created.match_id).await?;
    let host_relay_attack_units = controlled(&relay_attack, &host)?;
    let initial_guest_relay_attack_units = controlled(&relay_attack, &guest)?;
    if host_relay_attack_units.is_empty() && initial_guest_relay_attack_units.is_empty() {
        return Err("relay assault has no living squad".to_string());
    }
    let host_relay_attack_issued = !host_relay_attack_units.is_empty();
    let mut guest_relay_snapshot = relay_attack;
    if host_relay_attack_issued {
        let _ = client
            .submit(
                &host,
                &created.match_id,
                &guest_relay_snapshot,
                CommandSpec {
                    command_id: format!("{run_id}-host-attack-relay"),
                    kind: RtsOrderKind::Attack,
                    subjects: host_relay_attack_units,
                    target: objective,
                    queued: false,
                },
            )
            .await?;
        guest_relay_snapshot = client.snapshot(&guest, &created.match_id).await?;
    }
    let guest_relay_attack_units = controlled(&guest_relay_snapshot, &guest)?;
    if !guest_relay_attack_units.is_empty() {
        let _ = client
            .submit(
                &guest,
                &created.match_id,
                &guest_relay_snapshot,
                CommandSpec {
                    command_id: format!("{run_id}-guest-attack-relay"),
                    kind: RtsOrderKind::Attack,
                    subjects: guest_relay_attack_units,
                    target: objective,
                    queued: host_relay_attack_issued,
                },
            )
            .await?;
    }
    let relay = wait_for(
        &client,
        &host,
        &created.match_id,
        phase_timeout(),
        |snapshot| {
            snapshot.snapshot["phase"] == "complete"
                || (snapshot.snapshot["phase"] == "relay"
                    && snapshot.snapshot["relay_guard_hp"].as_i64().unwrap_or(1) <= 0)
        },
    )
    .await?;
    if relay.snapshot["phase"] == "complete" {
        return Err(format!(
            "relay assault reached terminal outcome {}",
            relay.snapshot["outcome"]
        ));
    }
    let _ = move_largest_squad_to_objective_and_hold(
        &client,
        &host,
        &guest,
        &created.match_id,
        &relay,
        objective,
        &format!("{run_id}-relay-capture"),
    )
    .await?;

    for wave in 1..=2u64 {
        let wave_snapshot = wait_for(
            &client,
            &host,
            &created.match_id,
            phase_timeout(),
            |snapshot| {
                snapshot.snapshot["phase"] == "complete"
                    || snapshot.snapshot["reinforcement_wave"]
                        .as_u64()
                        .unwrap_or_default()
                        >= wave
            },
        )
        .await?;
        if wave_snapshot.snapshot["phase"] == "complete" {
            return Err(format!(
                "wave {wave} did not spawn before terminal outcome {}",
                wave_snapshot.snapshot["outcome"]
            ));
        }
        let host_wave_units = controlled(&wave_snapshot, &host)?;
        let guest_wave_units = controlled(&wave_snapshot, &guest)?;
        let Some((ability_identity, ability_units)) = select_largest_living_squad_prefer_guest(
            &host,
            host_wave_units,
            &guest,
            guest_wave_units,
        ) else {
            return Err(format!("wave {wave} spawned without a living player squad"));
        };
        let ability_selected = ability_units
            .iter()
            .cloned()
            .collect::<std::collections::BTreeSet<_>>();
        if selected_signature_ability_ready(&wave_snapshot.snapshot, &ability_selected) {
            let _ = client
                .submit(
                    ability_identity,
                    &created.match_id,
                    &wave_snapshot,
                    CommandSpec {
                        command_id: format!("{run_id}-wave-{wave}-ability"),
                        kind: RtsOrderKind::Ability,
                        subjects: ability_units,
                        target: objective,
                        queued: false,
                    },
                )
                .await?;
        }
        let wave_snapshot = client.snapshot(&host, &created.match_id).await?;
        let cleared = clear_living_enemies(
            &client,
            &host,
            &guest,
            &created.match_id,
            wave_snapshot,
            objective,
            &format!("{run_id}-wave-{wave}"),
        )
        .await?;
        // Capture progress advances by at most two selected holders per tick.
        // In co-op the surviving holders can be split across members, so
        // always moving the host can leave only one selected unit on the
        // objective and miss the five-minute mission budget by a few ticks.
        // Move and hold with the member that currently owns the largest
        // living squad; the deterministic guest tie-break keeps the relay
        // veteran squad active while still exercising independent authority.
        let _ = move_largest_squad_to_objective_and_hold(
            &client,
            &host,
            &guest,
            &created.match_id,
            &cleared,
            objective,
            &format!("{run_id}-wave-{wave}-capture"),
        )
        .await?;
    }

    // Keep the client-observed terminal skew as diagnostic evidence. It includes
    // the terminal publication barrier plus the final snapshot request's
    // database RTT, so the formal actor-clock gate uses the server's cumulative
    // clock telemetry instead of misclassifying read-surface latency as dropped
    // simulation ticks.
    let terminal = wait_for(
        &client,
        &host,
        &created.match_id,
        completion_timeout(),
        |snapshot| snapshot.snapshot["phase"] == "complete",
    )
    .await?;
    let match_wall_elapsed_ms =
        u64::try_from(match_clock_started.elapsed().as_millis()).unwrap_or(u64::MAX);
    let observed_match_ticks = terminal
        .view
        .authoritative_tick
        .saturating_sub(initial_authoritative_tick);
    let match_tick_drift =
        observed_match_ticks as f64 - match_wall_elapsed_ms as f64 / tick_interval_ms;
    let complete = if terminal.view.phase == OnlineMatchPhase::Complete
        && terminal.view.settlement_state == "settled"
    {
        terminal
    } else {
        wait_for(
            &client,
            &host,
            &created.match_id,
            completion_timeout(),
            |snapshot| {
                snapshot.view.phase == OnlineMatchPhase::Complete
                    && snapshot.view.settlement_state == "settled"
            },
        )
        .await?
    };
    let terminal_duplicate: OnlineCommandReceipt = client
        .post(
            &host,
            &format!("/v1/online/matches/{}/commands", created.match_id),
            &first_request,
        )
        .await?;
    if !terminal_duplicate.duplicate || terminal_duplicate.sequence != first.sequence {
        return Err("terminal exact duplicate did not return the durable receipt".to_string());
    }
    if complete.snapshot["outcome"] != "victory" {
        return Err(format!(
            "authoritative battle completed without victory: {}",
            complete.snapshot["outcome"]
        ));
    }
    for (identity, initial_campaign) in [(&host, &campaign), (&guest, &guest_campaign)] {
        let member = complete
            .view
            .members
            .iter()
            .find(|member| member.player_id == identity.player_id)
            .ok_or_else(|| "completed view lost one member progression".to_string())?;
        if member.experience <= initial_campaign.experience
            || member.inventory_count
                <= initial_campaign
                    .inventory
                    .iter()
                    .map(|stack| u64::from(stack.quantity))
                    .sum::<u64>()
        {
            return Err(format!(
                "online member {} did not receive independent progression/inventory",
                identity.player_id
            ));
        }
    }
    let (command_ack_ms, command_ack_p95_ms, command_ack_max_ms) = client.command_ack_summary();
    let settlement_observation_wall_elapsed_ms =
        u64::try_from(match_clock_started.elapsed().as_millis()).unwrap_or(u64::MAX);
    Ok(json!({
        "status": "passed",
        "run_id": run_id,
        "campaign_id": campaign.campaign_id,
        "match_id": created.match_id,
        "members": complete.view.members,
        "authoritative_tick": complete.view.authoritative_tick,
        "initial_authoritative_tick": initial_authoritative_tick,
        "observed_match_ticks": observed_match_ticks,
        "match_wall_elapsed_ms": match_wall_elapsed_ms,
        "tick_interval_ms": tick_interval_ms,
        "match_tick_drift": match_tick_drift,
        "match_tick_drift_scope": "client_wall_to_terminal_read_surface_includes_publication_barrier_and_snapshot_database_latency_not_formal_actor_clock_gate",
        "settlement_observation_wall_elapsed_ms": settlement_observation_wall_elapsed_ms,
        "next_sequence": complete.view.next_sequence,
        "seed_hash": complete.view.seed_hash,
        "snapshot_hash": complete.view.snapshot_hash,
        "result_hash": complete.view.result_hash,
        "settlement_state": complete.view.settlement_state,
        "start_lost_response_retry_verified": start_lost_response_retry_verified,
        "duplicate_command_exactly_once": true,
        "terminal_duplicate_command_exactly_once": true,
        "tampered_duplicate_rejected": true,
        "sequence_regression_rejected": true,
        "concurrent_player_input_sequences": true,
        "concurrent_server_total_orders": total_orders,
        "websocket_full_delta_verified": true,
        "websocket_authoritative_effect_ms": websocket_authoritative_effect_ms,
        "websocket_authoritative_effect_sample_scope": "command_submit_start_to_hash_verified_stream_total_order",
        "websocket_authoritative_effect_samples_ms": websocket_authoritative_effect_samples_ms,
        "websocket_authoritative_effect_p95_ms": websocket_authoritative_effect_p95_ms,
        "websocket_authoritative_effect_max_ms": websocket_authoritative_effect_max_ms,
        "reconnect_command_race_rounds": reconnect_command_race_rounds,
        "reconnect_command_race_pipeline_depth": reconnect_command_race_pipeline_depth,
        "cross_member_control_rejected": true,
        "old_build_rejected": true,
        "restart_recovery": restart_recovery,
        "authenticated_reconnect": true,
        "replayed_commands": before_restart.view.next_sequence,
        "guest_progression": true,
        "independent_cloud_campaigns": [campaign.campaign_id, guest_campaign.campaign_id],
        "command_ack_samples": command_ack_ms.len(),
        "command_ack_ms": command_ack_ms,
        "command_ack_p95_ms": command_ack_p95_ms,
        "command_ack_max_ms": command_ack_max_ms,
    }))
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    match run().await {
        Ok(report) => println!("{}", serde_json::to_string_pretty(&report).unwrap()),
        Err(error) => {
            eprintln!("TRNM Online Authority E2E failed: {error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity(player_id: &str) -> Identity {
        Identity {
            player_id: player_id.to_string(),
            account_id: format!("{player_id}-account"),
            session: format!("{player_id}-session"),
        }
    }

    #[test]
    fn largest_living_squad_uses_member_count_with_guest_tie_break() {
        let host = identity("host");
        let guest = identity("guest");
        let (selected, units) = select_largest_living_squad_prefer_guest(
            &host,
            vec!["host:hero".to_string()],
            &guest,
            vec!["guest:hero".to_string(), "guest:aya".to_string()],
        )
        .expect("guest has living capture units");
        assert_eq!(selected.player_id, "guest");
        assert_eq!(units.len(), 2);

        let (selected, units) = select_largest_living_squad_prefer_guest(
            &host,
            vec!["host:hero".to_string()],
            &guest,
            vec!["guest:hero".to_string()],
        )
        .expect("both members have living capture units");
        assert_eq!(selected.player_id, "guest");
        assert_eq!(units, ["guest:hero"]);

        assert!(
            select_largest_living_squad_prefer_guest(&host, Vec::new(), &guest, Vec::new())
                .is_none()
        );
    }

    #[test]
    fn combat_target_selects_only_a_living_enemy_tile() {
        let snapshot = json!({
            "visible_tiles": [
                {"x":7,"y":9},
                {"x":8,"y":10}
            ],
            "enemies": [
                {"unit_id":"contact_dead", "hp":0, "position":{"x":1,"y":2}},
                {"unit_id":"contact_target", "hp":43, "position":{"x":7,"y":9}},
                {"unit_id":"next", "hp":487, "position":{"x":8,"y":10}}
            ],
            "party": [
                {"unit_id":"host:hero", "hp":10, "energy":18,
                 "ability_cooldown_ticks":0, "skill_ids":["iron_guard"]},
                {"unit_id":"host:aya", "hp":0, "energy":140,
                 "ability_cooldown_ticks":0, "skill_ids":["wind_step"]},
                {"unit_id":"guest:hero", "hp":20, "energy":17,
                 "ability_cooldown_ticks":0, "skill_ids":["iron_guard"]}
            ]
        });
        let (enemy_id, target) =
            first_living_enemy_target(&snapshot).expect("snapshot has one living target");
        assert_eq!(enemy_id, "contact_target");
        assert_eq!(target, RtsTile::new(7, 9));
        assert!(enemy_is_alive(&snapshot, "contact_target"));
        assert!(!enemy_is_alive(&snapshot, "contact_dead"));
        let selected = ["host:hero".to_string(), "host:aya".to_string()]
            .into_iter()
            .collect();
        assert_eq!(living_selected_count(&snapshot, &selected), 1);
        assert!(selected_signature_ability_ready(&snapshot, &selected));
        let guest_selected = ["guest:hero".to_string()].into_iter().collect();
        assert!(!selected_signature_ability_ready(
            &snapshot,
            &guest_selected
        ));
    }

    #[test]
    fn retries_only_explicitly_recoverable_service_unavailability() {
        assert!(publication_transition_is_recoverable(
            503,
            &json!({"error":"publication barrier","recoverable":true})
        ));
        assert!(!publication_transition_is_recoverable(
            503,
            &json!({"error":"publication barrier","recoverable":false})
        ));
        assert!(!publication_transition_is_recoverable(
            500,
            &json!({"error":"internal","recoverable":true})
        ));
        assert!(!publication_transition_is_recoverable(
            200,
            &json!({"recoverable":true})
        ));
    }
}

#[cfg(test)]
#[path = "online_e2e/async_http_tests.rs"]
mod async_http_tests;
