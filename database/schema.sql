-- Trendtype distributor schema
-- Derived from all 56 records in distributors.json.
--
-- Import rules expected by this schema:
--   * Keep one distributor row per source id; do not automatically merge duplicates.
--   * Normalize searchable text in application code with trim + whitespace collapse
--     + Unicode-aware case folding.
--   * Convert numeric founded_year strings to INTEGER; map null/"unknown" to NULL.
--   * Map blank, "N/A", and "TBD" descriptions to NULL. raw_json preserves the source.
--   * Parse only unambiguous dates. Preserve every original date in last_updated_raw.
--   * Treat nested contact and flat contact fields as separate contact rows. This
--     preserves record 17, where the two representations conflict.
--   * Compute record_fingerprint as SHA-256 over a canonicalized source record with
--     the source id removed. It is indexed, not unique, because exact duplicates exist.

PRAGMA foreign_keys = ON;

CREATE TABLE countries (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,

    CONSTRAINT ck_countries_name
        CHECK (length(trim(name)) > 0),
    CONSTRAINT ck_countries_normalized_name
        CHECK (length(trim(normalized_name)) > 0),
    CONSTRAINT uq_countries_normalized_name
        UNIQUE (normalized_name)
);

CREATE TABLE categories (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL,

    CONSTRAINT ck_categories_name
        CHECK (length(trim(name)) > 0),
    CONSTRAINT ck_categories_normalized_name
        CHECK (length(trim(normalized_name)) > 0),
    CONSTRAINT uq_categories_normalized_name
        UNIQUE (normalized_name)
);

