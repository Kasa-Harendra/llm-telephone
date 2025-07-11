*A **self-hostable, drop-in proxy** that enterprises run **inside their own VPC** to reach external LLM vendors while satisfying data-residency, SOC-2, rate-limit, and usage-based billing requirements—delivered as a Helm chart but operated by the customer’s infra team.*

---

## Reference Architecture

```mermaid
flowchart TB
  subgraph "Customer VPC (multi-AZ)"
    LB[AWS NLB and TLS termination]
    LB --> ENVOY[Envoy Gateway<br/>(mTLS ↔ clients)]
    ENVOY --> AUTHZ[AuthN / AuthZ<br/>(OPA + JWKS)]
    AUTHZ --> RL[Rate-Limit Service Redis and Envoy filters]
    RL --> TOK[Tokenizer / Meter]
    TOK --> ROUTER[LLM Router policy and routing]
    ROUTER --> EGR[eBPF NAT egress subnet]
  end

  EGR -- TLS --> VENDOR[External LLM Providers]

  subgraph "Metering Pipeline"
    TOK -- gRPC --> AGG[Usage Aggregator]
    AGG --> S3[S3 Staging Parquet]
    S3 --> SNS((SNS))
    SNS --> SaaS[Not Diamond Billing API]
  end

  ENVOY -. metrics .-> PROM[Prometheus and Grafana]

  classDef comp fill:#f9f,stroke:#333,stroke-width:1px;
````

*Solid lines = request path; dashed = metrics.*

---

## Traffic and Encryption Paths

1. **Client → LB**

   * mTLS recommended; TLS 1.3 at minimum.
2. **LB → Envoy Gateway**

   * Internal TLS; ALPN h2 for gRPC.
3. **Envoy → External Vendor**

   * Outbound via dedicated **egress subnet** with VPC endpoints or NAT; TLS terminated at vendor.
4. **Payload Encryption at Rest**

   * Tokenizer spills *only* token counts & SHA-256 hashes, no prompt text.
   * Local EBS volumes encrypted with KMS CMK.

---

## Authentication and Authorization

| Layer                  | Mechanism                                                     | Rationale                                                        |
| ---------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------- |
| **North-South**        | **OIDC** (preferred) or **API key** in `Authorization` header | Enterprises already use IdPs; fall back to scoped keys for bots  |
| **Service-to-Service** | mTLS with SPIFFE IDs (`spiffe://proxy/...`)                   | Zero-trust inside VPC; Envoy SDS manages cert rotation           |
| **Policy Engine**      | **OPA** (Rego) sidecar                                        | Express per-team → per-model ACLs; hot-reload from Git S3 bucket |

---

## Rate Limits and Quotas

* Envoy **local-rate-limit** filter → low-latency.
* Redis-based **GlobalRateLimit** service replicates across AZs.
* Key = `team_id::model_id`.
* Burst and sustained windows (e.g., 10 RPS / 60 RPS-min).
* On exceed → HTTP 429 with `Retry-After`.

---

## Tokenization and Metering

### Online Token Counter

* Uses **tiktoken** or vendor-provided tokenizer, compiled to WASM for speed.
* Emits:

  ```json
  {
    "ts": 1725564400,
    "tenant": "acme",
    "team": "ml-research",
    "model": "gpt-4o-mini",
    "prompt_tokens": 123,
    "completion_tokens": 456,
    "cost_usd": 0.0021,
    "trace_id": "abc-123"
  }
  ```
* Pushed via gRPC to **Aggregator**.

### Aggregator to Object Store

* Batches 10 k records or 30 s, whichever first, writes **Parquet** to S3 path
  `s3://nd-meter/tenant=<id>/dt=YYYY-MM-DD/part-*.parquet`.
* File metadata contains SHA-256 checksum + signer (Ed25519) for tamper-proof.

### Billing Export

* SNS event with pointer URI & signed digest.
* Upstream **Not Diamond Billing API** pulls, verifies signature, merges into Stripe/BillingOps.
* **Fallback**: Enterprise can FTP/HTTPS push zipped CSV (schema identical).

---

## Tenant and Team Isolation

| Isolation Axis          | Strategy                                                 |
| ----------------------- | -------------------------------------------------------- |
| **Network**             | K8s NetworkPolicies block cross-namespace traffic        |
| **Data**                | Per-team Redis prefix + ClickHouse row-level **RBAC**    |
| **Secrets**             | Hashicorp Vault Agent injections; least privilege tokens |
| **Resource Guardrails** | K8s ResourceQuota per namespace; prevents noisy neighbor |

---

## SOC-2 and Compliance Controls

* **Audit Logs**: Envoy tap filter → CloudWatch/Splunk (PII-redacted).
* **Config as Code**: Helm chart values stored in customer Git; signed commits.
* **Key Rotation**: SPIFFE SVIDs auto-rotate daily; KMS writes audited.
* **Data Residency**: No outbound storage; only token counts leave VPC.

