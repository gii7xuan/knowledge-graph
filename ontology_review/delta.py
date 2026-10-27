"""Materialize only explicit, bounded operations on an immutable base copy."""
from copy import deepcopy
from ontologyBuild.ontology_adapter import iter_classes, iter_object_properties, iter_datatype_properties, normalize_name
from ontologyBuild.providers.legacy import definition_conflicts, unexecuted_constraints
from review_agent.ontology_guard import validate_ontology
from review_agent.contracts import sha256_json
from .contracts import KINDS


def catalog(schema):
    return {item["id"]: {**item, "schema_kind": kind} for kind, iterator in (
        ("class", iter_classes), ("object_property", iter_object_properties), ("data_property", iter_datatype_properties)) for item in iterator(schema)}


def records(schema):
    owners = [schema]
    classes = schema.get("classes") or {}
    for key, item in (classes.items() if isinstance(classes, dict) else enumerate(classes)):
        if isinstance(item, dict):
            owners.append(item)
            yield classes, key, str(item.get("id") or key), item
    for owner in owners:
        for field in ("object_properties", "data_properties", "datatype_properties"):
            bucket = owner.get(field) or {}
            for key, item in (bucket.items() if isinstance(bucket, dict) else enumerate(bucket)):
                if isinstance(item, dict): yield bucket, key, str(item.get("id") or item.get("field") or key), item


def put(schema, bucket_name, item):
    bucket = schema.setdefault(bucket_name, {})
    if isinstance(bucket, list): bucket.append(item)
    elif isinstance(bucket, dict): bucket[item["id"]] = item
    else: raise ValueError("unsupported base schema container")


def apply_delta(base, operations, allowed_evidence_ids):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 16: raise ValueError("1..16 complete operations required")
    owl_source='ontology.ttl' in base.manifest.get('artifacts',{})
    annotation_fields={'label_language','definition_language','alias_language'} if owl_source else set()
    schema, normalized, allocated = deepcopy(base.ontology), [], {}
    for op in operations:
        if not isinstance(op, dict) or set(op) != {"kind", "target", "changes", "evidence_ids"} or not isinstance(op.get("kind"), str) or op.get("kind") not in KINDS:
            raise ValueError("operation requires kind/target/changes/evidence_ids")
        if not isinstance(op["changes"], dict) or not op["changes"] or len(str(op)) > 16000: raise ValueError("invalid bounded changes")
        if (not isinstance(op["evidence_ids"], list) or not op["evidence_ids"]
                or any(not isinstance(e, str) or e not in allowed_evidence_ids for e in op["evidence_ids"])):
            raise ValueError("every operation requires current source/modeling evidence")
        changes, kind = deepcopy(op["changes"]), op["kind"]
        if kind.startswith("add_") and kind != "add_alias":
            if op["target"] is not None or set(changes) - ({"label", "definition", "parent", "parents", "domain", "range", "datatype", "datatypes", "aliases"}|annotation_fields):
                raise ValueError("new IDs are allocated by server; unsupported modeling field")
            if not all(isinstance(changes.get(k), str) and changes[k].strip() for k in ("label", "definition")):
                raise ValueError("new schema items require label and definition")
            label = normalize_name(changes["label"])
            if label in allocated: raise ValueError("duplicate new label")
            if any(normalize_name(i.get("label", i["id"])) == label or label in {normalize_name(a) for a in i.get("aliases", [])} for i in catalog(schema).values()):
                raise ValueError("existing label/alias requires reuse or explicit review")
            prefix = {"add_class": "C_", "add_object_property": "R_", "add_data_property": "D_"}[kind]
            identity = prefix + sha256_json([base.manifest["release_id"], kind, label])[:20]
            if owl_source:
                from ontologyBuild.iri_identity import owl_extension_identity
                identity,_=owl_extension_identity(base.manifest['release_id'],kind,label)
            allocated[label] = identity
            normalized.append({**deepcopy(op), "target": identity})
        else:
            if not isinstance(op["target"], str) or op["target"] not in catalog(schema): raise ValueError("existing formal target required")
            normalized.append(deepcopy(op))
    classes = {normalize_name(item["id"]): item["id"] for item in iter_classes(schema)}
    for item in iter_classes(schema):
        for alias in [item.get("label"), *(item.get("aliases") or [])]:
            if alias: classes[normalize_name(alias)] = item["id"]
    for op in normalized:
        if op["kind"] == "add_class": classes[normalize_name(op["changes"]["label"])] = op["target"]
    for op in normalized:
        kind, target, changes = op["kind"], op["target"], deepcopy(op["changes"])
        if kind not in {'add_class','add_object_property','add_data_property'} and target not in catalog(schema): raise ValueError('operation target was retired earlier in this delta')
        for key in ("domain", "range", "parents", "aliases", "datatypes"):
            if key in changes and (not isinstance(changes[key], list) or any(not isinstance(v, str) or not v.strip() for v in changes[key])):
                raise ValueError("modeling lists require nonempty strings")
        for key in ("domain", "range", "parents"):
            if key in changes:
                if any(normalize_name(v) not in classes for v in changes[key]): raise ValueError("unresolved class reference")
                changes[key] = [classes[normalize_name(v)] for v in changes[key]]
        if changes.get("parent"):
            if normalize_name(changes["parent"]) not in classes: raise ValueError("unresolved parent")
            changes["parent"] = classes[normalize_name(changes["parent"])]
        if kind in {"add_class", "add_object_property", "add_data_property"}:
            allowed = {"add_class": {"label", "definition", "parent", "parents", "aliases"},
                       "add_object_property": {"label", "definition", "domain", "range", "aliases"},
                       "add_data_property": {"label", "definition", "domain", "datatype", "datatypes", "aliases"}}[kind]
            if set(changes) - (allowed|annotation_fields): raise ValueError("unsupported field for schema item kind")
            if kind == "add_object_property" and (not changes.get("domain") or not changes.get("range")): raise ValueError("property endpoints must be modeled, not guessed as empty")
            if kind == "add_data_property" and (not changes.get("domain") or not (changes.get("datatypes") or changes.get("datatype"))): raise ValueError("data property requires modeled domain/datatype")
            put(schema, {"add_class": "classes", "add_object_property": "object_properties", "add_data_property": "datatype_properties"}[kind], {"id": target, **changes})
        elif kind == "merge_terms":
            into = changes.get("into")
            items = catalog(schema)
            if set(changes) != {"into"} or not isinstance(into,str) or into not in items or into == target or items[into]["schema_kind"] != items[target]["schema_kind"]:
                raise ValueError("merge requires a distinct existing item of the same kind")
            for _, _, identity, item in list(records(schema)):
                for field in ("domain", "range", "subclasses", "parents"):
                    if field in item: item[field] = [into if v == target else v for v in item[field]]
                if item.get("parent") == target: item["parent"] = into
                if identity == into: item["aliases"] = list(dict.fromkeys([*item.get("aliases", []), items[target].get("label", target)]))
            for bucket, key, identity, _ in reversed(list(records(schema))):
                if identity == target:
                    if isinstance(bucket, list): bucket.pop(key)
                    else: del bucket[key]
            schema.setdefault("retired_schema_ids", {})[target] = into
        else:
            target_kind = catalog(schema)[target]["schema_kind"]
            constraints = {"class": {"parent", "parents"}, "object_property": {"domain", "range"}, "data_property": {"domain", "datatypes", "datatype"}}[target_kind]
            allowed = {"add_alias": {"aliases"}|({'alias_language'} if owl_source else set()),
                       "revise_definition": {"definition"}|({'definition_language'} if owl_source else set()), "revise_constraint": constraints}[kind]
            if set(changes) - allowed: raise ValueError("operation modifies unapproved fields")
            if kind == "revise_definition" and (not isinstance(changes.get("definition"), str) or not changes["definition"].strip()): raise ValueError("nonempty definition required")
            for _, _, identity, item in records(schema):
                if identity == target:
                    if kind == "add_alias": item["aliases"] = list(dict.fromkeys([*item.get("aliases", []), *changes.get("aliases", [])]))
                    else:
                        item.update(changes)
                        if target_kind=='class' and kind=='revise_constraint':
                            if 'parents' in changes: item['parent']=changes['parents'][0] if changes['parents'] else None
                            elif 'parent' in changes: item['parents']=[changes['parent']] if changes['parent'] else []
        op["changes"] = changes
    result={"operations": normalized, "merged_schema": schema, "delta_hash": sha256_json(normalized),
            "risk_category": "high_impact" if any(o["kind"] in {"merge_terms", "revise_constraint", "revise_definition"} for o in normalized) else "additive"}
    if owl_source:
        from .owl_delta import compile_owl_delta
        compiled=compile_owl_delta(base,normalized)
        result.update(merged_schema=compiled['project_schema'],owl_patch=compiled['patch'])
    return result


