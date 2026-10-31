"""One function per security rule in tasks/rules_catalogue.json.

Each function takes (cfg, params) and returns None when the rule passes, or a short message
when it fails. Severity, weight and category live in the catalogue, not here, so there is one
source of truth. The functions follow each rule's 'fails_when' and 'precedence' text exactly.

cfg has already passed check_config(), so every field exists and has the right type.
"""
import ipaddress

from validator.schema import parse_source

RULES = {}


def rule(rule_id):
    def register(fn):
        if rule_id in RULES:
            raise ValueError(f"rule {rule_id} is defined twice")
        RULES[rule_id] = fn
        return fn
    return register


# ---------------------------------------------------------------- helpers

def _sources(cfg, port):
    return [r["source"] for r in cfg["ingress"] if r["port"] == port]


def _is_world(source):
    """0.0.0.0/0 or ::/0, the two sources CIS and Security Hub treat as 'the internet'."""
    network = parse_source(source)
    return network is not None and network.prefixlen == 0


def _open_to_ipv4_internet(cfg, port):
    """Some rule on this port admits every IPv4 address (customers must be able to reach it)."""
    for source in _sources(cfg, port):
        network = parse_source(source)
        if network is not None and network.version == 4 and network.prefixlen == 0:
            return True
    return False


def _covers(source, target):
    """True when the source block contains the whole target block (same IP version)."""
    network = parse_source(source)
    return network is not None and network.version == target.version and target.subnet_of(network)


def _show(sources):
    return ", ".join(s if s else "(empty)" for s in sources)


def _world_open(cfg, port, label):
    world = [s for s in _sources(cfg, port) if _is_world(s)]
    if world:
        return f"{label} (port {port}) is open to the internet from {_show(world)}"
    return None


def _only_from(cfg, port, allowed_cidr, who):
    """Shared logic of WS-02 and DB-03.

    Fails when (a) no source on the port covers allowed_cidr, or (b) a source other than
    allowed_cidr is present that is not world-open. World-open sources belong to WS-01 / DB-02.
    """
    allowed = ipaddress.ip_network(allowed_cidr)
    sources = _sources(cfg, port)
    if not any(_covers(s, allowed) for s in sources):
        found = _show(sources) if sources else "no rule"
        return f"{who} at {allowed} cannot connect on port {port} (sources: {found})"
    extra = [s for s in sources if not _is_world(s) and parse_source(s) != allowed]
    if extra:
        return f"port {port} is also open to {_show(extra)}, not only to {who} at {allowed}"
    return None


# ---------------------------------------------------------------- web server

@rule("WS-01")
def ssh_not_public(cfg, params):
    return _world_open(cfg, 22, "SSH")


@rule("WS-02")
def ssh_only_from_admin(cfg, params):
    return _only_from(cfg, 22, params["admin_cidr"], "the administrator")


@rule("WS-03")
def rdp_not_public(cfg, params):
    return _world_open(cfg, 3389, "RDP")


@rule("WS-04")
def http_reachable(cfg, params):
    if not _open_to_ipv4_internet(cfg, 80):
        return "the website cannot be reached over HTTP (no port 80 rule from 0.0.0.0/0)"
    return None


@rule("WS-05")
def https_reachable(cfg, params):
    if not _open_to_ipv4_internet(cfg, 443):
        return "the website cannot be reached over HTTPS (no port 443 rule from 0.0.0.0/0)"
    return None


@rule("WS-06")
def disk_encrypted(cfg, params):
    if cfg["disk_encryption"] is not True:
        return "the server disk is not encrypted"
    return None


# ---------------------------------------------------------------- storage bucket

BLOCK_PUBLIC_ACCESS = ("block_public_acls", "ignore_public_acls",
                       "block_public_policy", "restrict_public_buckets")


@rule("ST-01")
def public_access_blocked(cfg, params):
    off = [key for key in BLOCK_PUBLIC_ACCESS if cfg["block_public_access"][key] is not True]
    if off:
        return f"Block Public Access is not complete; switched off: {', '.join(off)}"
    return None


@rule("ST-02")
def encrypted_at_rest(cfg, params):
    if cfg["encryption"] not in ("sse-s3", "sse-kms"):
        chosen = "not chosen" if cfg["encryption"] is None else cfg["encryption"]
        return f"objects are not encrypted at rest (encryption: {chosen})"
    return None


@rule("ST-03")
def versioning_enabled(cfg, params):
    if cfg["versioning"] != "enabled":
        chosen = "not chosen" if cfg["versioning"] is None else cfg["versioning"]
        return f"versioning is not enabled (versioning: {chosen})"
    return None


@rule("ST-04")
def access_logging_to_log_bucket(cfg, params):
    logging = cfg["access_logging"]
    target = logging["target_bucket"]
    if logging["enabled"] is not True:
        return "server access logging is off"
    if target == params["log_bucket"]:
        return None
    if not target:
        return "server access logging has no target bucket"
    if target == cfg["bucket_name"]:
        return "server access logs are written into the bucket itself"
    return f"server access logs go to {target}, not to {params['log_bucket']}"


# ---------------------------------------------------------------- database

@rule("DB-01")
def db_not_public(cfg, params):
    if cfg["publicly_accessible"] is not False:
        return "the database is publicly accessible"
    return None


@rule("DB-02")
def db_port_not_public(cfg, params):
    return _world_open(cfg, cfg["port"], "the database")


@rule("DB-03")
def db_port_only_from_app(cfg, params):
    return _only_from(cfg, cfg["port"], params["app_cidr"], "the application server")


@rule("DB-04")
def db_storage_encrypted(cfg, params):
    if cfg["storage_encrypted"] is not True:
        return "database storage is not encrypted"
    return None


@rule("DB-05")
def db_backups_kept(cfg, params):
    days, minimum = cfg["backup_retention_days"], params["min_backup_days"]
    if days == 0:
        return f"automated backups are switched off (retention 0 days; need at least {minimum})"
    if days < minimum:
        return f"backups are kept {days} days; need at least {minimum}"
    return None
