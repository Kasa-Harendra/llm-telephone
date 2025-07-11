# Multi-Modal Routing Gateway — End-to-End System Design

*Target scenario: a single production service that can ingest **text, code-gen, vision, and audio** prompts and route each request to “the best” third-party model (LLM/LVM) given a latency ↔ cost ↔ quality objective while maintaining **≤ 60 ms median router overhead** and **thousands of concurrent QPS**.*

---

## 0 . Requirements & Invariants

| Axis | Hard Constraint | Rationale |
|------|-----------------|-----------|
| **Latency** | Router adds ≤ 60 ms p50, ≤ 120 ms p95 | Must hide in model *total* SLA (< 500 ms for interactive UX) |
| **Throughput** | 5 k QPS steady-state; burst 10× | Mirrors hot launches (e.g., overnight agent farms) |
| **Modalities** | Text, code-gen, vision, audio (speech⇌text) | Matches Not Diamond roadmap Q3 2025 |
| **Routing Objective** | User-tunable *λ* (**cost ↔ latency ↔ quality**) | Mimics Not Diamond dial |
| **Freshness** | Use eval metrics ≤ 15 min old | Third-party models drift quickly |
| **Availability** | 99.95 % end-to-end | Enterprise expectation |
| **Regulatory** | SOC-2 & optional VPC / data-plane isolation | Enterprise sales blocker |

---

## 1 . Evolution From Zero → Production

### 1.1 “Whiteboard v0” — Single-Region Pilot

*Pros:* fastest learning loop, no global complexity.  
*Cons:* cold-start latency for non-US users; single-point failure.

### 1.2 “MVP v1” — Multi-Region Active/Passive + CDN Edge Auth

*Add:* secondary region hot-standby, CloudFront/Cloudflare edge for auth+rate-limit.

### 1.3 “Prod v2” — *Global Active/Active*, Online ML Router, QoS Dial

*Add:*  
* Global PoPs with **Anycast** + request-coalescing.  
* Learned router (GBDT + NN) fed by **Real-time Feature Store** (RTFS).  
* **Speculative-Execution Executor** (SEE): parallel fire-and-race within latency budget.  
* **Warm Response Cache** keyed by `(promptHash, top-k-models)`.

### 1.4 “Enterprise v3” — BYO-VPC Proxy & Private Model Mesh

*Add:*  
* Helm-installable proxy that re-uses the same Router via gRPC.  
* Side-channel to bring customer-hosted models into the mesh via **Reverse-SDS** (Service Discovery Service).

---

## 2 . High-Level Architecture (Prod v2)

