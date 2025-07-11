*A real-time monitoring and recommendation platform that ingests metrics from **millions of LLM calls/day**, computes live health signals for **100 + providers**, and returns **sub-100 ms** model recommendations under 99.9 % availability.*

---

## Hard Requirements and Operating Targets

| Axis | Specification |
|------|---------------|
| **Throughput** | ≥ 3 M LLM invocations/day (≈ 35 RPS sustained; 500 RPS burst) |
| **Ingest → Recommendation** | Live metrics visible & routed on P95 ≤ 5 s |
| **Recommendation Latency** | P50 ≤ 40 ms, P95 ≤ 100 ms |
| **Availability** | 99.9 % API uptime |
| **Customization** | Per-request weight vector ⟨latency, cost, accuracy⟩ (∑ = 1) |
| **Provider Outage Handling** | Detect within 30 s; auto-reroute with zero 5xx to caller |
| **Storage Horizon** | 90 days hot; 1 year cold |

---

## Bird’s-Eye Architecture

```mermaid
flowchart TB
  subgraph "Ingress Plane"
    G1[Edge API GW<br/>(Anycast)] --> NORM[Request Normalizer]
    NORM --> RANK[Realtime Recommender]
    RANK --> RESP[Best Model<br/>and Fallbacks]
  end

  NORM --> METRIC(msg)
  METRIC --> KAFKA([Kafka topic:<br/>metrics.raw])

  subgraph "Stream Proc Cluster"
    KAFKA --> FENRIR[Fluvio/Flink<br/>window agg]
    FENRIR --> FEAT[(Features KV<br/>Redis Cluster)]
    FENRIR --> CH[(ClickHouse<br/>Hot OLAP)]
  end

  FEAT --> RANK
  CH --> DASH[Grafana/React Dashboard]

  FENRIR -. alerts .-> ALERT[Alertmanager ➜ PagerDuty]
  classDef comp fill:#f6f,stroke:#333,stroke-width:1px;
````

---

## Request and Metric Flow

1. **Client** hits **Edge Gateway** (Anycast → nearest POP).
2. **Normalizer** tags request (`tenant, query_type, input_length`) → attaches UUID `trace_id`.
3. Pushes *metric stub* to **Kafka** (`metrics.raw`):

   ```json
   { "ts": ..., "trace_id": "...", "provider": "openai", "event": "start", "meta": {...} }
   ```
4. After LLM response returns to client, edge proxy emits `"end"` record with latency, tokens, cost, judge accuracy (if available).
5. **Flink** aggregates sliding windows (5 s, 1 m) producing per-provider KPIs: p50/95 latency, error %, cost/tok, accuracy score.
6. Aggregator writes:

   * **Redis HASH** (`provider_id → {lat_p50, err_pct, cost}`) for *live* recommendation.
   * **ClickHouse** (append-only) for analytics & long-range trends.

---

## Real-Time Recommender (core logic)

```python
score = (w_latency  * norm(latency_p50)) +
        (w_cost     * norm($/tok))      +
        (w_accuracy * (1 - norm(acc_score)))
