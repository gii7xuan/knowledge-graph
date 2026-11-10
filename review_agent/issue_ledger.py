"""Server-owned issue lifecycle; planner prose cannot resolve an obligation."""

from .contracts import sha256_json
import copy

REVISION_ISSUES = {"CONDITION_MISSING", "QUALIFIER_MISMATCH", "DIRECTION_MISMATCH", "ENTITY_MISMATCH", "RELATION_MISMATCH", "revision_proposed"}
SOURCE_ISSUES = {"EVIDENCE_INSUFFICIENT", "source_conflict", "counterevidence", "unverified_source"}


class IssueLedger:
    def __init__(self, state):
        self.state = state

    def origin_revision(self,issue,repository,fallback):
        origin=issue.get('opened_revision',issue.get('inherited_from_revision',fallback))
        with repository.connect() as c:
            row=c.execute("SELECT MIN(COALESCE(json_extract(e.event_json,'$.revision'),a.revision)) FROM issue_events e JOIN audit_runs a ON a.audit_id=e.audit_id WHERE a.candidate_id=? AND e.issue_id=? AND json_extract(e.event_json,'$.action')='opened'",
                (self.state.candidate['candidate_id'],issue['issue_id'])).fetchone()
            if not row or row[0] is None:
                row=c.execute("SELECT MIN(a.revision) FROM audit_runs a,json_each(a.context_json,'$.issue_history') i WHERE a.candidate_id=? AND json_extract(i.value,'$.issue_id')=?",
                    (self.state.candidate['candidate_id'],issue['issue_id'])).fetchone()
        if row and row[0] is not None: origin=min(origin,int(row[0]))
        issue['opened_revision']=origin
        return origin

    def inherit(self, repository):
        """A new audit cannot erase revision-bound obligations or their evidence."""
        from .response_contract import CONTRACT_VERSION
        self.inherit_reviewed_revision_evidence(repository)
        prior = repository.previous_revision_audit(self.state.candidate["candidate_id"], self.state.candidate["revision"], self.state.audit_id)
        if not prior:
            return
        saved = (prior.get("agent_state") or {}).get("state", {})
        counters={eid for vote in prior.get('votes',[]) if vote.get('response_status')=='ok' for eid in vote.get('counter_evidence_ids',[])}
        carried = [i for i in prior["context"].get("issue_history", saved.get("issues", []))
                   if i.get("status", "open") != "resolved" and i["code"] in REVISION_ISSUES | SOURCE_ISSUES | {"ontology_gap"}]
        for issue in copy.deepcopy(carried):
            self.origin_revision(issue,repository,prior['revision'])
            issue["inherited_from_audit"] = prior["audit_id"]
            issue['inherited_from_revision']=prior['revision']
            self.state.issues.append(issue)
            self.event(issue, "inherited", source_audit_id=prior["audit_id"])
        if carried or counters:
            archive = [*self.state.archive, *list(saved.get("archive") or [])]
            for proposal in prior.get('rounds',[]):
                archive.extend(proposal.get('evidence_bundle',{}).get('items',[]))
            archive=list({e['evidence_id']:e for e in archive}.values())
            self.state.archive = copy.deepcopy(archive)
            self.state.protected_ids = sorted(set(self.state.protected_ids) | set(saved.get("protected_ids", [])) |
                counters | {eid for issue in carried for eid in issue.get("evidence_ids", [])})
        if counters:
            self.open('counterevidence','先前版本的反证需要在当前表达下重新核验',True,evidence_ids=counters)

    def inherit_reviewed_revision_evidence(self, repository):
        """Carry the exact applied draft's verified evidence into its fresh review."""
        split=self.state.candidate.get('split_origin') or {}
        if split.get('draft_id'):
            from .revision_service import RevisionService,revision_expression
            from .repository import ConflictError
            draft=RevisionService(repository).draft(split['draft_id'])
            ancestor=repository.get_revision(self.state.candidate['candidate_id'],1)
            if (draft['status']!='applied' or draft['draft_hash']!=split.get('draft_hash') or
                sha256_json({k:v for k,v in draft.items() if k not in {'draft_hash','status','review_result'}})!=draft['draft_hash'] or
                draft.get('operation')!='split_child' or draft['after']['candidate_id']!=ancestor['candidate_id'] or
                revision_expression(draft['after'])!=revision_expression(ancestor)):
                raise ConflictError('split source provenance does not match the independently reviewed child')
            self.state.archive=list({e['evidence_id']:copy.deepcopy(e) for e in [*self.state.archive,*draft['evidence_bundle']['items']]}.values())
            self.state.protected_ids=sorted(set(self.state.protected_ids)|set(draft['protected_evidence_ids'])|set(draft['evidence_ids']))
            self.state.context['split_evidence_provenance']={'group_id':split['group_id'],'draft_id':draft['draft_id'],
                'draft_hash':draft['draft_hash'],'parent_votes_reused':False,'each_child_requires_new_review':True}
        provenance=self.state.candidate.get('revision_provenance') or {}
        if not provenance.get('draft_id'):return
        from .revision_service import RevisionService
        from .repository import ConflictError
        from .contracts import canonical_triple,normalize_fact_part
        draft=RevisionService(repository).draft(provenance['draft_id'])
        source_revision=draft['base_revision']+1
        ancestor=repository.get_revision(self.state.candidate['candidate_id'],source_revision)
        original={k:v for k,v in draft.items() if k not in {'draft_hash','status','review_result'}}
        if (draft['status']!='applied' or draft['draft_hash']!=provenance.get('draft_hash')
                or sha256_json(original)!=draft['draft_hash']
                or draft['candidate_id']!=self.state.candidate['candidate_id']
                or source_revision>self.state.candidate['revision']
                or (ancestor.get('revision_provenance') or {}).get('draft_hash')!=draft['draft_hash']
                or canonical_triple(draft['after'])!=canonical_triple(ancestor)
                or any(draft['after'].get(k)!=ancestor.get(k) for k in
                    ('document_version','source_index_hash','ontology_version','ontology_release_id','profile_hash'))
                or any(ancestor.get(k)!=self.state.candidate.get(k) for k in ('document_version','source_index_hash'))):
            raise ConflictError('applied revision evidence provenance does not match current candidate')
        items=draft['evidence_bundle']['items']
        self.state.archive=list({e['evidence_id']:copy.deepcopy(e) for e in [*self.state.archive,*items]}.values())
        self.state.protected_ids=sorted(set(self.state.protected_ids)|set(draft['protected_evidence_ids'])|set(draft['evidence_ids']))
        self.state.context['revision_evidence_provenance']={'draft_id':draft['draft_id'],'draft_hash':draft['draft_hash'],
            'source_revision':source_revision,'evidence_hash':draft['evidence_bundle']['evidence_hash'],
            'supporting_evidence_ids':draft['evidence_ids'],'prior_approval_reused_for_current_revision':False,
            'expression_changed':({**canonical_triple(draft['before']),'surface_relation':normalize_fact_part(draft['before']['triple'].get('surface_relation'))}!=
                                  {**canonical_triple(draft['after']),'surface_relation':normalize_fact_part(draft['after']['triple'].get('surface_relation'))})}

    def event(self, issue, action, proposal=None, **details):
        event = {"issue_id": issue["issue_id"], "action": action, "proposal_digest": proposal,
                 "candidate_id": self.state.candidate["candidate_id"], "revision": self.state.candidate["revision"],
                 **details, "sequence": len(self.state.issue_events)}
        event["event_id"] = sha256_json([self.state.audit_id, event])
        self.state.issue_events.append(event)

    def open(self, code, description, critical=False, *, proposal=None, evidence_ids=(), evidence_hash=None):
        existing = next((i for i in self.state.issues if i["code"] == code and i.get("status", "open") != "resolved"), None)
        if existing:
            existing["critical"] = bool(existing.get("critical") or critical)
            existing["evidence_ids"] = sorted(set(existing.get("evidence_ids", [])) | set(evidence_ids))
            return existing
        issue = {"issue_id": "issue_" + sha256_json([self.state.audit_id, code, len(self.state.issues)])[:24],
                 "code": code, "description": description, "status": "open", "critical": critical,
                 "opened_in_proposal": proposal, "opened_evidence_hash": evidence_hash,
                 'opened_revision':self.state.candidate['revision'],
                 "evidence_ids": sorted(set(evidence_ids)), "resolution_evidence_ids": [],
                 "resolved_in_proposal": None, "resolution_rule": None,
                 "requires_revision": code in REVISION_ISSUES}
        self.state.issues.append(issue)
        self.event(issue, "opened", proposal)
        return issue

    def resolve(self, issue, proposal, rule, evidence_ids):
        policy=getattr(self.state,'context',{}).get('experimental_policy',{})
        if policy.get('no_issue_resolution') or (policy.get('no_counterevidence_resolution') and issue['code'] in {'source_conflict','counterevidence'}):
            self.event(issue,'resolution_disabled_by_experiment',proposal['proposal_digest'],rule=rule)
            return
        issue.update(status="resolved", resolved_in_proposal=proposal["proposal_digest"],
                     resolution_rule=rule, resolution_evidence_ids=sorted(set(evidence_ids)))
        self.event(issue, "resolved", proposal["proposal_digest"], rule=rule, evidence_ids=issue["resolution_evidence_ids"])

    def observe(self, proposal, votes, consensus, required_votes):
        eligible = set(consensus.get("eligible_voter_ids", []))
        valid = [v for v in votes if v.get("voter_id") in eligible]
        digest, evidence_hash = proposal["proposal_digest"], proposal["evidence_bundle"]["evidence_hash"]
        counter_ids = {eid for vote in valid for eid in vote.get("counter_evidence_ids", [])}
        source = consensus.get("source_support_consensus", {}).get("result")
        if counter_ids and (any(v.get("source_support") == "supported" for v in valid)
                            or source != "contradicted"):
            self.open("source_conflict", "存在需要独立复核适用范围的反证", True, proposal=digest,
                      evidence_ids=counter_ids | {eid for v in valid for eid in v.get("supporting_evidence_ids", [])}, evidence_hash=evidence_hash)
        for vote in valid:
            if proposal.get('response_contract_version')in {'validator_response_v5','validator_response_v5_1','validator_response_v5_2','validator_response_v6','validator_response_v6_1','validator_response_v6_2','validator_response_v6_3','validator_response_v7','validator_response_v8'}:
                for check in vote.get('checks',[]):
                    if proposal.get('response_contract_version') in {'validator_response_v7','validator_response_v8'} and check.get('check') in vote.get('representation_checks', []):
                        continue
                    if check.get('status')=='fail' and check.get('check') in {'entity_reference','relation_semantics','qualifiers','relation_direction'}:
                        code={'entity_reference':'ENTITY_MISMATCH','relation_semantics':'RELATION_MISMATCH',
                              'qualifiers':'QUALIFIER_MISMATCH','relation_direction':'DIRECTION_MISMATCH'}[check['check']]
                        self.open(code,check.get('note') or '表达须修订',True,proposal=digest,
                            evidence_ids=check.get('evidence_ids') or [],evidence_hash=evidence_hash)
            for code in set(vote.get("reason_codes", [])) & (REVISION_ISSUES | {"EVIDENCE_INSUFFICIENT"}):
                self.open(code, "当前候选存在来源、限定或方向义务", True, proposal=digest,
                          evidence_ids=vote.get("counter_evidence_ids") or vote.get("supporting_evidence_ids") or [], evidence_hash=evidence_hash)
            if vote.get("source_support") == "insufficient":
                self.open("EVIDENCE_INSUFFICIENT", "当前原文不足，需定向补证", True, proposal=digest,
                          evidence_ids=[item["evidence_id"] for item in proposal["evidence_bundle"]["items"]], evidence_hash=evidence_hash)
        if source in {"supported", "contradicted"}:
            supporting = ([v for v in valid if v["voter_id"] in consensus["source_support_consensus"]["support_voter_ids"]]
                          if source == "supported" else [v for v in valid if v.get("source_support") == "contradicted"])
            source_ids = {eid for vote in supporting for eid in [*vote.get("supporting_evidence_ids", []), *vote.get("counter_evidence_ids", [])]}
            for issue in self.state.issues:
                if issue.get("status", "open") == "resolved": continue
                if issue.get('requires_revision'):
                    revised=issue.get('opened_revision',issue.get('inherited_from_revision',self.state.candidate['revision']))<self.state.candidate['revision']
                    checks_ready=all(all(any(c['check']==name and c['status'] in {'pass','not_applicable'} and c['evidence_ids']
                        for c in vote.get('checks',[])) for name in ('triple_support','qualifiers','relation_direction')) for vote in supporting)
                    current_claim=any(issue['code'] in vote.get('reason_codes',[]) for vote in valid)
                    if revised and source=='supported' and len(supporting)>=required_votes and checks_ready and not current_claim:
                        self.resolve(issue,proposal,'independent_new_revision_expression_recheck',source_ids)
                    continue
                if issue['code']=='ontology_gap' and proposal.get('ontology_check',{}).get('status')=='pass' and len(supporting)>=required_votes:
                    self.resolve(issue,proposal,'independent_current_schema_recheck',source_ids)
                if (issue["code"] == "EVIDENCE_INSUFFICIENT" and issue.get("opened_evidence_hash") != evidence_hash
                        and {i["evidence_id"] for i in proposal["evidence_bundle"]["items"]} - set(issue.get("evidence_ids", []))):
                    self.resolve(issue, proposal, "new_evidence_independent_source_recheck", source_ids)
                if issue['code']=='EVIDENCE_INSUFFICIENT' and issue.get('status','open')!='resolved':
                    provenance=self.state.context.get('revision_evidence_provenance') or {}
                    revision_provenance=self.state.candidate.get('revision_provenance') or {}
                    grounded=all(all(any(c['check']==name and c['status'] in {'pass','not_applicable'}
                        and c['evidence_ids'] for c in vote.get('checks',[])) for name in
                        ('triple_support','qualifiers','relation_direction')) for vote in supporting)
                    old_expression=issue.get('opened_revision',self.state.candidate['revision'])<self.state.candidate['revision']
                    verified_revision=(bool(revision_provenance.get('draft_id') and revision_provenance.get('draft_hash')) and
                        provenance.get('expression_changed') is True and
                        provenance.get('source_revision')==self.state.candidate['revision'] and
                        provenance.get('draft_id')==revision_provenance.get('draft_id') and
                        provenance.get('draft_hash')==revision_provenance.get('draft_hash') and
                        set(provenance.get('supporting_evidence_ids',[]))<=
                            {item['evidence_id'] for item in proposal['evidence_bundle']['items']})
                    if (source=='supported' and old_expression and verified_revision and grounded
                            and len(supporting)>=required_votes and
                            not any({'EVIDENCE_INSUFFICIENT','SOURCE_CONTRADICTS'} & set(vote.get('reason_codes',[])) for vote in valid)):
                        self.resolve(issue,proposal,'independent_new_revision_source_recheck',source_ids)
                if issue["code"] in {"source_conflict", "counterevidence"}:
                    protected = set(issue.get("evidence_ids", []))
                    reviews = [vote for vote in supporting if any(c["check"] == "counterevidence"
                        and c["status"] in {"pass", "not_applicable"} and protected <= set(c["evidence_ids"])
                        for c in vote.get("checks", []))]
                    if protected and len(reviews) >= required_votes:
                        self.resolve(issue, proposal, "independent_current_counterevidence_recheck", protected | source_ids)
        # Relevance exclusions require independent quorum and retain their history.
        current_material = counter_ids | {eid for v in valid for eid in v.get("supporting_evidence_ids", [])}
        for eid in set(self.state.evidence_exclusions) & current_material:
            prior = self.state.evidence_exclusions.pop(eid)
            self.event({"issue_id": "relevance_" + eid}, "exclusion_revoked", digest, evidence_id=eid, prior=prior)
        for item in proposal["evidence_bundle"]["items"]:
            eid = item["evidence_id"]
            assessments = [a for vote in valid for a in vote.get("evidence_assessments", []) if a["evidence_id"] == eid]
            irrelevant_voters = {v["voter_id"] for v in valid if any(a["evidence_id"] == eid and a["role"] == "irrelevant" for a in v.get("evidence_assessments", []))}
            if len(irrelevant_voters) >= required_votes and all(a["role"] == "irrelevant" for a in assessments) and eid not in counter_ids:
                if eid not in current_material:
                    record = {"proposal_digest": digest, "voter_ids": sorted(irrelevant_voters),
                              "rule": "independent_irrelevance_agreement", "assessments": assessments}
                    if self.state.evidence_exclusions.get(eid) != record:
                        self.state.evidence_exclusions[eid] = record
                        self.event({"issue_id": "relevance_" + eid}, "evidence_excluded", digest, evidence_id=eid, **record)

    def unresolved(self):
        return [issue for issue in self.state.issues if issue.get("status", "open") != "resolved"]
