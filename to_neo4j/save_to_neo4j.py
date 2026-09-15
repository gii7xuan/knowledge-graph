import json
import re
import os
import hashlib
from glob import glob
from tqdm import tqdm
from neo4j import GraphDatabase

# ================= 配置区域 =================
# 1. Neo4j 配置
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

# 2. 数据源目录
INPUT_DIR = os.environ.get("KG_IMPORT_DIR", "runs/v2/accepted")  # 导入该目录下所有 JSON 文件

# ===========================================

class Neo4jImporter:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self._create_constraints()

    def _create_constraints(self):
        """
        创建唯一约束，防止节点重复
        在各个实体类别上创建唯一约束（如 C_Gun, C_Ammunition 等）
        """
        with self.driver.session() as session:
            try:
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_key IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (f:Fact) REQUIRE f.fact_key IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Evidence) REQUIRE e.evidence_key IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (s:Section) REQUIRE s.section_id IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.document_id IS UNIQUE")
                print("唯一约束已创建（或已存在）")
            except Exception as e:
                print(f"创建唯一约束时出错: {e}")

    def close(self):
        self.driver.close()

    def clean_relation_type(self, relation):
        """
        清洗关系名称，使其符合 Neo4j 的 Type 命名规范
        1. 替换非字母数字下划线的字符
        2. 如果清洗后为空，给默认值
        """
        # 替换括号、空格、标点为下划线
        clean_rel = re.sub(r'[^\w\u4e00-\u9fa5]+', '_', relation)
        clean_rel = clean_rel.strip('_')

        if not clean_rel:
            return "RELATED_TO"
        return clean_rel

    def make_fact_key(self, head, relation, tail):
        """同一事实只由 head/relation/tail 决定，不包含来源片段。"""
        raw = f"{self.normalize_entity_name(head)}\t{relation}\t{self.normalize_entity_name(tail)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def make_evidence_key(self, fact_key, source_id, evidence, start=None, end=None):
        """同一事实下的同一来源证据只保存一次。"""
        raw = f"{fact_key}\t{source_id}\t{start}\t{end}\t{evidence}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def normalize_entity_name(self, name):
        return "".join(str(name or "").split()).casefold()

    def make_entity_key(self, name, domain="firearm_artillery"):
        raw = f"{self.normalize_entity_name(name)}\t{domain}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def is_importable(self, item):
        if item.get("decision") in {"auto_accept", "human_accept"}:
            return True
        if item.get("review_status") == "accepted":
            return True
        if item.get("review_decision") in {"accept", "modify"}:
            return True
        return False

    def safe_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def json_text(self, value):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def clean_text(self, value):
        return str(value or "").strip()

    def get_existing_entity_names(self) -> set:
        """获取数据库中已存在的所有实体名称"""
        with self.driver.session() as session:
            result = session.run("MATCH (e:Entity) RETURN e.name AS name")
            return {record["name"] for record in result}

    def import_triplet(self, session, item):
        if not self.is_importable(item):
            return False

        head = self.clean_text(item.get('head'))
        tail = self.clean_text(item.get('tail'))
        raw_relation = self.clean_text(item.get('relation'))
        source_id = self.clean_text(item.get('source_id'))
        evidence = self.clean_text(item.get('evidence'))
        confidence = self.safe_float(item.get('confidence'))

        # 获取实体类别信息（用于 Neo4j 导入时的颜色区分）
        head_type = item.get('head_type', 'Unknown')
        tail_type = item.get('tail_type', 'Unknown')

        # 基础校验
        if not head or not tail or not raw_relation:
            return False

        # 关系名清洗
        safe_relation = self.clean_relation_type(raw_relation)

        head_key = self.make_entity_key(head)
        tail_key = self.make_entity_key(tail)
        fact_key = item.get("fact_key") or self.make_fact_key(head, raw_relation, tail)

        query = f"""
        MERGE (h:Entity {{entity_key: $head_key}})
        SET h.name = $head,
            h.type_id = $head_type,
            h.updated_at = datetime()
        MERGE (t:Entity {{entity_key: $tail_key}})
        SET t.name = $tail,
            t.type_id = $tail_type,
            t.updated_at = datetime()
        MERGE (f:Fact {{fact_key: $fact_key}})
        ON CREATE SET
            f.head = $head,
            f.relation = $raw_relation,
            f.tail = $tail,
            f.created_at = datetime()
        SET
            f.updated_at = datetime(),
            f.raw_score = $raw_score,
            f.calibrated_probability = $calibrated_probability,
            f.decision = $decision,
            f.calibrator_version = $calibrator_version,
            f.policy_version = $policy_version,
            f.best_confidence = CASE
                WHEN f.best_confidence IS NULL OR $confidence > f.best_confidence
                THEN $confidence
                ELSE f.best_confidence
            END,
            f.source_ids = CASE
                WHEN $source_id = '' OR $source_id IN coalesce(f.source_ids, [])
                THEN coalesce(f.source_ids, [])
                ELSE coalesce(f.source_ids, []) + [$source_id]
            END
        MERGE (h)-[:FACT_SUBJECT]->(f)
        MERGE (f)-[:FACT_OBJECT]->(t)
        MERGE (h)-[r:{safe_relation}]->(t)
        ON CREATE SET r.created_at = datetime()
        SET
            r.fact_key = $fact_key,
            r.surface_relation = $surface_relation,
            r.relation = $raw_relation,
            r.updated_at = datetime(),
            r.best_confidence = f.best_confidence
        """

        session.run(
            query,
            head_key=head_key,
            tail_key=tail_key,
            head=head,
            tail=tail,
            head_type=head_type,
            tail_type=tail_type,
            raw_relation=raw_relation,
            surface_relation=item.get("surface_relation", raw_relation),
            source_id=source_id,
            evidence=evidence,
            fact_key=fact_key,
            confidence=confidence,
            raw_score=self.safe_float(item.get("raw_score")),
            calibrated_probability=self.safe_float(item.get("calibrated_probability")),
            decision=item.get("decision"),
            calibrator_version=item.get("calibrator_version"),
            policy_version=item.get("policy_version"),
            confidence_detail_json=self.json_text(item.get('confidence_detail')),
            confidence_decision_json=self.json_text(item.get('confidence_decision')),
        )
        evidence_items = item.get("evidence_items") or []
        if not evidence_items and (source_id or evidence):
            evidence_items = [{"chunk_id": source_id, "text": evidence}]
        for evidence_item in evidence_items:
            if not isinstance(evidence_item, dict):
                continue
            self.import_evidence(session, fact_key, item, evidence_item)
        return True

    def import_evidence(self, session, fact_key, item, evidence_item):
        text = evidence_item.get("text", "") or item.get("evidence", "")
        chunk_id = evidence_item.get("chunk_id") or item.get("source_id", "")
        evidence_key = self.make_evidence_key(
            fact_key,
            chunk_id,
            text,
            evidence_item.get("start"),
            evidence_item.get("end"),
        )
        document_id = evidence_item.get("document_id") or item.get("document_id", "")
        breadcrumb_path = evidence_item.get("breadcrumb_path") or item.get("breadcrumb_path", "")
        section_id = hashlib.sha1(f"{document_id}\n{breadcrumb_path}".encode("utf-8")).hexdigest()[:12] if breadcrumb_path else ""
        query = """
        MATCH (f:Fact {fact_key: $fact_key})
        MERGE (e:Evidence {evidence_key: $evidence_key})
        ON CREATE SET e.created_at = datetime()
        SET
            e.updated_at = datetime(),
            e.document_id = $document_id,
            e.chunk_id = $chunk_id,
            e.sentence_id = $sentence_id,
            e.breadcrumb_path = $breadcrumb_path,
            e.text = $text,
            e.start = $start,
            e.end = $end,
            e.level = $level,
            e.retrieval_score = $retrieval_score,
            e.entailment_score = $entailment_score,
            e.contradiction_score = $contradiction_score,
            e.confidence = $confidence
        MERGE (f)-[:SUPPORTED_BY]->(e)
        WITH e
        WHERE $chunk_id <> ''
        MERGE (c:Chunk {chunk_id: $chunk_id})
        SET c.document_id = $document_id,
            c.breadcrumb_path = $breadcrumb_path
        MERGE (e)-[:FROM_CHUNK]->(c)
        WITH c
        WHERE $section_id <> ''
        MERGE (s:Section {section_id: $section_id})
        SET s.document_id = $document_id,
            s.breadcrumb_path = $breadcrumb_path
        MERGE (c)-[:BELONGS_TO]->(s)
        WITH s
        WHERE $document_id <> ''
        MERGE (d:Document {document_id: $document_id})
        MERGE (d)-[:HAS_SECTION]->(s)
        """
        session.run(
            query,
            fact_key=fact_key,
            evidence_key=evidence_key,
            document_id=document_id,
            chunk_id=chunk_id,
            sentence_id=evidence_item.get("sentence_id"),
            breadcrumb_path=breadcrumb_path,
            text=text,
            start=evidence_item.get("start"),
            end=evidence_item.get("end"),
            level=evidence_item.get("level"),
            retrieval_score=self.safe_float(evidence_item.get("retrieval_score")),
            entailment_score=self.safe_float(evidence_item.get("entailment_score")),
            contradiction_score=self.safe_float(evidence_item.get("contradiction_score")),
            section_id=section_id,
            confidence=self.safe_float(item.get("confidence")),
        )

    def import_data(self, triplets):
        print("正在连接 Neo4j 并写入数据...")

        # 获取已存在的实体名称
        existing_names = self.get_existing_entity_names()
        print(f"数据库中已存在 {len(existing_names)} 个实体")

        # 统计计数
        success_count = 0

        with self.driver.session() as session:
            for item in tqdm(triplets, desc="Importing"):
                try:
                    if self.import_triplet(session, item):
                        success_count += 1
                except Exception as e:
                    head = self.clean_text(item.get('head'))
                    tail = self.clean_text(item.get('tail'))
                    safe_relation = self.clean_relation_type(self.clean_text(item.get('relation')))
                    print(f"写入失败 [{head} -> {safe_relation} -> {tail}]: {e}")

        print(f"\n导入完成!成功导入或合并 {success_count} 条三元组事实。")