```mermaid
flowchart TB
  subgraph Edge POPs (Anycast)
    A1(API Gateway + Auth) -->|JSON/Proto| N[Normalizer & Modal Detector]
  end
  N --> FSTRT(Fwd to<br>Router Shard)
  subgraph Region-Local Zone
    FRT(Router Service) -->|features| FS[(Real-Time<br>Feature Store)]
    FRT -->|candidate set| SEE(Speculative Exec Mgr)
    SEE -->|async| CONN[Model Connectors Pool]
    CONN -->|responses| ARB{Arbitrator<br/>(QoS dial)}
    ARB --> CACHE[Warm Resp Cache (Redis Co-Located)]
    ARB --> MON[Metrics/Spans]
    CACHE --hit--> RESP[Return Best Answer]
    ARB --miss--> RESP
  end
  FS <-- INGESTION[Streaming<br>Metrics Ingest]
  MON --> OBS[Observability Stack<br>(OTel ➜ ClickHouse/Grafana)]
  RESP --> A1
````

*Legend*: solid line = sync; dashed = async.

---

## 3 . Component Deep-Dive

### 3.1 Edge API Gateway

* **Anycast IP** routes to nearest POP (≤ 20 ms RTT for 95 % world).
* **JWT / HMAC Key** auth & **rate-limit** in-edge (blocks bad traffic earlier).
* **Protocol**: HTTP/2 + gRPC; `multipart/form-data` for large images/audio.

### 3.2 Normalizer & Modal Detector

* Detects modality via MIME or fast heuristic (magic bytes / FFT for audio).
* Outputs canonical JSON:

```json
{
  "trace_id": "...",
  "modality": "vision",
  "prompt": "... base64 if binary ...",
  "qos": { "latency_weight": 0.3, "cost_weight": 0.2, "quality_weight": 0.5 },
  "user_tags": ["prod", "team-x"]
}
```

### 3.3 Router Service (stateless shards)

* **Feature Vector** = concat(user-provided metadata, embedding of prompt, past hit ratios, cost curves).
* **Model**: GBDT (CatBoost) for *fast* candidate ranking → pass top-k (≤ 3) to small **MLP** re-ranker trained on pairwise win-rate.
* Online inference ≤ 5 ms in-memory.
* **Cold-start**: heuristics (e.g., prefer GPT-4o for code if budget OK).

### 3.4 Real-Time Feature Store (RTFS)

* Write-Optimized **Kafka→ClickHouse→Redis** cascade.

  * Kafka topic `model_metrics` (counters every 10 s).
  * Materialized-view in ClickHouse for long-range analytics.
  * Latest slice (< 1 min) KV-mirrored to Redis-Cluster for sub-millisecond read in router.

### 3.5 Speculative Execution Mgr (SEE)

* Launches **parallel** calls to top-k candidates BUT with staggered delays (`Δ = p50_latency(candidate_i−1)`).
* Cancels slower stragglers when a high-confidence answer returns.
* **Budget Enforcement**: if cumulative wait exceeds user requested `latency_budget`, abort remaining.

### 3.6 Model Connectors Pool

* Each connector = *Circuit-Breaker + Retry-Budget + QoS tags*.
* Pool is **modal-aware** (e.g., GPU vs CPU endpoints).
* **Throttling** by cost cap & tenant budget.

### 3.7 Arbitrator

* Chooses winner based on **QoS Scalar**:

  $$
  score = λ_Q \cdot \text{quality\_pred} - λ_L \cdot \text{latency} - λ_C \cdot \text{cost}
  $$
* Performs *post-hoc* guardrail filters (toxicity, PII).

### 3.8 Warm Response Cache

* Redis *sharded by prompt hash*;
* **TTL** = 5 min (high duplicate hit for agent chains).
* **Co-located** with Router to avoid cross-AZ hop.

### 3.9 Observability & SLOs

| Layer            | Metric            | SLO/Alert             |
| ---------------- | ----------------- | --------------------- |
| **Gateway**      | Auth failure rate | < 0.1 %               |
| **Router**       | p50 overhead      | ≤ 60 ms               |
| **End-to-End**   | p99 latency       | ≤ 800 ms              |
| **Availability** | 5xx ratio         | < 0.05 % 5-min window |
| **Cost**         | \$/1 k tokens     | ±15 % target          |

Stack: **OpenTelemetry → OTLP → ClickHouse** (fast tail), **Grafana + Alertmanager**, **Honeycomb** for trace search.

---

## 4 . Data Flows

1. **Request** lands at nearest POP (Anycast) → auth, quota verified.
2. **Normalizer** tags modality & pushes to **Router** (same region).
3. **Router** pulls latest model features (Redis) → ranks → top-k.
4. **SEE** fires speculative calls through **Connectors**.
5. **Connectors** stream partials (where supported, e.g., SSE).
6. **Arbitrator** picks best, pushes to **Cache**, emits metrics to **Kafka**.
7. **Response** streamed to client; tail-latency stragglers cancelled.
8. **Async ingest** updates RTFS (win-rate, cost) — closes feedback loop.

---

## 5 . Consistency & Freshness

* **Model Metrics**: Slack 10-15 s acceptable; Redis update by Kafka → Materialized-View → Redis replicator.
* **Config Pushes** (new model, cost change): via **Envoy xDS** stream, propagated < 5 s.
* Strong read-after-write on prompt cache not required; use *probabilistic invalidation* if prompt >256 KB.

---

## 6 . Scaling & Cost Control

| Plane          | Strategy                                                                     |
| -------------- | ---------------------------------------------------------------------------- |
| **Read Path**  | Stateless shards behind Layer-4 Anycast; Kubernetes HPA on CPU + RPS.        |
| **Write Path** | Kafka partitioning by `model_id` (keeps ordering for metrics).               |
| **Storage**    | ClickHouse cluster w/ tiered S3 for cold.                                    |
| **Connectors** | **gRPC-mux** keep-alives;  ➜ reduces TLS handshakes.                         |
| **GPU Usage**  | Prefer provider-side GPUs; *if* self-hosted, use **KServe + Nvidia MIG**.    |
| **Cost Gate**  | Per-tenant daily spend cap: router rewrites QoS to cheaper tier if breached. |

---

## 7 . API Contract (abridged)

```http
POST /v1/route
Authorization: Bearer <token>
Content-Type: application/json

