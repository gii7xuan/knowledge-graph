"""Deterministic extension IRIs whose project IDs also survive fresh imports."""
import hashlib
from .ontology_adapter import normalize_name
from review_agent.contracts import sha256_json


def owl_extension_identity(base_release_id,kind,label):
    prefix={'add_class':'C','add_object_property':'R','add_data_property':'D'}[kind]
    seed=sha256_json([base_release_id,kind,normalize_name(label)])
    iri='urn:kg-ontology:extension:'+kind+':'+seed
    return prefix+'_'+hashlib.sha256(iri.encode('utf-8')).hexdigest()[:20],iri
