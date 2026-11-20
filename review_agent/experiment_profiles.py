"""Versioned isolated experiment settings, shared by execution and reporting."""
from copy import deepcopy
from pathlib import Path

DIAGNOSTIC_TOOLS = ['inspect_context', 'search_evidence', 'inspect_ontology', 'lookup_review_memory',
                    'run_validation', 'recover_validation', 'propose_revision', 'review_revision',
                    'review_dispute', 'finish_review']
RESOLUTION_TOOLS = DIAGNOSTIC_TOOLS + ['apply_reviewed_revision', 'request_ontology_extension',
    'get_ontology_extension_status', 'propose_mapping', 'propose_split_revision', 'review_split_revision',
    'apply_reviewed_split', 'review_semantic_contract', 'assess_ontology_coverage']
VERSION = 'agent_experiment_profiles_v1'


def build_config(base, *, profile='diagnostic', directory=None):
    if profile not in {'diagnostic', 'agent_resolution', 'weighted_diagnostic','handoff_revision'}:
        raise ValueError('unknown experiment profile')
    config = deepcopy(base)
    resolution = profile in {'agent_resolution','handoff_revision'}
    config.update(mode='shadow', validator_contract_version='validator_response_v8')
    if directory is not None:
        directory = Path(directory).resolve()
        config.update(database_path=str(directory / 'review.sqlite3'), request_cache_namespace=directory.name)
    config['planner'] = {**config.get('planner', {}), 'enabled': True, 'strategy': 'llm_react',
                         'durable_requests':resolution,
                         'allowed_tools': list(RESOLUTION_TOOLS if resolution else DIAGNOSTIC_TOOLS)}
    config['budget'] = {**config.get('budget', {}), 'max_total_tokens': 360000,
        'total_timeout_seconds': 420, 'max_llm_calls_per_candidate': 18, 'max_planner_steps': 3,
        'max_validation_rounds': 1, 'max_tool_calls': 8, 'max_parallel_candidates': 2}
    config['case_budget'] = {**config.get('case_budget', {}), 'tokens': 400000}
    config['memory'] = {**config.get('memory', {}), 'enabled': False, 'scope': {'mode': 'disabled'}}
    config['ontology_extension'] = {**config.get('ontology_extension', {}), 'enabled': resolution,
                                    'auto_publish_enabled': False}
    config['routing'] = {**config.get('routing', {}), 'auto_accept_enabled': False,
                          'semantic_auto_reject_enabled': False}
    config['neo4j'] = {**config.get('neo4j', {}), 'auto_import_enabled': False}
    if profile == 'weighted_diagnostic':
        from .vote_weighting import normalize_policy
        config['vote_weighting']=normalize_policy({'mode':'dynamic'},int(config['consensus']['committee_size']))
        config['agent_resolution']={'enabled':False}
        config['planner'].update(durable_requests=True,compact_view=True,allowed_tools=[t for t in DIAGNOSTIC_TOOLS
            if t not in {'propose_revision','review_revision','lookup_review_memory'}])
        config['budget'].update(max_planner_steps=4,max_llm_calls_per_candidate=8,max_retries_per_request=0,
            max_total_tokens=100000,total_timeout_seconds=240,per_call_timeout_seconds=90,max_parallel_candidates=1)
        config['case_budget'].update(llm_calls=8,tokens=100000,tool_calls=8)
        config['experiment_profile']={'version':VERSION,'name':profile,'isolated_only':True}
    if resolution:
        config['experiment_profile'] = {'version': VERSION, 'name': profile, 'isolated_only': True}
        config['agent_resolution'] = {'enabled': True, 'version': 'agent_resolution_v1',
            'max_attempts_per_issue': 2, 'max_semantic_revisions': 1, 'reserve_final_committee_calls': 3,
            'max_semantic_contract_reviews_per_seat': 1, 'automatic_production_writes': False}
        config['planner'].update(durable_requests=True, compact_view=True, state_driven_steps=True)
        config['mapping_preflight']={'enabled':True}
        config['dispute_review'] = {**config.get('dispute_review', {}), 'enabled': True,
                                    'resolution_snapshots': True}
        # Includes initial ballot, bounded focused/revision reviews and the child's full ballot.
        # The case account spans every revision; it is the hard limit, not a per-step reset.
        config['budget'].update(max_planner_steps=8, max_tool_calls=16, max_validation_rounds=2,
            max_llm_calls_per_candidate=48, max_total_tokens=800000, total_timeout_seconds=1200)
        config['case_budget'].update(llm_calls=64, tokens=1200000, tool_calls=40)
    if profile=='handoff_revision':
        from .vote_weighting import normalize_policy
        config['vote_weighting']=normalize_policy({'mode':'dynamic'},int(config.get('consensus',{}).get('committee_size',3)))
        config['response_correction']={'max_corrections_per_seat':0,'max_calls_per_candidate_version':0}
        config['semantic_clarification']={'enabled':False}
        config['planner'].update(stream_responses=True,request_timeout_seconds=180,http_timeout_seconds=180)
        for slot in config.get('validators',[]):slot['stream_responses']=True
        config['mapping_preflight']={'enabled':True,'revision_proposals':True}
        config['experiment_profile']['version']='handoff_revision_profile_v2'
        config['planner']['allowed_tools']=[t for t in RESOLUTION_TOOLS if t not in {
            'request_ontology_extension','get_ontology_extension_status','propose_split_revision',
            'review_split_revision','apply_reviewed_split'}]
        config['ontology_extension'].update(enabled=False,auto_publish_enabled=False)
        config['dispute_review']['scope_review']=True
        config['agent_resolution'].update(version='handoff_revision_policy_v2',qualifier_grounding_required=True,
            expression_grounding_required=True,expression_revision_contract='grounded_expression_revision_v1')
        config['agent_resolution']['reserve_final_committee_calls']=int(config.get('consensus',{}).get('committee_size',3))
        config['budget'].update(max_llm_calls_per_candidate=24,max_tool_calls=24,max_retries_per_request=0,
            per_call_timeout_seconds=180,planner_timeout_seconds=180,
            max_focused_input_bytes=262144,focused_review_timeout_seconds=180)
        config['case_budget'].update(llm_calls=32,tool_calls=48)
    return config


def describe(config):
    resolution = bool(config.get('agent_resolution', {}).get('enabled'))
    from .dispute_review import SCOPE_VERSION,SCOPE_PROMPT,SCOPE_SCHEMA
    from .planner_contracts import argument_schemas
    from .contracts import sha256_json
    result={'version': (config.get('experiment_profile') or {}).get('version',VERSION), 'name': (config.get('experiment_profile') or {}).get('name','agent_resolution' if resolution else 'diagnostic'),
        'planner_execution':{k:bool(config['planner'].get(k)) for k in ('durable_requests','compact_view','state_driven_steps')},
        'effective_tools': list(config['planner']['allowed_tools']), 'budget': deepcopy(config['budget']),
        'case_budget': deepcopy(config['case_budget']), 'resolution_policy': deepcopy(config.get('agent_resolution')),
        'candidate_revisions_automatically_applied': resolution,
        'ontology_extension_execution_enabled': bool(config.get('ontology_extension', {}).get('enabled')),
        'ontology_publication_enabled': False, 'graph_import_enabled': False}
    if (config.get('experiment_profile') or {}).get('name')=='handoff_revision':
        result.update(scope_review_contract={'version':SCOPE_VERSION,'hash':sha256_json([SCOPE_PROMPT,SCOPE_SCHEMA])},
            action_contract_hash=sha256_json(argument_schemas(True,True)))
    return result