```

* **Weights** `⟨w_latency, w_cost, w_accuracy⟩` supplied per call.
* `norm()` = min-max across active providers (cached 5 s).
* Returns **top-K** models + **ordered fallback** list excluding providers with:

  * error % > 10 % (circuit-breaker) **OR**
  * health data older than 30 s.

*Response example*:

```json
{
  "primary": "claude-4-sonnet",
  "fallbacks": ["gpt-4o-mini","gemini-2.0-flash"],
  "explain": { "scores": {...}, "weights": {...} }
}
```

*Recommender is a stateless Go service; 99-percentile latency 6 ms (all in-mem).*

---

## Provider Health and Outage Detection

| Signal          | Threshold                | Action            |
| --------------- | ------------------------ | ----------------- |
| **5xx ratio**   | > 5 % over 30 s          | Mark **DEGRADED** |
| **Timeout p95** | > 2 × baseline           | Mark **DEGRADED** |
| **Kafka lag**   | > 15 s (missing metrics) | Mark **UNKNOWN**  |
| **Manual flag** | Ops CLI toggle           | Force **DOWN**    |

State stored in **Redis** key TTL = 10 s; recommender filters providers `state != UP`.

Alerting rules in **Prometheus ➜ Alertmanager**.

---

## Scaling and SLO Safeguards

| Plane          | Scaling Strategy                                   |
| -------------- | -------------------------------------------------- |
| **Edge GW**    | k8s HPA on RPS + p95 latency; 3 × AZ               |
| **Kafka**      | 48 partitions; tiered storage; MirrorMaker2 for DR |
| **Flink**      | Autoscale on lag; state backend = RocksDB + S3     |
| **Redis**      | Cluster mode; partition by `hash(provider_id)`     |
| **ClickHouse** | 2 shards × 3 replicas; hot NVMe → cold S3          |

*Back-pressure:* Edge emits 503 “Service Busy” if Redis p99 > 15 ms or Flink lag > 60 s.

---

## Custom Evaluation Criteria

Clients supply:

```json
{
  "weights": { "latency":0.2, "cost":0.3, "accuracy":0.5 },
  "constraints": { "region":"eu", "max_cost":0.002 }
}
```

**Constraint filter** runs before scoring.
Accuracy can be modelled via *synthetic judge tasks* (per-provider daily sweep) inserted into feature store.

---

## Data Model (ClickHouse)

```sql
CREATE TABLE provider_metrics (
  ts DateTime,
  provider LowCardinality(String),
  region LowCardinality(String),
  latency_p50_ms UInt16,
  latency_p95_ms UInt16,
  err_ratio Float32,
  accuracy Float32,
  cost_per_tok Float32,
  window ENUM('5s','1m','1h')
) ENGINE = ReplicatedMergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (provider, region, ts, window);
```

Materialized views:

* `provider_health_mv` – latest row per provider → drives Grafana heatmap.
* `cost_trend_mv` – 7-day moving avg for anomaly detection.

---

## Handling Millions of Daily Calls

* **Batch-metrics**: Edge proxies batch multiple completions into one Kafka message (compression Snappy) → 4 × network savings.
* **Async judge**: heavy accuracy eval runs on sampled traffic (e.g., 5 %) to keep cost stable.
* **Price tiers**: Use **OpenCost** style tagging (`tenant`, `team`) for chargeback.

---

## Key Design Trade-Offs

| Decision              | Choice     | Alternative       | Why                                                          |
| --------------------- | ---------- | ----------------- | ------------------------------------------------------------ |
| Stream engine         | **Flink**  | Kinesis Analytics | Stateful ops, open source, vendor-agnostic                   |
| Feature KV            | **Redis**  | DynamoDB          | 1-ms reads, on-prem & VPC deployable                         |
| Recommender in memory | Go + Redis | Vector DB         | Scalar score cheaper & faster                                |
| Accuracy sampling     | 5 %        | 100 %             | Saves 90 % judge cost; statistically robust via Wilson score |
| Outage detect window  | 30 s       | 5 s               | Avoid false positives on transient spikes                    |

---

## Anticipated Q&A

<details>
<summary>🔄 *What if Redis fails?*</summary>
Use Redis Cluster with 3 masters + 3 replicas; Edge has local fallback scoring based on last-known static table (warm file) valid 5 min → still under 100 ms.
</details>

<details>
<summary>🌍 *How to respect data residency (EU vs US)?*</summary>
Separate Kafka + Flink + Redis clusters per region; Edge tag `region`; recommender selects only same-region providers unless cross-region allowed via header flag.
</details>

<details>
<summary>💸 *Contain cost when a provider secretly raises prices?*</summary>
Price feed ingested nightly; if `cost_per_tok > budget * 1.2`, recommender score penalized; alert to FinOps.
</details>

<details>
<summary>🧪 *Testing before prod?*</summary>
Replay last 24 h metrics through staging stack; validate p50 recommendation latency; chaos test by dropping provider metrics stream to verify auto-checkbox.
</details>

---

## Conclusion

By separating fast in-memory feature serving (Redis) from high-write OLAP (ClickHouse) and driving decisions with sliding-window aggregates computed in Flink, the platform meets **sub-100 ms recommendation latency**, auto-handles provider degradation, and scales economically to **millions of calls per day**—all while giving users granular control over latency ↔ cost ↔ accuracy trade-offs.
