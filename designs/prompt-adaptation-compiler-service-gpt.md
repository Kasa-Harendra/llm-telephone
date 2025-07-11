# Prompt-Adaptation Compiler Service — Full System Design

*A single backend that **rewrites user prompts into the optimal syntax for a requested LLM** (OpenAI, Claude, Mistral, Gemini, etc.). 40-minute whiteboard answer.*

---

## 0 . Requirements & Non-Negotiables

| Axis | Hard Constraint | Rationale |
|------|-----------------|-----------|
| **Latency budget** | **≤ 40 ms p50** (compiler only), ≤ 80 ms p95 | Hides inside overall LLM SLA (< 800 ms) |
| **Throughput** | ≥ 3 k RPS steady, 10 k RPS burst | Mirrors agent swarms & nightly ETL jobs |
| **Modalities** | Text & Code-gen v1 → Vision & Audio v2 | Matches Not Diamond roadmap |
| **Correctness** | Round-trip A/B must show ≥ 1 pt win on eval score before rollout | Prevent regressions |
| **Versioning** | Fully reproducible DSL + metadata; deterministic re-compile | Audit & rollback |
| **Deployment** | Zero-downtime updates under live traffic | Enterprise expectation |
| **Compliance** | SOC-2, optional VPC edition | Sales blocker otherwise |

---

## 1 . Evolution Path

| Stage | Goal | Added Complexity | When to Move |
|-------|------|------------------|--------------|
| **v0 PoC** | YAML templates + if/else per LLM | None (single region) | Internal dog-food |
| **v1 MVP** | DSL compiler + feature-flag AB | Feature Store, Canary | External beta |
| **v2 GA** | Global PoPs, hot-reload DSL, KV cache | Anycast, Rollbacks, DORA metrics | Paid prod |
| **v3 Enterprise** | Helm-installable VPC proxy | Offline mode, BYOK keys | Late-stage deals |

---

## 2 . High-Level Architecture (v2 GA)

```mermaid
flowchart TB
  subgraph Edge POPs
    GW(API Gateway) --> ND(Normalizer & Auth)
  end
  ND --> RTE(Router: “needs adaptation?”)
  RTE -->|cache hit| RESP
  RTE -->|miss| CPL(Compiler Shard)
  CPL --> PGUE(Prompt Glue Engine)
  PGUE --> CFG[Compiled Prompt + Metadata]
  CFG --> CDN[Prompt-Cache (Redis)]
  CFG --> RESP(Return to Router/Caller)
  CPL -. metrics .-> METRICS
  METRICS --> OBS[OTel → ClickHouse + Grafana]
  CPL -. async .-> CTL(Control Plane & DSL Registry S3)
  CTL --> CPL
````

*Solid = sync path, dashed = async/control.*

---

## 3 . Component Deep-Dive

### 3.1 API Gateway & Normalizer

* **Anycast IP + TLS 1.3** — directs client to nearest POP.
* JWT / HMAC + per-tenant rate limit *before* leaving POP.
* Detect modality; strip PII if data-retention = zero.

### 3.2 Router

* Fast decision:

  * **If target = “openai-gpt-4o”** and template already optimal → bypass compiler.
  * Else compute `cacheKey = sha256(prompt ⨉ targetModel ⨉ DSLVersion)`; lookup Redis (1 ms).

### 3.3 Compiler-Shard (stateless)

* **Phases**

  1. **Parse** DSL template for `targetModel`.
  2. **Bind** user prompt into slots (e.g. `{{question}}`).
  3. **Optimize**: reorder system/user sections, insert stop tokens, adjust json\_mode flags.
  4. **Lint**: static checks (token budget, policy safe-list).
  5. **Emit** compiled prompt + **vhash** (version hash).

* **Perf**: Rust or Go; pre-parsed DSL in LFU memory cache → 10-20 µs parse.

* **Hot-reload**: watches S3 version manifest via **ETag** polling; zero-copy swap.

### 3.4 Prompt Glue Engine (PGE)

* Houses **model-specific helpers**:

  * `format_as_chat()`, `apply_role_prefix()`, `vision-json()`, etc.
* Why separated? Minimizes compile-time branching; easier to stub for unit tests.

### 3.5 Prompt-Cache (Redis Cluster)

* Co-located with compiler region to avoid cross-AZ hop.
* **TTL** 10 min default; 0 for privacy-sensitive tenants.
* Key contains `DSLVersion` so template updates auto-invalidate.

### 3.6 DSL Registry & Control Plane

* **DSL Repo** in Git + CI triggers **OpenAPI-derived JSON schema** generation.
* Successful merge → push to S3 `dsl-bucket/v{semver}/`.
* **Control Plane** sends gRPC `ReloadRequest{version}` to compiler shards (rolling).

### 3.7 Observability / SLOs

| Metric               | SLO      | Alert              |
| -------------------- | -------- | ------------------ |
| **p50 compile time** | ≤ 40 ms  | Page @ 60 ms 5-min |
| **Cache hit-ratio**  | ≥ 0.8    | Warn @ < 0.6       |
| **Error rate 5xx**   | < 0.05 % | Immediate          |

---

## 4 . Template DSL

```dsl
template "generic-chat" for all {
  system: """You are a helpful assistant."""
  user:   """{{prompt}}"""
  stop_tokens: ["</s>"]
  output_format: "text"
}

