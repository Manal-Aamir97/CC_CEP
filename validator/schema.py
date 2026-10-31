"""Checks a configuration against schema/config.schema.json, using the standard library only.

The schema is ordinary JSON Schema (draft 2020-12), so the browser form can check it with any
JSON Schema library. This module implements only the keywords that schema uses and raises
SchemaError on any other keyword, so a schema edit can never be silently ignored.

Three levels of checking:
    config_problems(cfg)      structure (schema) plus meaning (CIDRs parse, database port rules)
    check_config(cfg)         raises ConfigError if config_problems finds anything
    submission_problems(cfg)  decision fields still empty; the form must block Submit until none
"""
import ipaddress
import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "config.schema.json"

ANNOTATIONS = {"$schema", "$id", "$comment", "$defs", "title", "description", "default", "examples"}
KEYWORDS = {"$ref", "type", "const", "enum", "pattern", "minLength", "maxLength", "minimum",
            "maximum", "items", "minItems", "maxItems", "required", "properties",
            "additionalProperties", "allOf", "if", "then"}


class SchemaError(Exception):
    """The schema file uses something this checker does not implement."""


class ConfigError(ValueError):
    """The configuration is not valid; .problems lists every reason."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@lru_cache(maxsize=1)
def load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------- JSON Schema subset

def _same(a, b):
    """JSON equality: true is not 1, 7 is not "7"."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _has_type(value, name):
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    raise SchemaError(f"unknown type {name!r}")


def _resolve(ref, root):
    if not ref.startswith("#/"):
        raise SchemaError(f"only local $ref is supported, got {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _join(path, key):
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else key


def _validate(value, schema, path, root, errors):
    unknown = set(schema) - KEYWORDS - ANNOTATIONS
    if unknown:
        raise SchemaError(f"unsupported keyword(s) {sorted(unknown)} at {path or '(root)'}")
    where = path or "(config)"

    if "$ref" in schema:
        _validate(value, _resolve(schema["$ref"], root), path, root, errors)

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_has_type(value, t) for t in types):
            errors.append(f"{where}: expected {' or '.join(types)}, got {json.dumps(value)}")
            return

    if "const" in schema and not _same(value, schema["const"]):
        errors.append(f"{where}: must be {json.dumps(schema['const'])}, got {json.dumps(value)}")
    if "enum" in schema and not any(_same(value, option) for option in schema["enum"]):
        options = ", ".join(json.dumps(option) for option in schema["enum"])
        errors.append(f"{where}: {json.dumps(value)} is not one of {options}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {json.dumps(value)} is not in the expected format")

    if _has_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: {value} is less than the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: {value} is greater than the maximum {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{where}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{where}: allows at most {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate(item, schema["items"], _join(path, index), root, errors)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing required field {key!r}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], _join(path, key), root, errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}: unexpected field {key!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _validate(item, schema["additionalProperties"], _join(path, key), root, errors)

    for sub in schema.get("allOf", []):
        _validate(value, sub, path, root, errors)

    if "if" in schema:
        probe = []
        _validate(value, schema["if"], path, root, probe)
        if not probe and "then" in schema:
            _validate(value, schema["then"], path, root, errors)


def schema_problems(cfg):
    errors = []
    schema = load_schema()
    _validate(cfg, schema, "", schema, errors)
    return errors


# ---------------------------------------------------------------- meaning beyond the schema

def parse_source(source):
    """'203.0.113.25/32' -> ip_network; '' or anything unparseable -> None.

    Host bits are ignored, as AWS does for the scoring question: 203.0.113.25/24 is the /24.
    """
    if not source or "/" not in source:
        return None
    try:
        return ipaddress.ip_network(source, strict=False)
    except ValueError:
        return None


def semantic_problems(cfg):
    problems = []
    for index, rule in enumerate(cfg.get("ingress", [])):
        source = rule.get("source")
        if isinstance(source, str) and source and parse_source(source) is None:
            problems.append(f"ingress[{index}].source: {source!r} is not a valid CIDR block")
        if cfg.get("resource") == "database" and rule.get("port") != cfg.get("port"):
            problems.append(f"ingress[{index}].port: database rules must use the database "
                            f"port {cfg.get('port')}, got {rule.get('port')}")
    return problems


def config_problems(cfg):
    problems = schema_problems(cfg)
    if not problems:
        problems = semantic_problems(cfg)
    return problems


def check_config(cfg):
    problems = config_problems(cfg)
    if problems:
        raise ConfigError(problems)


def submission_problems(cfg):
    """Fields the participant still has to fill in. Says nothing about which value is secure."""
    check_config(cfg)
    missing = [f"ingress[{i}].source: fill in a source"
               for i, rule in enumerate(cfg.get("ingress", [])) if rule["source"] == ""]
    if cfg["resource"] == "storage_bucket":
        if cfg["encryption"] is None:
            missing.append("encryption: choose an option")
        if cfg["versioning"] is None:
            missing.append("versioning: choose an option")
        if cfg["access_logging"]["enabled"] and not cfg["access_logging"]["target_bucket"]:
            missing.append("access_logging.target_bucket: choose a target bucket")
    return missing