CREATE TABLE distributors (
    id                        INTEGER PRIMARY KEY,
    source_id                 INTEGER NOT NULL,
    name                      TEXT NOT NULL,
    normalized_name           TEXT NOT NULL,
    country_id                INTEGER NOT NULL,
    description               TEXT,
    founded_year              INTEGER,
    last_updated_raw          TEXT NOT NULL,
    last_updated_date         TEXT,
    last_updated_parse_status TEXT NOT NULL,
    record_fingerprint        TEXT NOT NULL,
    raw_json                  TEXT NOT NULL,
    ingested_at               TEXT NOT NULL
                              DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    CONSTRAINT uq_distributors_source_id
        UNIQUE (source_id),
    CONSTRAINT fk_distributors_country
        FOREIGN KEY (country_id) REFERENCES countries(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT ck_distributors_source_id
        CHECK (source_id > 0),
    CONSTRAINT ck_distributors_name
        CHECK (length(trim(name)) > 0),
    CONSTRAINT ck_distributors_normalized_name
        CHECK (length(trim(normalized_name)) > 0),
    CONSTRAINT ck_distributors_description
        CHECK (description IS NULL OR length(trim(description)) > 0),
    CONSTRAINT ck_distributors_founded_year
        CHECK (founded_year IS NULL OR founded_year BETWEEN 1800 AND 2100),
    CONSTRAINT ck_distributors_last_updated_raw
        CHECK (length(trim(last_updated_raw)) > 0),
    CONSTRAINT ck_distributors_last_updated_status
        CHECK (
            last_updated_parse_status IN (
                'iso',
                'dmy',
                'mdy',
                'ambiguous',
                'invalid'
            )
        ),
    CONSTRAINT ck_distributors_last_updated_date_shape
        CHECK (
            last_updated_date IS NULL
            OR (
                length(last_updated_date) = 10
                AND last_updated_date GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
            )
        ),
    CONSTRAINT ck_distributors_last_updated_consistency
        CHECK (
            (
                last_updated_parse_status IN ('iso', 'dmy', 'mdy')
                AND last_updated_date IS NOT NULL
            )
            OR (
                last_updated_parse_status IN ('ambiguous', 'invalid')
                AND last_updated_date IS NULL
            )
        ),
    CONSTRAINT ck_distributors_fingerprint
        CHECK (
            length(record_fingerprint) = 64
            AND record_fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
    CONSTRAINT ck_distributors_raw_json
        CHECK (json_valid(raw_json) = 1)
);

CREATE TABLE distributor_aliases (
    id               INTEGER PRIMARY KEY,
    distributor_id   INTEGER NOT NULL,
    alias             TEXT NOT NULL,
    normalized_alias  TEXT NOT NULL,
    source_position   INTEGER NOT NULL,

    CONSTRAINT fk_aliases_distributor
        FOREIGN KEY (distributor_id) REFERENCES distributors(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT ck_aliases_alias
        CHECK (length(trim(alias)) > 0),
    CONSTRAINT ck_aliases_normalized_alias
        CHECK (length(trim(normalized_alias)) > 0),
    CONSTRAINT ck_aliases_source_position
        CHECK (source_position >= 0),
    CONSTRAINT uq_aliases_value
        UNIQUE (distributor_id, normalized_alias),
    CONSTRAINT uq_aliases_position
        UNIQUE (distributor_id, source_position)
);

CREATE TABLE distributor_categories (
    distributor_id INTEGER NOT NULL,
    category_id    INTEGER NOT NULL,

    CONSTRAINT pk_distributor_categories
        PRIMARY KEY (distributor_id, category_id),
    CONSTRAINT fk_distributor_categories_distributor
        FOREIGN KEY (distributor_id) REFERENCES distributors(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT fk_distributor_categories_category
        FOREIGN KEY (category_id) REFERENCES categories(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE distributor_locations (
    id              INTEGER PRIMARY KEY,
    distributor_id  INTEGER NOT NULL,
    city            TEXT NOT NULL,
    normalized_city TEXT NOT NULL,
    country_id      INTEGER NOT NULL,
    source_position INTEGER NOT NULL,

    CONSTRAINT fk_locations_distributor
        FOREIGN KEY (distributor_id) REFERENCES distributors(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT fk_locations_country
        FOREIGN KEY (country_id) REFERENCES countries(id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    CONSTRAINT ck_locations_city
        CHECK (length(trim(city)) > 0),
    CONSTRAINT ck_locations_normalized_city
        CHECK (length(trim(normalized_city)) > 0),
    CONSTRAINT ck_locations_source_position
        CHECK (source_position >= 0),
    CONSTRAINT uq_locations_value
        UNIQUE (distributor_id, normalized_city, country_id),
    CONSTRAINT uq_locations_position
        UNIQUE (distributor_id, source_position)
);

CREATE TABLE distributor_contacts (
    id              INTEGER PRIMARY KEY,
    distributor_id  INTEGER NOT NULL,
    email           TEXT COLLATE NOCASE,
    phone           TEXT,
    source_shape    TEXT NOT NULL,
    source_position INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT fk_contacts_distributor
        FOREIGN KEY (distributor_id) REFERENCES distributors(id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,
    CONSTRAINT ck_contacts_value
        CHECK (
            (email IS NOT NULL AND length(trim(email)) > 0)
            OR (phone IS NOT NULL AND length(trim(phone)) > 0)
        ),
    CONSTRAINT ck_contacts_source_shape
        CHECK (source_shape IN ('nested_contact', 'flat_fields')),
    CONSTRAINT ck_contacts_source_position
        CHECK (source_position >= 0),
    CONSTRAINT uq_contacts_source
        UNIQUE (distributor_id, source_shape, source_position)
);

-- Search/filter indexes. Name/alias prefix searches can use these directly.
-- At 500k rows, substring/fuzzy name search should move to FTS/trigram search
-- and pagination should move from OFFSET to a stable keyset cursor.
CREATE INDEX idx_distributors_normalized_name
    ON distributors (normalized_name, id);

CREATE INDEX idx_distributors_country
    ON distributors (country_id, normalized_name, id);

CREATE INDEX idx_distributors_record_fingerprint
    ON distributors (record_fingerprint);

CREATE INDEX idx_aliases_normalized_alias
    ON distributor_aliases (normalized_alias, distributor_id);

CREATE INDEX idx_distributor_categories_category
    ON distributor_categories (category_id, distributor_id);

CREATE INDEX idx_locations_country_city
    ON distributor_locations (country_id, normalized_city, distributor_id);

CREATE INDEX idx_contacts_email
    ON distributor_contacts (email)
    WHERE email IS NOT NULL;

CREATE INDEX idx_contacts_phone
    ON distributor_contacts (phone)
    WHERE phone IS NOT NULL;
