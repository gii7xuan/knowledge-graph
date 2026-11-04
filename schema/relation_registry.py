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

    def lookup(self, surface, *, head_type=None, tail_type=None, tail_kind='entity', guard=None):
        """Recall is not semantic confirmation; expose every competing definition."""
        ids = {surface} if surface in self.properties else self.aliases.get(normalize_name(surface),set())
        candidates = []
        for identity in sorted(ids):
            prop = self.properties[identity]
            constraints = guard.check({'triple':{'head_type':head_type,'tail_type':tail_type,
                'relation_id':identity,'tail_kind':tail_kind}}) if guard else {}
            definition = prop.get('definition') or prop.get('description') or prop.get('definitions')
            candidates.append({**prop,'definition_available':bool(definition),
                'constraint_check':constraints,'semantic_match_confirmed':False})
        compatible = [x for x in candidates if x['constraint_check'].get('status')!='fail']
        status = ('unassigned' if not ids else 'constraint_conflict' if not compatible else
                  'ambiguous' if len(compatible)>1 else 'matched' if surface in self.properties and
                  compatible[0]['definition_available'] and compatible[0]['constraint_check'].get('status')=='pass' else 'unassigned')
        return {'status':status,'relation_id':surface if status=='matched' else None,
                'matched_ids':sorted(ids),'candidates':candidates,'lookup_scope':'full_published_schema',
                'absence_confirmed':False,'semantic_review_required':True}

    def reference_catalog(self):
        """All property kinds, definitions and aliases, including explicit absences."""
        return {'properties':list(self.properties.values()), 'counts':{
            kind:sum(p['property_kind']==kind for p in self.properties.values()) for kind in ('object','data')},
            'full_published_schema':True}
