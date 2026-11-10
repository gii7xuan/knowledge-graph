"""Permissions and minimum obligations remain outside the planner's authority."""

from .contracts import sha256_json
from .planner_contracts import ActionError, TOOL_SCHEMAS, validate_action, argument_schemas


class ActionPolicy:
    def __init__(self, config):
        self.config = config
        self.allowed = set(config.get("allowed_tools", TOOL_SCHEMAS))
        if config.get('enable_dispute_review'):self.allowed.add('review_dispute')
        if self.allowed - (set(TOOL_SCHEMAS)|({'review_dispute'} if config.get('enable_dispute_review') else set())):
            raise ValueError("planner whitelist contains unregistered tools")

    def definitions(self):
        return {k: v for k, v in argument_schemas(bool(self.config.get('enable_dispute_review'))).items() if k in self.allowed}

    @staticmethod
    def blocked_tools(state):
        available={i['evidence_id'] for i in state.archive if i.get('anchor_valid') is True}
        validation=state.current_validation()
        blocked={}
        if validation is not None:
            blocked['run_validation']='current frozen input already validated; consume results, revise, search or recover failed seats'
        if not state.validation_recovery.get('recoverable_voter_ids'):
            blocked['recover_validation']='no safely recoverable failed seats; unknown requests require reconciliation'
        if not any(d.get('status')=='proposed' or d.get('status')=='review_incomplete' and d.get('review_recovery_available') for d in state.revision_drafts):
            blocked['review_revision']='no current proposed revision draft'
        if not any(d.get('status')=='approved' for d in state.revision_drafts):
            blocked['apply_reviewed_revision']='no independently approved revision draft'
        if not available: blocked['inspect_context']='no verified evidence ID; search_evidence first'
        if not state.ontology_checked:
            blocked['run_validation']='inspect_ontology must check the full frozen ontology first'
            blocked['finish_review']='inspect_ontology must run before finishing'
        elif not state.bundle.get('items'):
            blocked['run_validation']='no verified evidence; use search_evidence'
            if not state.searched_scopes:
                blocked['finish_review']='search_evidence before handing off an evidence-less candidate'
        elif validation is None:
            blocked['finish_review']='current independent validation is required; call run_validation'
        if state.context.get('revision_only'):
            blocked['run_validation']='revision investigation uses historical observations; new revision gets final independent review'
            if state.ontology_checked: blocked.pop('finish_review',None)
        baseline=state.context.get('dispute_baseline')
        if baseline:
            blocked['run_validation']='initial ballot is preserved; supplement context and review only the frozen dispute, or hand off'
            if state.ontology_checked:blocked.pop('finish_review',None)
        if not baseline or not any(d['status']=='open' for d in (state.context.get('dispute_report') or {}).get('disputes',[])):
            blocked['review_dispute']='no open claim-level disagreement from the initial ballot'
        from .extension_eligibility import extension_eligibility
        eligibility=extension_eligibility(state)
        if not eligibility['allowed_change_kinds']:
            blocked['request_ontology_extension']='; '.join(sorted(set(eligibility['blocked_change_kinds'].values())))
        if not state.pending_extension_id:
            blocked['get_ontology_extension_status']='no subscribed ontology extension task'
        return blocked

    def validate_request(self, action, state):
        validate_action(action)
        if action['action'] not in self.allowed: raise ActionError('tool not permitted by execution policy')
        args=action['arguments']
        available={x['evidence_id'] for x in state.archive if x.get('anchor_valid') is True}
        cited=args.get('evidence_ids',[])+([args['evidence_id']] if 'evidence_id' in args else [])
        if set(cited)-available:
            raise ActionError('evidence reference is outside current verified source; select exact valid_evidence_ids')
        reason=self.blocked_tools(state).get(action['action'])
        if reason: raise ActionError(reason)
        if action['action']=='review_dispute':
            report=state.context.get('dispute_report') or {}
            if not any(d['dispute_id']==args['dispute_id'] and d['status']=='open' for d in report.get('disputes',[])):
                raise ActionError('select an exact open dispute from the initial ballot')
        if action['action']=='get_ontology_extension_status' and args['extension_id']!=state.pending_extension_id:
            raise ActionError('extension_id differs from the subscribed task')
        if action['action'] in {'review_revision','apply_reviewed_revision'}:
            draft=next((d for d in state.revision_drafts if d['draft_id']==args['draft_id']),None)
            if not draft or draft.get('base_revision')!=state.candidate['revision']:
                raise ActionError('draft does not belong to the current candidate revision')
            if action['action']=='apply_reviewed_revision' and args['draft_hash']!=draft['draft_hash']:
                raise ActionError('draft_hash differs from approved draft')
        if action['action']=='request_ontology_extension':
            from .extension_eligibility import validate_extension
            validate_extension(state,args['change_kind'],args['proposed_changes'])
            validation=state.current_validation()
            eligible=set(validation['consensus'].get('eligible_voter_ids',[]))
            supported={eid for vote in validation.get('votes',[]) if vote.get('voter_id') in eligible
                       for eid in vote.get('supporting_evidence_ids',[])}
            if not args['evidence_ids'] or not set(args['evidence_ids'])<=supported:
                raise ActionError('extension evidence_ids must select current source_supported_evidence_ids')
        return action

    def check(self, action, state):
        self.validate_request(action,state)
        args = action["arguments"]
        available = {x["evidence_id"] for x in state.archive if x.get("anchor_valid") is True}
        cited = args.get("evidence_ids", []) + ([args["evidence_id"]] if "evidence_id" in args else [])
        if set(cited) - available:
            raise ActionError("evidence reference is outside current verified source")
        fingerprint = sha256_json({"tool": action["action"], "arguments": args,
            "document_version": state.candidate["document_version"],
            "query_state": state.validation_key() if action["action"] in {"search_evidence", "run_validation"} else None})
        repeats = state.fingerprints.get(fingerprint, 0)
        if repeats >= int(self.config.get("max_repeated_actions", 2)):
            raise ActionError("repeated action without information gain")
        state.fingerprints[fingerprint] = repeats + 1
        return fingerprint

    def final_gaps(self, state):
        gaps = dict(state.gaps)
        if not state.ontology_checked:
            gaps["ontology_not_checked"] = True
        validation = state.current_validation()
        gaps["validation_missing"] = validation is None
        gaps["version_stale"] = bool(state.validations and validation is None)
        unresolved = [i for i in state.issues if i.get("status", "open") != "resolved"]
        gaps["critical_issues_unresolved"] = any(i.get("critical") for i in unresolved)
        selected = {i["evidence_id"] for i in state.bundle.get("items", [])}
        gaps["protected_evidence_omitted"] = bool(set(state.protected_ids) - selected - set(state.evidence_exclusions))
        material = {x["evidence_id"] for x in state.archive if x.get("review_obligation")}
        material.update(state.protected_ids)
        gaps["unreviewed_discovered_evidence"] = bool(material - selected - set(state.evidence_exclusions))
        gaps["unresolved_counterevidence"] = any(i["code"] in {"source_conflict", "counterevidence"} for i in unresolved)
        gaps["unresolved_support_conflict"] = gaps["unresolved_counterevidence"]
        gaps["need_more_evidence"] = any(i["code"] in {"EVIDENCE_INSUFFICIENT", "source_conflict", "counterevidence"} for i in unresolved)
        return gaps