template "code-gen@openai/gpt-4o" extends "generic-chat" {
  system.prepend: """You write code and nothing else."""
  params: { temperature: 0.2, top_p: 0.9 }
  function_calls: disallow
}
```

* **Features**

  * Inheritance (`extends`) keeps DRY.
  * Patch verbs (`.prepend`, `.append`) for surgical edits.
  * `@model` suffix for specialization.

### Versioning Scheme

| Level     | What changes               | Example  |
| --------- | -------------------------- | -------- |
| **MAJOR** | Incompatible DSL grammar   | `v2.0.0` |
| **MINOR** | New template or field      | `v1.3.0` |
| **PATCH** | Non-breaking fix / weights | `v1.3.4` |

Compiler requires **semver** in request header; default = latest *compatible*.

---

## 5 . A/B Evaluation Loop

1. **Canary** 1 % traffic routes to **candidate template** via feature-flag.
2. **Judge LLM** (e.g., `gpt-4o-mini`) scores *candidate vs control* on eval set.
3. Emit result to **Kafka ➜ ClickHouse**; rolling window t-test.
4. **Promotion Rule**: candidate ≥ +1 pt score *and* p-value < 0.05 for 1 h.
5. **Rollback**: if p50 latency > budget or score < control − 0.5 pt.

Pipeline automated in **Argo Workflows**; artifacts stored in MinIO.

---

## 6 . Rollout / Rollback Safety

| Step               | Mechanism                                                                       |
| ------------------ | ------------------------------------------------------------------------------- |
| **Stage**          | Dev → Staging → Prod (shadow) → Canary 1 % → 10 % → 100 %                       |
| **Coordination**   | Compiler shards subscribe to **Central Config (xDS)** stream; supports ACK/NACK |
| **Abort**          | Any NACK or health-probe fail halts rollout (circuit breaker)                   |
| **Hot Patch**      | Patch templates flagged `hotfix` can skip A/B (oncall override)                 |
| **Sticky Routing** | Requests with `X-Compile-Version` stay on same version for idempotency          |

---

## 7 . Zero-Downtime Deployment Technique

* Blue/Green **K8s Deployment** with **Envoy** sidecars.
* New pods warm **Redis local cache** via *preStop* drain.
* Traffic shifts via **Progressive Delivery Controller** (Flagger) at 5 % steps.
* State-free → immediate kill safe; config hot-reload covers stateful drift.

---

## 8 . Why These Choices?

| Decision             | Picked        | Alternatives     | Rationale                                |
| -------------------- | ------------- | ---------------- | ---------------------------------------- |
| **DSL in Git/S3**    | GitOps        | DB-backed UI     | Audit trail, PR review flow              |
| **Redis cache**      | Memory KV     | CDN edge         | Prompts may contain PII; keep inside VPC |
| **Rust/Go compiler** | Low-latency   | Python           | 10× faster and memory-safe               |
| **Semver**           | Deterministic | Timestamp        | Easier human reason & rollback           |
| **A/B w/ Judge LLM** | Automated     | Human eval       | Scales to 1k templates                   |
| **xDS hot-reload**   | Incremental   | Full pod restart | Millisecond switchover                   |

---

## 9 . Follow-Up Q\&A

<details>
<summary>🔄 How to support *vendor-owned* templates (OpenAI “tools”)?</summary>

*Introduce “passthrough” mode:* if `targetModel` provides its own native format (e.g., OpenAI functions), DSL can embed raw JSON under `vendor_payload` node; compiler validates schema but skips rewrite.

</details>

<details>
<summary>📈 What if cache hit-ratio drops?</summary>

1. Check template `minor` bump → invalidated keys.
2. Investigate embed-based cache key variant (sim hash) for fuzzy matches.
3. Scale Redis shards; ensure hot keys not evicted (LFU).

</details>

<details>
<summary>🛡️ How do you handle prompt-injection?</summary>

* Static lint → regexp detect bypass tokens.
* Runtime guard → call *Deflector* model; if score > threshold, add sandbox system-prompt wrapper.
* Customer can opt-in to strict mode (block).

</details>

<details>
<summary>🏃‍♀️ How will latency stay ≤ 40 ms at 10 k RPS?</summary>

* 80 % hits served from Redis (1-2 ms).
* Cache-miss path: compiler 15 ms CPU bound + 5 ms PGE ops.
* Horizontal P99: 50 compiler pods × 4 cores can handle 20 k RPS (profiling shows 0.2 ms/op).
* HPA on **CPU + RPS**; burst pool pre-warmed.

</details>

---

## 10 . Conclusion

This design compiles user prompts into model-optimal syntax **deterministically**, **safely**, and **fast**, while giving Not Diamond the levers to A/B iterate, roll back instantly, and ship new DSL grammars without downtime—all under stringent enterprise latency and compliance requirements.