def check_structure(schema, base_profile, base_schema=None):
    report = validate_ontology(schema)
    errors = [*report["errors"], *definition_conflicts(schema)]
    if schema.get("application_profile") != base_profile: errors.append({"code": "PROFILE_CHANGE_NOT_REVIEWED"})
    if unexecuted_constraints(schema): errors.append({"code": "UNEXECUTED_AXIOMS_REQUIRE_DEDICATED_PROFILE"})
    from schema.relation_registry import RelationRegistry
    aliases = RelationRegistry(schema).aliases
    prior_aliases = RelationRegistry(base_schema or {}).aliases
    for alias, identities in aliases.items():
        if len(identities) > 1 and identities != prior_aliases.get(alias): errors.append({"code": "PROPERTY_ALIAS_CONFLICT", "alias": alias, "ids": sorted(identities)})
    prior = catalog(base_schema or {})
    known = {"string", "boolean", "integer", "int", "long", "short", "byte", "nonNegativeInteger", "positiveInteger", "decimal", "double", "float", "date", "dateTime", "anyURI"}
    supported_datatypes = known | {"http://www.w3.org/2001/XMLSchema#" + name for name in known} | {"http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"}
    for identity, item in catalog(schema).items():
        if item["schema_kind"] != "data_property" or item == prior.get(identity): continue
        datatypes = item.get("datatypes") or ([item["datatype"]] if item.get("datatype") else [])
        if len(datatypes) != 1 or datatypes[0] not in supported_datatypes:
            errors.append({"code": "UNSUPPORTED_DATATYPE", "id": identity})
    if any(not item.get("definition") and not item.get("description") for item in catalog(schema).values()):
        report["definition_coverage"] = "partial_legacy"
    return {"status": "pass" if not errors else "fail", "errors": errors, "warnings": report["warnings"],
            "class_count": report["class_count"], "relation_count": report["relation_count"], "owl_reasoner_executed": False}