{
  "modality": "text",          // optional; auto-detect if omitted
  "input": "How do I implement Dijkstra in Go?",
  "qos": { "latency_weight":0.2, "cost_weight":0.3, "quality_weight":0.5 },
  "latency_budget_ms": 800,
  "stream": true
}
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Model-Id: gpt-4o-mini
X-Routing-Latency: 41

{ "completion": "...", "metadata": { "tokens": 211, "cost": 0.004 } }
```

*Streaming* uses SSE/gRPC-stream with `delta` chunks.

---

## 8 . Design Choices vs Alternatives

| Decision           | Picked                 | Rejected         | Why                                                                     |
| ------------------ | ---------------------- | ---------------- | ----------------------------------------------------------------------- |
| **Online Ranking** | GBDT→MLP               | Vector search    | Dense retrieval not great for *structured* model metadata; GBDT cheaper |
| **Speculation**    | Staggered‐parallel     | Full parallel    | Saves 30–40 % cost w/ negligible p50 delta                              |
| **Feature Store**  | Kafka→ClickHouse→Redis | DynamoDB Streams | ClickHouse gives *OLAP* + *low-latency OLTP* via materialized views     |
| **Cache**          | Prompt-hash Redis      | CDN edge         | Avoids privacy leaks of raw prompts                                     |
| **Observability**  | OTel + ClickHouse      | Elastic          | ClickHouse 3–4× cheaper at >1 M spans/s                                 |

---

## 9 . Common Follow-Up Q\&A

<details>
<summary>🔧 How would you add *New Model X* with unknown latency?</summary>

1. Register in **Model Registry** with cost/regions.
2. Auto-discover latency via synthetic probes stored in RTFS.
3. Router exploration: 2 % traffic → Thompson-Sampling until confidence > 95 %.
4. Promote if QoS‐adjusted score beats incumbent by ≥ Δ.

</details>

<details>
<summary>🌋 What if a whole provider region goes down?</summary>

* Envoy health-checks trip → Connector breaker opens (30 s).
* Router excludes unhealthy endpoints; SEE skips them.
* Anycast fails over to nearest healthy POP (< 200 ms penalty).
* SLO alert triggers — on-call can disable provider via config plane.

</details>

<details>
<summary>💸 How do you keep cost predictable?</summary>

* **Token-budget** policy per tenant; Router rewrites QoS dial dynamically.
* Spot price feed for GPU hosts updates cost feature; higher cost lowers score.
* Daily cost anomaly detector (Z-score on \$/day) auto-pages FinOps.

</details>

<details>
<summary>🧪 Testing strategy before prod?</summary>

1. **Canary** release: 1 % traffic shadowed.
2. **Replay Harness**: last 24 h requests → offline evaluation (latency + judge LLM score).
3. **Chaos Monkey**: randomly delay model endpoints 500 ms; verify SEE cancels gracefully.
4. Perf test with **Locust** simulating 20 k RPS across 8 regions.

</details>

---

## 10 . Conclusion

This architecture keeps **median router overhead \~40 ms**, supports agent-scale parallelism, offers dial-a-trade-off QoS, and cleanly extends to private/VPC deployments—matching Not Diamond’s multi-model mission while staying cost-aware and observability-first.