---

## Deployment and Upgrade Strategy

| Deliverable       | Details                                                                                   |
| ----------------- | ----------------------------------------------------------------------------------------- |
| **Helm Chart**    | `enterprise-vpc-proxy-<semver>.tgz`; default values = HA (3 AZ)                           |
| **Blue/Green**    | Chart installs `proxy-v1` ⟶ test ⟶ flip DNS CNAME ⟶ remove `proxy-v0`                     |
| **CRDs**          | `RateLimitPolicy`, `TeamBinding`, `ModelEndpoint` for dynamic config                      |
| **Zero-Downtime** | Envoy hot-restart + `outlier_detection split_external_origin_errors` keeps inflight conns |
| **Rollback**      | `helm rollback` versioned; stateful Redis pods kept separate so data preserved            |

---

## Achieving 99.999% Availability

* Proxy itself is **stateless** behind NLB; 3+ pods per AZ.
* Redis **Active-Active** (`redis-raft` or AWS MemoryDB) for limiter.
* LB health-checks every 2 s; failed pod drained.
* **SLO probes** shipped in Helm: synthetics call dummy model every 15 s; alert if p99 latency > 1 s.
* **Automatic restart** via PodDisruptionBudgets and K8s Autoscaler.
* Customers supply the hardware but chart enforces **multi-AZ spread constraints**; readiness fails if < 2 AZs.

---

## Billing File Format (Parquet schema)

| Column              | Type               | Nullable | Notes             |
| ------------------- | ------------------ | -------- | ----------------- |
| `ts`                | `TIMESTAMP_MILLIS` | no       | Wall-clock        |
| `tenant`            | `STRING`           | no       | Org slug          |
| `team`              | `STRING`           | yes      | Logical team      |
| `model`             | `STRING`           | no       | Vendor/model name |
| `prompt_tokens`     | `INT32`            | no       |                   |
| `completion_tokens` | `INT32`            | no       |                   |
| `cost_usd`          | `DOUBLE`           | no       | pre-computed      |
| `trace_id`          | `STRING`           | yes      | Debug cross-ref   |
| `sig`               | `BINARY(64)`       | no       | Ed25519 signature |

---

## Cost Accounting Logic

```
cost = (prompt_tokens × prompt_price) + (completion_tokens × comp_price)
where price retrieved from VersionedPricing Table (upgradable via CRD)
```

*Prices cached in Redis; updated daily by side-car loader.*

---

## Design Choices vs Alternatives

| Problem                 | Picked                   | Rejected           | Rationale                               |
| ----------------------- | ------------------------ | ------------------ | --------------------------------------- |
| **Gateway**             | Envoy                    | Nginx              | Native rate-limit & mTLS SDS            |
| **Auth**                | OIDC + API keys          | HMAC only          | SSO plus machine compat                 |
| **Meter Staging**       | Parquet on S3            | Direct DB insert   | Write-heavy isolation, cheap            |
| **Aggregator Lang**     | Rust                     | Go                 | Zero-copy Parquet write, 40 % less CPU  |
| **Redis Active-Active** | MemoryDB or `redis-raft` | Single-AZ Sentinel | Five-nines target needs multi-AZ quorum |
| **Billing Export**      | Pull (SaaS)              | Push only          | Handles firewalls; SaaS can backfill    |

---

## Anticipated Questions

<details>
<summary>🔄 *How do we rotate external vendor keys without redeploy?*</summary>
CRD `ModelEndpoint` holds encrypted key (`sealed-secrets`). Re-apply with new version; side-car watcher triggers hot update in Envoy SDS within 5 s—connections re-use.
</details>

<details>
<summary>🧑‍🤝‍🧑 *Can multiple business units share one proxy?*</summary>
Yes—use **team namespaces**: unique API key prefix + OIDC `group` claim; rate-limit keyed by `(tenant,team)`; billing rows aggregated by that key.
</details>

<details>
<summary>💰 *How do we detect cost anomalies early?*</summary>
Aggregator emits `tokens/s` metric; Prometheus alert if > 2× weekly baseline; proxy enforces “budget guardrail” that returns HTTP 402 once soft limit exceeded.
</details>

<details>
<summary>🗄️ *Is data ever stored outside customer VPC?*</summary>
Only token counts & costs (no prompt text) leave VPC; PII redacted; full logs stay local. For ultra-strict customers we support **air-gap mode** where billing files are pushed manually.
</details>

---

## Conclusion

This design delivers a **plug-and-play proxy** that sits inside any enterprise VPC, upholding stringent residency and SOC-2 controls while giving Not Diamond precise, cryptographically verifiable usage data—achieving five-nines availability through HA Helm defaults without us touching customer metal.
