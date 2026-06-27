
"""
KB Region Ingest Script
Reads a YAML file and upserts into Supabase kb_regions table.
Creates the table if it does not exist.
"""

import sys
import json
import yaml
import psycopg2
from psycopg2.extras import Json
from datetime import date, datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Fill these in before running

YOUR_PASSWORD = "FGQmHAEd0B2HlSvI"
PROJECT_REF = "epvedeqnurqnyewksrmw"

DB_URL = f"postgresql://postgres:{YOUR_PASSWORD}@db.{PROJECT_REF}.supabase.co:5432/postgres"

# ── VALID ENUMS ───────────────────────────────────────────────────────────────

ENUMS = {
    "altitude":             {"low", "medium", "high", "very_high"},
    "climate":              {"tropical_humid", "tropical_dry", "cold_desert", "temperate", "alpine", "coastal"},
    "terrain":              {"flat", "hilly", "mountainous", "coastal", "plateau"},
    "water_bodies":         {"none", "limited", "moderate", "abundant"},
    "forest_cover":         {"none", "low", "medium", "high"},
    "urbanization":         {"very_low", "low", "medium", "high", "very_high"},
    "remoteness":           {"very_low", "low", "medium", "high", "very_high"},
    "physical_exertion":    {"low", "medium", "high"},
    "scenic_value":         {"low", "medium", "high", "very_high"},
    "spiritual_culture":    {"none", "low", "medium", "high", "very_high"},
    "social_scene":         {"none", "low", "medium", "high"},
    "nightlife":            {"none", "low", "medium", "high"},
    "pace":                 {"slow", "balanced", "fast"},
    "novelty":              {"low", "medium", "high", "very_high"},
    "in_destination_tier":  {"budget", "mid", "premium"},
    "connectivity":         {"low", "medium", "high"},
    "walkability":          {"low", "medium", "high"},
    "local_authenticity":   {"low", "medium", "high", "very_high"},
    "food_accessibility":   {"low", "medium", "high"},
    "spontaneity_friendly": {"low", "medium", "high"},
    "typical_level":        {"low", "moderate", "high"},
    "peak_level":           {"low", "moderate", "high"},
    "off_peak_level":       {"low", "moderate", "high"},
    "confidence":           {"medium", "high"},
    "data_source":          {"curated", "generated", "verified"},
}

VALID_MONTHS = {"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"}

# ── CREATE TABLE SQL ──────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS kb_regions (
    region_id           TEXT PRIMARY KEY,
    schema_version      TEXT NOT NULL,
    schema_type         TEXT NOT NULL DEFAULT 'travel_region',
    name                TEXT NOT NULL,
    aliases             JSONB,
    environment         JSONB,
    destination_character JSONB,
    seasonality         JSONB,
    cost                JSONB,
    constraints         JSONB,
    experience_attributes JSONB,
    crowd_profile       JSONB,
    uniqueness_factor   JSONB,
    known_for           JSONB,
    meta                JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kb_regions_updated_at ON kb_regions;
CREATE TRIGGER kb_regions_updated_at
    BEFORE UPDATE ON kb_regions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
