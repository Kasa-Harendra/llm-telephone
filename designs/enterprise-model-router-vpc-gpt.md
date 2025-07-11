# Enterprise Model Router with VPC Support: System Design

*A **self-hostable** routing layer that enterprises install **inside their own VPC** yet still benefit from a centrally trained decision engine. The router chooses the best LLM per request while preserving **zero-data-retention** guarantees and gracefully degrading across provider limits & outages. Supports both **single-tenant** and **multi-tenant** deployment topologies.*

---

## P1: Problem Statement

1. **Local In-VPC Data Plane**
   * Terminates client traffic, tokenizes prompt locally, proxies to external LLMs.
2. **Central Intelligence Plane**
   * Maintains global model stats & ML weights **without raw prompt data**.
3. **Privacy Bridge**
   * Ships **minimal, redacted telemetry** (hashes + token counts) from VPC → SaaS.
4. **Resilience Layer**
   * Circuit breakers, back-pressure, provider quota coordination.
5. **Multi-Tenant Controls**
   * Namespace isolation for rate-limit, spend, and logs.

---

## P4: Detailed Architecture

```mermaid
flowchart TB
  subgraph Customer VPC
    A[Client Apps] -->|mTLS| GW[Envoy Gateway]
    GW --> NORM[Local Normalizer<br/>modality detect]
    NORM --> TOK[Tokenizer &<br/>Cost Estimator]
    TOK --> RT[Routing Core]
    RT --> CB{Circuit Breaker}
    CB --> |OK| LLM1[Provider A]
    CB --> |Alt| LLM2[Provider B]
    RT --> REDIS[Redis Cache]
    TOK -. anonymized stats .-> AGENT[Telemetry Agent]
  end

  AGENT -- HTTPS --> CTL[Central SaaS<br/>Routing Brain]
  CTL -- Policy Snapshot --> RT
  CTL --> DASH[Usage & Health Dashboard]
````

*Solid = data plane; dashed = privacy-safe control sync.*

---

## 3  Data- plane Details (Inside VPC)

| Component                 | Function                                                                        | Notes                        |
| ------------------------- | ------------------------------------------------------------------------------- | ---------------------------- |
| **Envoy Gateway**         | mTLS auth (API key or OIDC) + rate-limit per `tenant/team`.                     | Hot-reload config via xDS.   |
| **Normalizer**            | Detects modality, extracts metadata (tokens, mime, language).                   | No prompt stored.            |
| **Tokenizer / Estimator** | Counts tokens per target model to predict cost locally.                         | WASM compiled; sub-1 ms.     |
| **Routing Core**          | Implements `score = w₁·lat + w₂·cost + w₃·quality` using *local* feature table. | Pure Go binary, stateless.   |
| **Redis Cache**           | Prompt-hash ⇒ last chosen model (5 min TTL) to skip recompute.                  | In-memory, encrypted volume. |
| **Circuit Breaker**       | Fast failover on 429/5xx; consults provider quotas from CTL.                    | Exponential back-off.        |
| **Telemetry Agent**       | Hash(prompt) + numeric features → uploads every 60 s.                           | No raw text leaves VPC.      |

---

## 4  Central SaaS (“Routing Brain”)

* Multi-tenant ClickHouse stores **aggregated** metrics: latency, success %, \$/tok.
* Online **GBDT → Light MLP** re-trains hourly.
* Outputs **Policy Snapshot** JSON:

```json
{
  "models": {
    "gemini-2-flash": { "cost":0.0005, "p50":400, "quality":0.92 },
    "...": {}
  },
  "quotas": { "openai": { "window_s":60,"limit":90000 } }
}
```

*Signed and versioned* (HMAC); VPC routers pull via long-poll gRPC.

---

## 5  Zero-Data-Retention & Privacy

| Technique                       | Description                                                              |
| ------------------------------- | ------------------------------------------------------------------------ |
| **Prompt Hashing**              | SHA-256 over first 256 B + salt; used for cache key only.                |
| **Feature Extraction**          | Length, language code, domain tag; no content text.                      |
| **Edge Encryption**             | KMS CMK for Redis + local disk; no S3 uploads.                           |
| **Telemetry Redaction**         | Only numerical stats & opaque hash IDs uploaded.                         |
| **Customer-controlled Opt-out** | Set `telemetry=false`; router then uses **public baseline** model stats. |

---

## 6  Rate-Limit & Failure Handling

* **Sliding-Window** counters in Envoy.
* If provider returns *RateLimit-Reset* header, Circuit Breaker records `resume_at`.
* **Token-Budget Guardrail** per tenant: estimator predicts cost; hard-fail if > budget.
* **Fallback Order** pre-computed in snapshot; if all down → configurable “grace mode” (slow default model).

---

## 7  Scaling from Single → Multi-Tenant

| Layer          | Single-Tenant          | Multi-Tenant                                    |
| -------------- | ---------------------- | ----------------------------------------------- |
| **Namespace**  | One Redis DB           | Key prefix `tenant_id:`                         |
| **Auth**       | Single API key         | OIDC group claim maps to tenant                 |
| **Quotas**     | Global                 | Per-tenant & per-team window                    |
| **Deployment** | k8s namespace “router” | k8s `RouterSet` CRD per tenant (Helm sub-chart) |
| **Logs**       | Local Loki stack       | Loki streams labeled by tenant; RBAC viewer     |

---

## 8  Batch vs. Real-Time Sync

| Path               | Frequency                        | Payload               | Transport |
| ------------------ | -------------------------------- | --------------------- | --------- |
| **Policy Pull**    | long-poll, delta pushed in <10 s | gzipped JSON (\~5 KB) | gRPC      |
| **Telemetry Push** | every 60 s or 10 k rows          | parquet (<200 KB)     | HTTPS PUT |
| **Quota Update**   | instant via control stream       | protobuf              | xDS       |

---

## 9  Deployment & Upgrade Flow

1. Customer installs **Helm chart** `notdiamond-router-<semver>.tgz`.
2. CI pulls OCI image digest signed (cosign).
3. Chart includes **readiness probe** (router + envoy).
4. Blue/Green: Helm sets `router-v2` services, warm-ups Redis; flips Nimbus label.
5. Rollback = `helm rollback`.

---

## 10  Key Trade-Offs

| Decision                        | Alternatives         | Justification                                                     |
| ------------------------------- | -------------------- | ----------------------------------------------------------------- |
| **Central ML, Local Inference** | Fully on-prem ML     | Avoids leaking data but still benefits from fleet-wide learning.  |
| **Redis Cache**                 | CDN Edge             | Can't leak prompts externally; Redis in-VPC is fastest.           |
| **Hash + Features Telemetry**   | No telemetry         | Strikes compliance balance; many customers accept hashed stats.   |
| **Go Router**                   | Rust                 | Go + Envoy filters already battle-tested, lower compile friction. |
| **Long-poll Pull**              | SaaS pushes webhooks | Many VPCs block inbound; pull fits firewalls.                     |

---

## 11  Anticipated Q\&A

<details>
<summary>🔄 *What if VPC has no outbound internet?*</summary>
Provide *offline mode*: vendor IPs whitelisted; control snapshot delivered as signed file over SFTP by customer ops daily.
</details>

<details>
<summary>🛡️ *How to verify central SaaS never stores PII?*</summary>
Open-source telemetry schema & on-prem hashing code; customer can audit that only numeric + hash columns exist. SOC-2 Type II attestation covers pipeline.
</details>

<details>
<summary>📈 *How does router keep ≤100 ms overhead?*</summary>
* 90 % cache hits (<2 ms).  
* Inference scoring: all provider features pre-loaded in memory (O(1)).  
* Tokenizer WASM 20 µs.  
* Total extra CTL/Redis hop ~3–5 ms.
</details>

---

## 12  Conclusion

The proposed hybrid architecture guarantees enterprise privacy by **keeping all raw prompts inside the customer VPC**, yet leverages **centralized fleet learning** through hash-based telemetry. It scales seamlessly from a single tenant PoC to large multi-tenant SaaS customers, while meeting 99.9 % availability, zero-data-retention, and rapid failover requirements.
