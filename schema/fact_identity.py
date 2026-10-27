"""Stable literal identity and relation meaning, independent of display labels."""
import hashlib
import json
from decimal import Decimal
from datetime import date, datetime, timezone

XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_LANG = "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"
INTEGER_TYPES = {"integer", "int", "long", "short", "byte", "nonNegativeInteger", "positiveInteger"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def literal_identity(triple):
    datatype = triple.get("datatype") or ""
    if not datatype.startswith(("http://", "https://")): datatype = XSD + datatype
    kind, value = datatype.removeprefix(XSD), triple.get("value")
    text = str(value)
    if kind in INTEGER_TYPES: canonical = str(int(text))
    elif kind == "decimal": canonical = format(Decimal(text).normalize(), "f"); canonical = "0" if Decimal(canonical) == 0 else canonical
    elif kind in {"float", "double"}: canonical = float(text).hex()
    elif kind == "boolean": canonical = "true" if value is True or text in {"true", "1"} else "false"
    elif kind == "date": canonical = date.fromisoformat(text).isoformat()
    elif kind == "dateTime":
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        canonical = parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else parsed.isoformat()
    else: canonical = value
    return {"datatype": datatype, "value": canonical, "unit": triple.get("unit"),
            "language": str(triple.get("language") or "").lower() or None}


def relation_semantic_hash(ontology, relation_id):
    from .relation_registry import RelationRegistry
    from ontologyBuild.ontology_adapter import iter_classes, class_reference_index, normalize_name
    relation = RelationRegistry(ontology).properties.get(relation_id)
    if not relation: return None
    classes = {c["id"]: c for c in iter_classes(ontology)}
    references = class_reference_index(ontology)
    closure, pending = {}, [*relation.get("domain", []), *relation.get("range", [])]
    while pending:
        reference = pending.pop()
        identity = references.get(normalize_name(reference))
        if not identity or identity in closure: continue
        item = classes[identity]
        closure[identity] = {k: item.get(k) for k in ("id", "definition", "description", "parent", "parents", "disjoint_with", "equivalent_to")}
        pending.extend(item.get("parents") or ([item["parent"]] if item.get("parent") else []))
    return digest({"relation": {k: relation.get(k) for k in ("id", "definition", "description", "domain", "range", "domain_semantics", "range_semantics", "datatype", "datatypes", "property_kind", "inverse_of")},
                   "endpoint_classes": closure, "constraint_semantics": ontology.get("application_profile", {}).get("constraint_semantics", "allowed_any")})
