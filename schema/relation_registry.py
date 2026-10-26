"""Shared exact ID/declared-alias lookup; similarity is never a confirmed mapping."""

from ontologyBuild.ontology_adapter import iter_object_properties, iter_datatype_properties, normalize_name


class RelationRegistry:
    def __init__(self, ontology):
        self.properties, self.aliases = {}, {}
        for kind, properties in (("object", iter_object_properties(ontology)), ("data", iter_datatype_properties(ontology))):
            for item in properties:
                identity = str(item["id"])
                self.properties[identity] = {**item, "property_kind": kind}
                for alias in [identity, item.get("label"), item.get("name"), *(item.get("aliases") or [])]:
                    if alias:
                        self.aliases.setdefault(normalize_name(alias), set()).add(identity)

    def resolve(self, surface):
        if surface in self.properties:
            return {"status": "mapped", "relation_id": surface, "matched_by": "standard_id"}
        matches = self.aliases.get(normalize_name(surface), set())
        if len(matches) == 1:
            return {"status": "mapped", "relation_id": next(iter(matches)), "matched_by": "declared_label_or_alias"}
        return {"status": "ambiguous" if matches else "needs_full_schema_lookup", "relation_id": None,
                "matched_ids": sorted(matches), "lookup_scope": "full_published_schema"}
