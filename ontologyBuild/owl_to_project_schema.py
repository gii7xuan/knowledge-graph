"""Conservative Turtle/OWL import into an explicitly limited application profile.

All RDF statements remain in the source artifact. Only named classes, named
subclass edges and named domain/range constraints are executable here. This is
not an OWL reasoner: domain/range are used as application admission constraints.
"""

from __future__ import annotations

import hashlib
from typing import Any


def convert_turtle(source: str) -> dict[str, Any]:
    try:
        from rdflib import Graph, Literal, RDF, RDFS, OWL, URIRef, XSD, SKOS
        from rdflib.compare import to_canonical_graph
    except ImportError as exc:
        raise RuntimeError("Turtle import requires ontologyBuild/requirements-import.txt") from exc

    graph = to_canonical_graph(Graph().parse(data=source, format="turtle"))
    class_nodes = set(graph.subjects(RDF.type, OWL.Class)) | set(graph.subjects(RDF.type, RDFS.Class))
    objects = set(graph.subjects(RDF.type, OWL.ObjectProperty))
    data = set(graph.subjects(RDF.type, OWL.DatatypeProperty))
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        class_nodes.update(node for node in (child, parent) if isinstance(node, URIRef))
    for prop in objects | data:
        class_nodes.update(node for node in graph.objects(prop, RDFS.domain) if isinstance(node, URIRef))
        if prop in objects:
            class_nodes.update(node for node in graph.objects(prop, RDFS.range) if isinstance(node, URIRef))
    class_nodes = {node for node in class_nodes if isinstance(node, URIRef) and not str(node).startswith(str(XSD))}
    mapping = {}
    for prefix, nodes in (("C", class_nodes), ("R", objects), ("D", data)):
        for node in sorted(nodes, key=str):
            # Punning is retained in RDF, but cannot be silently flattened to one project ID.
            if str(node) in mapping:
                raise ValueError(f"IRI used for multiple entity kinds: {node}")
            identity = prefix + "_" + hashlib.sha256(str(node).encode("utf-8")).hexdigest()[:20]
            if identity in mapping.values():
                raise ValueError("IRI/project ID hash collision")
            mapping[str(node)] = identity

    def labels(node, predicate):
        return [{"value": str(value), "language": value.language}
                for value in sorted(graph.objects(node, predicate), key=lambda x: x.n3())
                if isinstance(value, Literal)]

    def common(node):
        names = labels(node, RDFS.label) + labels(node, SKOS.prefLabel)
        definitions = labels(node, SKOS.definition) + labels(node, RDFS.comment)
        aliases = [row["value"] for row in labels(node, SKOS.altLabel)]
        label = next((row["value"] for row in names if row["language"] == "zh"),
                     names[0]["value"] if names else str(node).rsplit("#", 1)[-1].rsplit("/", 1)[-1])
        return {"id": mapping[str(node)], "iri": str(node), "label": label, "aliases": aliases,
                "labels": names, "definitions": definitions,
                "description": "\n".join(row["value"] for row in definitions)}

    classes = {}
    for node in sorted(class_nodes, key=str):
        item = common(node)
        parents = sorted(mapping[str(value)] for value in graph.objects(node, RDFS.subClassOf)
                         if isinstance(value, URIRef) and str(value) in mapping)
        item.update({"parents": parents, "parent": parents[0] if parents else None})
        classes[item["id"]] = item

    props = {"object_properties": {}, "data_properties": {}}
    for kind, nodes in (("object_properties", objects), ("data_properties", data)):
        for node in sorted(nodes, key=str):
            if not isinstance(node, URIRef):
                raise ValueError("anonymous properties cannot be mapped to stable project IDs")
            item = common(node)
            item["domain"] = sorted(mapping[str(value)] for value in graph.objects(node, RDFS.domain)
                                    if isinstance(value, URIRef) and str(value) in mapping)
            item["domain_semantics"] = "all"
            item["domain_expressions"] = sorted(value.n3() for value in graph.objects(node, RDFS.domain))
            item["range_expressions"] = sorted(value.n3() for value in graph.objects(node, RDFS.range))
            if kind == "object_properties":
                item["range"] = sorted(mapping[str(value)] for value in graph.objects(node, RDFS.range)
                                       if isinstance(value, URIRef) and str(value) in mapping)
                item["range_semantics"] = "all"
            else:
                item["datatypes"] = sorted(str(value) for value in graph.objects(node, RDFS.range)
                                           if isinstance(value, URIRef))
                item["datatype"] = item["datatypes"][0] if len(item["datatypes"]) == 1 else None
            props[kind][item["id"]] = item

    supported_datatypes = {str(XSD) + name for name in (
        "string", "boolean", "integer", "int", "long", "short", "byte", "nonNegativeInteger",
        "positiveInteger", "decimal", "double", "float", "date", "dateTime", "anyURI",
    )} | {str(RDF.langString)}
    issues = []
    for node in sorted(data, key=str):
        if len(set(graph.objects(node, RDFS.range))) > 1:
            issues.append({"subject": node.n3(), "predicate": str(RDFS.range), "object": "multiple_datatypes",
                           "status": "preserved_not_executed", "blocks_auto_review": True})
    annotations = {RDFS.label, RDFS.comment, RDFS.seeAlso, RDFS.isDefinedBy,
                   SKOS.prefLabel, SKOS.altLabel, SKOS.definition,
                   OWL.versionInfo, OWL.versionIRI, OWL.priorVersion, OWL.backwardCompatibleWith}
    annotations.update(graph.subjects(RDF.type, OWL.AnnotationProperty))
    declarations = {OWL.Class, RDFS.Class, OWL.ObjectProperty, OWL.DatatypeProperty,
                    OWL.Ontology, OWL.AnnotationProperty, OWL.NamedIndividual}
    instance_count = 0
    known_entities = class_nodes | objects | data
    for subject, predicate, value in sorted(graph, key=lambda row: tuple(x.n3() for x in row)):
        supported = predicate in annotations
        if predicate == RDF.type and value in declarations and isinstance(subject, URIRef):
            supported = True
        elif predicate == RDFS.subClassOf and subject in class_nodes and value in class_nodes:
            supported = True
        elif predicate in {RDFS.domain, RDFS.range} and subject in objects | data:
            supported = (isinstance(value, URIRef) and (
                value in class_nodes if predicate == RDFS.domain or subject in objects
                else str(value) in supported_datatypes))
        # Example ABox assertions are retained only in source, never extracted as facts.
        elif ((predicate == RDF.type and value in class_nodes and isinstance(subject, URIRef)
               and subject not in known_entities)
              or (predicate in objects | data and isinstance(subject, URIRef) and subject not in known_entities)):
            instance_count += 1
            supported = True
        if not supported:
            issues.append({"subject": subject.n3(), "predicate": str(predicate), "object": value.n3(),
                           "status": "preserved_not_executed", "blocks_auto_review": True})
    if OWL.Nothing in class_nodes:
        issues.append({"subject": OWL.Nothing.n3(), "predicate": str(RDF.type), "object": "bottom_class",
                       "status": "preserved_not_executed", "blocks_auto_review": True})

    # Conservative first profile: unsupported semantics block all automatic admission.
    # This also covers inherited restrictions, property chains and unresolved imports.
    profile = {
        "profile_version": "named_owl_application_v1", "constraint_semantics": "required_all",
        "interpretation": "named OWL domain/range used as application constraints; not OWL entailment",
        "executed_features": ["named_classes", "multiple_parents", "named_domain_range_all", "typed_literal"],
        "auto_review_blocked": bool(issues),
        "blocked_relation_ids": sorted(mapping[str(node)] for node in objects | data) if issues else [],
        "unsupported_axiom_count": len(issues), "reasoner": "not_run",
    }
    schema = {"schema_format": "owl_application_v1", "classes": classes, **props,
              "application_profile": profile}
    return {"project_schema": schema, "iri_id_map": mapping, "application_profile": profile,
            "mapping_report": {"format": "turtle", "errors": [], "unsupported_axioms": issues,
                               "example_assertion_count": instance_count,
                               "example_instances_imported_as_facts": False,
                               "semantic_fidelity": "limited_application_profile"}}
