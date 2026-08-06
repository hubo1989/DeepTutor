-- DeepTutor commercial control plane v1.
-- This file is intentionally replayable; the runner additionally records and
-- verifies its SHA-256 checksum in commercial.schema_migration.

CREATE SCHEMA IF NOT EXISTS commercial;

CREATE TABLE IF NOT EXISTS commercial.schema_migration (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS commercial.billing_customer (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    external_customer_id TEXT,
    trial_claimed_at TIMESTAMPTZ,
    trial_subscription_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT billing_customer_trial_pair CHECK (
        (trial_claimed_at IS NULL AND trial_subscription_id IS NULL)
        OR (trial_claimed_at IS NOT NULL AND trial_subscription_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS billing_customer_provider_external_uidx
    ON commercial.billing_customer (provider, external_customer_id)
    WHERE external_customer_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS commercial.plan_version (
    id TEXT PRIMARY KEY,
    plan_code TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    name TEXT NOT NULL,
    price_minor BIGINT NOT NULL CHECK (price_minor >= 0),
    currency CHAR(3) NOT NULL,
    billing_interval TEXT NOT NULL CHECK (billing_interval IN ('month', 'year')),
    trial_days INTEGER NOT NULL CHECK (trial_days > 0),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (plan_code, version)
);

CREATE TABLE IF NOT EXISTS commercial.entitlement (
    id TEXT PRIMARY KEY,
    plan_version_id TEXT NOT NULL
        REFERENCES commercial.plan_version(id) ON DELETE RESTRICT,
    key TEXT NOT NULL,
    value_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (plan_version_id, key)
);

CREATE TABLE IF NOT EXISTS commercial.subscription (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL
        REFERENCES commercial.billing_customer(id) ON DELETE NO ACTION
        DEFERRABLE INITIALLY DEFERRED,
    plan_version_id TEXT NOT NULL
        REFERENCES commercial.plan_version(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (
        status IN ('trialing', 'active', 'past_due', 'canceled', 'expired')
    ),
    provider TEXT NOT NULL,
    external_subscription_id TEXT,
    current_period_start TIMESTAMPTZ NOT NULL,
    current_period_end TIMESTAMPTZ NOT NULL,
    trial_started_at TIMESTAMPTZ,
    trial_ends_at TIMESTAMPTZ,
    canceled_at TIMESTAMPTZ,
    version BIGINT NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT subscription_period_order CHECK (current_period_end > current_period_start),
    CONSTRAINT subscription_trial_shape CHECK (
        (status <> 'trialing')
        OR (
            trial_started_at IS NOT NULL
            AND trial_ends_at IS NOT NULL
            AND trial_ends_at > trial_started_at
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS subscription_provider_external_uidx
    ON commercial.subscription (provider, external_subscription_id)
    WHERE external_subscription_id IS NOT NULL;

-- Protects the commercial invariant from write skew across app instances.
CREATE UNIQUE INDEX IF NOT EXISTS subscription_one_live_per_customer_uidx
    ON commercial.subscription (customer_id)
    WHERE status IN ('trialing', 'active', 'past_due');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'billing_customer_trial_subscription_fk'
          AND conrelid = 'commercial.billing_customer'::regclass
    ) THEN
        ALTER TABLE commercial.billing_customer
            ADD CONSTRAINT billing_customer_trial_subscription_fk
            FOREIGN KEY (trial_subscription_id)
            REFERENCES commercial.subscription(id)
            ON DELETE NO ACTION
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS commercial.webhook_event (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES commercial.billing_customer(id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    provider TEXT NOT NULL,
    external_event_id TEXT NOT NULL,
    dedupe_key CHAR(71) NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    processing_error TEXT,
    processing_attempts INTEGER NOT NULL DEFAULT 0 CHECK (processing_attempts >= 0),
    UNIQUE (provider, external_event_id)
);

CREATE INDEX IF NOT EXISTS webhook_event_unprocessed_idx
    ON commercial.webhook_event (received_at, id)
    WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS commercial.usage_reservation (
    id TEXT PRIMARY KEY,
    dedupe_key CHAR(71) NOT NULL UNIQUE,
    customer_id TEXT NOT NULL
        REFERENCES commercial.billing_customer(id) ON DELETE RESTRICT,
    subscription_id TEXT NOT NULL
        REFERENCES commercial.subscription(id) ON DELETE RESTRICT,
    meter TEXT NOT NULL,
    quantity BIGINT NOT NULL CHECK (quantity > 0),
    state TEXT NOT NULL CHECK (state IN ('active', 'finalized', 'released')),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    finalized_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    actual_quantity BIGINT CHECK (actual_quantity >= 0),
    usage_event_id TEXT,
    CONSTRAINT usage_reservation_period_order CHECK (period_end > period_start),
    CONSTRAINT usage_reservation_terminal_shape CHECK (
        (state = 'active' AND finalized_at IS NULL AND released_at IS NULL)
        OR (state = 'finalized' AND finalized_at IS NOT NULL AND released_at IS NULL)
        OR (state = 'released' AND released_at IS NOT NULL AND finalized_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS usage_reservation_open_idx
    ON commercial.usage_reservation (
        customer_id,
        subscription_id,
        meter,
        period_start,
        period_end
    )
    WHERE state = 'active';

CREATE TABLE IF NOT EXISTS commercial.usage_event (
    id TEXT PRIMARY KEY,
    dedupe_key CHAR(71) NOT NULL UNIQUE,
    reservation_id TEXT NOT NULL UNIQUE
        REFERENCES commercial.usage_reservation(id) ON DELETE RESTRICT,
    customer_id TEXT NOT NULL
        REFERENCES commercial.billing_customer(id) ON DELETE RESTRICT,
    subscription_id TEXT NOT NULL
        REFERENCES commercial.subscription(id) ON DELETE RESTRICT,
    meter TEXT NOT NULL,
    quantity BIGINT NOT NULL CHECK (quantity >= 0),
    provider TEXT,
    model TEXT,
    usage_units_json JSONB NOT NULL,
    price_version TEXT NOT NULL CHECK (length(btrim(price_version)) > 0),
    cost_micros BIGINT NOT NULL CHECK (cost_micros >= 0),
    is_estimated BOOLEAN NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT usage_event_period_order CHECK (period_end > period_start)
);

CREATE INDEX IF NOT EXISTS usage_event_period_idx
    ON commercial.usage_event (
        customer_id,
        subscription_id,
        meter,
        period_start,
        period_end
    );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'usage_reservation_usage_event_fk'
          AND conrelid = 'commercial.usage_reservation'::regclass
    ) THEN
        ALTER TABLE commercial.usage_reservation
            ADD CONSTRAINT usage_reservation_usage_event_fk
            FOREIGN KEY (usage_event_id)
            REFERENCES commercial.usage_event(id)
            ON DELETE NO ACTION
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION commercial.prevent_plan_version_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable after publication', TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'plan_version_immutable'
          AND tgrelid = 'commercial.plan_version'::regclass
    ) THEN
        CREATE TRIGGER plan_version_immutable
            BEFORE UPDATE OR DELETE ON commercial.plan_version
            FOR EACH ROW EXECUTE FUNCTION commercial.prevent_plan_version_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'entitlement_immutable'
          AND tgrelid = 'commercial.entitlement'::regclass
    ) THEN
        CREATE TRIGGER entitlement_immutable
            BEFORE UPDATE OR DELETE ON commercial.entitlement
            FOR EACH ROW EXECUTE FUNCTION commercial.prevent_plan_version_mutation();
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION commercial.protect_trial_claim()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.trial_subscription_id IS NOT NULL
       AND (
           NEW.trial_subscription_id IS DISTINCT FROM OLD.trial_subscription_id
           OR NEW.trial_claimed_at IS DISTINCT FROM OLD.trial_claimed_at
       ) THEN
        RAISE EXCEPTION 'lifetime trial claim is immutable'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'billing_customer_trial_claim_immutable'
          AND tgrelid = 'commercial.billing_customer'::regclass
    ) THEN
        CREATE TRIGGER billing_customer_trial_claim_immutable
            BEFORE UPDATE ON commercial.billing_customer
            FOR EACH ROW EXECUTE FUNCTION commercial.protect_trial_claim();
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION commercial.validate_subscription_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;
    IF NOT (
        (OLD.status = 'trialing' AND NEW.status IN ('expired', 'canceled'))
        OR (OLD.status = 'active' AND NEW.status IN ('past_due', 'canceled'))
        OR (OLD.status = 'past_due' AND NEW.status IN ('active', 'canceled'))
    ) THEN
        RAISE EXCEPTION 'invalid subscription transition: % -> %', OLD.status, NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'subscription_state_machine'
          AND tgrelid = 'commercial.subscription'::regclass
    ) THEN
        CREATE TRIGGER subscription_state_machine
            BEFORE UPDATE OF status ON commercial.subscription
            FOR EACH ROW EXECUTE FUNCTION commercial.validate_subscription_transition();
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION commercial.validate_usage_reservation_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.state = NEW.state THEN
        RETURN NEW;
    END IF;
    IF OLD.state <> 'active' OR NEW.state NOT IN ('finalized', 'released') THEN
        RAISE EXCEPTION 'invalid usage reservation transition: % -> %', OLD.state, NEW.state
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION commercial.prevent_usage_event_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('deeptutor.commercial_erasure', TRUE) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'usage_event rows are immutable after settlement'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'usage_event_immutable'
          AND tgrelid = 'commercial.usage_event'::regclass
    ) THEN
        CREATE TRIGGER usage_event_immutable
            BEFORE UPDATE OR DELETE ON commercial.usage_event
            FOR EACH ROW EXECUTE FUNCTION commercial.prevent_usage_event_mutation();
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'usage_reservation_state_machine'
          AND tgrelid = 'commercial.usage_reservation'::regclass
    ) THEN
        CREATE TRIGGER usage_reservation_state_machine
            BEFORE UPDATE OF state ON commercial.usage_reservation
            FOR EACH ROW EXECUTE FUNCTION commercial.validate_usage_reservation_transition();
    END IF;
END
$$;