"""

# ── VALIDATION ────────────────────────────────────────────────────────────────

errors = []

def json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

def as_json(value):
    return Json(value, dumps=lambda obj: json.dumps(obj, default=json_default))

def check_enum(value, field_name):
    if value is None:
        return
    allowed = ENUMS.get(field_name)
    if allowed and value not in allowed:
        errors.append(f"Invalid value '{value}' for '{field_name}'. Allowed: {sorted(allowed)}")

def check_months(months, field_name):
    if not months:
        return
    for m in months:
        if m not in VALID_MONTHS:
            errors.append(f"Invalid month '{m}' in '{field_name}'")

def validate(data):
    # Required top-level fields
    for field in ["schema_version", "schema_type", "region_id", "name"]:
        if not data.get(field):
            errors.append(f"Missing required field: '{field}'")

    if data.get("schema_version") != "1.0":
        errors.append(f"schema_version must be '1.0', got '{data.get('schema_version')}'")

    if data.get("schema_type") != "travel_region":
        errors.append(f"schema_type must be 'travel_region', got '{data.get('schema_type')}'")

    region_id = data.get("region_id", "")
    if region_id != region_id.lower().replace(" ", "_"):
        errors.append(f"region_id must be snake_case lowercase: '{region_id}'")

    # Environment
    env = data.get("environment", {})
    for field in ["altitude", "climate", "terrain", "water_bodies", "forest_cover", "urbanization"]:
        check_enum(env.get(field), field)

    # Destination character
    char = data.get("destination_character", {})
    for field in ["remoteness", "physical_exertion", "scenic_value", "spiritual_culture",
                  "social_scene", "nightlife", "pace", "novelty"]:
        check_enum(char.get(field), field)

    # Seasonality
    season = data.get("seasonality", {})
    check_months(season.get("best_months"), "best_months")
    check_months(season.get("acceptable_months"), "acceptable_months")
    check_months(season.get("avoid_months"), "avoid_months")

    # Cost
    cost = data.get("cost", {})
    check_enum(cost.get("in_destination_tier"), "in_destination_tier")

    # Constraints
    constraints = data.get("constraints", {})
    check_enum(constraints.get("connectivity"), "connectivity")
    for bool_field in ["altitude_above_4000m", "medical_facilities_limited",
                       "road_conditions_challenging", "luxury_inventory_available",
                       "trekking_available", "trekking_required", "permit_required",
                       "beach", "wildlife"]:
        val = constraints.get(bool_field)
        if val is not None and not isinstance(val, bool):
            errors.append(f"'{bool_field}' must be true or false, got '{val}'")

    # Experience attributes
    exp = data.get("experience_attributes", {})
    for field in ["walkability", "local_authenticity", "food_accessibility", "spontaneity_friendly"]:
        check_enum(exp.get(field), field)

    # Crowd profile
    crowd = data.get("crowd_profile", {})
    for field in ["typical_level", "peak_level", "off_peak_level"]:
        check_enum(crowd.get(field), field)
    check_months(crowd.get("peak_months"), "peak_months")

    # Meta
    meta = data.get("meta", {})
    check_enum(meta.get("confidence"), "confidence")
    check_enum(meta.get("data_source"), "data_source")
    if not meta.get("last_updated"):
        errors.append("meta.last_updated is required")

    return errors

# ── INGEST ────────────────────────────────────────────────────────────────────

def ingest(yaml_path):
    print(f"\nReading: {yaml_path}")

    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)

    print("Validating...")
    validate(data)

    if errors:
        print("\nValidation failed:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)

    print("Validation passed.")

    print("Connecting to Supabase...")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    print("Creating table if not exists...")
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()

    print("Upserting region...")
    upsert_sql = """
        INSERT INTO kb_regions (
            region_id, schema_version, schema_type, name, aliases,
            environment, destination_character, seasonality, cost,
            constraints, experience_attributes, crowd_profile,
            uniqueness_factor, known_for, meta
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (region_id) DO UPDATE SET
            schema_version        = EXCLUDED.schema_version,
            schema_type           = EXCLUDED.schema_type,
            name                  = EXCLUDED.name,
            aliases               = EXCLUDED.aliases,
            environment           = EXCLUDED.environment,
            destination_character = EXCLUDED.destination_character,
            seasonality           = EXCLUDED.seasonality,
            cost                  = EXCLUDED.cost,
            constraints           = EXCLUDED.constraints,
            experience_attributes = EXCLUDED.experience_attributes,
            crowd_profile         = EXCLUDED.crowd_profile,
            uniqueness_factor     = EXCLUDED.uniqueness_factor,
            known_for             = EXCLUDED.known_for,
            meta                  = EXCLUDED.meta;
    """

    cur.execute(upsert_sql, (
        data.get("region_id"),
        data.get("schema_version"),
        data.get("schema_type"),
        data.get("name"),
        as_json(data.get("aliases")),
        as_json(data.get("environment")),
        as_json(data.get("destination_character")),
        as_json(data.get("seasonality")),
        as_json(data.get("cost")),
        as_json(data.get("constraints")),
        as_json(data.get("experience_attributes")),
        as_json(data.get("crowd_profile")),
        as_json(data.get("uniqueness_factor")),
        as_json(data.get("known_for")),
        as_json(data.get("meta")),
    ))

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n✓ '{data['name']}' ingested successfully as '{data['region_id']}'")

# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python ingest.py <path-to-yaml>")
        print("Example: python ingest.py spiti_valley.yaml")
        sys.exit(1)

    ingest(sys.argv[1])
