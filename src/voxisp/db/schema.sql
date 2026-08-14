-- Modelo de dados núcleo — spec §8.
-- Multi-tenant com RLS no Postgres (spec §5, Segurança).

CREATE TABLE IF NOT EXISTS tenant_config (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    connectors  jsonb NOT NULL DEFAULT '{}',
    persona     jsonb NOT NULL DEFAULT '{}',
    sla         jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS call (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenant_config(id),
    ani            text NOT NULL,
    dnis           text NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    ended_at       timestamptz,
    duration_s     integer,
    subscriber_id  text,
    outcome        text,             -- contained | escalated | abandoned | error
    contained      boolean NOT NULL DEFAULT false,
    escalated_to   text,             -- fila/motivo de destino, se houver
    protocol       text,
    recording_url  text,
    csat           smallint
);

CREATE TABLE IF NOT EXISTS turn (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id         uuid NOT NULL REFERENCES call(id) ON DELETE CASCADE,
    seq             integer NOT NULL,
    role            text NOT NULL,   -- customer | assistant
    transcript      text NOT NULL,
    asr_confidence  real,
    intent          text,
    entities        jsonb NOT NULL DEFAULT '{}',
    latency_ms      jsonb NOT NULL DEFAULT '{}',
    tokens          jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS action (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id           uuid NOT NULL REFERENCES call(id) ON DELETE CASCADE,
    tool_name         text NOT NULL,
    params            jsonb NOT NULL DEFAULT '{}',
    result            jsonb NOT NULL DEFAULT '{}',
    status            text NOT NULL,   -- pending | success | failed
    idempotency_key   text NOT NULL,
    executed_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS escalation (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id          uuid NOT NULL REFERENCES call(id) ON DELETE CASCADE,
    reason           text NOT NULL,
    queue            text,
    context_payload  jsonb NOT NULL DEFAULT '{}',
    at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS incident_link (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id      uuid NOT NULL REFERENCES call(id) ON DELETE CASCADE,
    incident_id  text NOT NULL,
    source       text NOT NULL   -- massivo
);

-- Índices essenciais (spec §8)
CREATE INDEX IF NOT EXISTS idx_call_tenant_started ON call (tenant_id, started_at);
CREATE INDEX IF NOT EXISTS idx_call_subscriber ON call (subscriber_id);
CREATE INDEX IF NOT EXISTS idx_turn_call_seq ON turn (call_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_idempotency ON action (idempotency_key);