def main():
    # 检查目录是否存在
    if not os.path.exists(INPUT_DIR):
        print(f"错误：目录 {INPUT_DIR} 不存在。")
        return

    # 获取目录下所有 JSON 文件
    json_files = glob(os.path.join(INPUT_DIR, '*.json'))

    if not json_files:
        print(f"错误：在目录 {INPUT_DIR} 中没有找到 JSON 文件。")
        return

    print(f"找到 {len(json_files)} 个 JSON 文件待导入。")

    # 初始化数据库连接器
    importer = Neo4jImporter(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)

    total_success = 0
    total_triplets = 0

    try:
        # 遍历每个 JSON 文件
        for json_file in tqdm(json_files, desc="Processing Files"):
            try:
                # 读取 JSON 文件
                with open(json_file, 'r', encoding='utf-8') as f:
                    triplets = json.load(f)

                file_triplets = len(triplets)
                total_triplets += file_triplets

                # 导入数据
                file_success = 0
                with importer.driver.session() as session:
                    for item in triplets:
                        try:
                            if importer.import_triplet(session, item):
                                file_success += 1
                                total_success += 1
                        except Exception as e:
                            head = importer.clean_text(item.get('head'))
                            tail = importer.clean_text(item.get('tail'))
                            safe_relation = importer.clean_relation_type(importer.clean_text(item.get('relation')))
                            print(f"写入失败 [{head} -> {safe_relation} -> {tail}]: {e}")

                print(f"文件 [{os.path.basename(json_file)}] 导入完成: {file_success}/{file_triplets} 条三元组事实")

            except Exception as e:
                print(f"处理文件 {json_file} 时出错: {e}")
                continue

    finally:
        importer.close()

    print(f"\n所有文件导入完成!")
    print(f"共处理 {len(json_files)} 个文件, {total_triplets} 条三元组")
    print(f"成功导入或合并 {total_success} 条三元组事实到 Neo4j。")


if __name__ == "__main__":
    main()
