"""Publish the reviewed preview on the user's explicit instruction."""
import hashlib
import json
from pathlib import Path
from ontologyBuild.import_service import publish_draft
from ontologyBuild.release_service import load_release
from ontologyBuild.release_catalog import ReleaseCatalog

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'runs/ontology/20261027_chapter1_recovery'


def main():
    preview=json.loads((RUN/'chapter1_import_preview.json').read_text(encoding='utf-8'))
    source=json.loads((RUN/'chapter1_generation_report.json').read_text(encoding='utf-8'))['chapter_source']
    assert hashlib.sha256(Path(source['source_path']).read_bytes()).hexdigest()==source['source_raw_sha256']
    release=publish_draft(Path(preview['draft_path']),expected_artifact_digest=preview['artifact_digest'],
        expected_base_release_id=preview['base_release_id'],approved_by='user_via_codex_20261027',
        reason='用户明确要求发布第一章本体，并以该版本启动第一章候选抽取；保留未运行的领域语义审核及推理器状态。',
        release_root=ROOT/'runs/ontology_releases')
    verified=load_release(release.path)
    assert ReleaseCatalog(ROOT/'runs/ontology_releases').current().manifest['release_id']==verified.manifest['release_id']
    receipt={**verified.binding(),'status':'published','approved_by':'user_via_codex_20261027',
        'source_draft':preview['draft_path'],'source_artifact_digest':preview['artifact_digest'],
        'domain_semantic_review':'not_run','owl_reasoner':'not_run'}
    (RUN/'publication_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    readme=RUN/'README.md'
    marker='## 后续发布记录'
    body=readme.read_text(encoding='utf-8').split(marker)[0].rstrip()
    readme.write_text(body+'\n\n'+marker+'\n\n已按用户明确指令发布并激活。上面的待发布描述为生成时状态；发布详情见 [publication_receipt.json](publication_receipt.json)。\n'
        +'领域语义审核和推理器仍未运行，发布动作不改变这些验证状态。\n',encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
