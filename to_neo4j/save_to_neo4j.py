import json
import re
import os
import hashlib
from glob import glob
from tqdm import tqdm
from neo4j import GraphDatabase

# ================= 配置区域 =================
# 1. Neo4j 配置
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "92832400"

# 2. 数据源目录
INPUT_DIR = 'triplets'  # 导入该目录下所有 JSON 文件

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
                # 创建唯一约束，确保每个实体类别的 name 属性唯一
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_Gun) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_Ammunition) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_ComponentMechanism) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_LoadCondition) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_FiringEffect) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_FiringTable) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_CompensatingDevice) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_CoolingDevice) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_BreechBlock) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_RecoilDevice) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_Barrel) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_Carriage) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_Shield) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:C_FiringPlatform) REQUIRE c.name IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (f:Fact) REQUIRE f.fact_key IS UNIQUE")
                session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Evidence) REQUIRE e.evidence_key IS UNIQUE")
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
        raw = f"{head}\t{relation}\t{tail}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def make_evidence_key(self, fact_key, source_id, evidence):
        """同一事实下的同一来源证据只保存一次。"""
        raw = f"{fact_key}\t{source_id}\t{evidence}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def safe_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def json_text(self, value):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def get_existing_entity_names(self) -> set:
        """获取数据库中已存在的所有实体名称"""
        with self.driver.session() as session:
            result = session.run("MATCH (c:C_Gun) RETURN c.name AS name UNION MATCH (c:C_Ammunition) RETURN c.name AS name UNION MATCH (c:C_ComponentMechanism) RETURN c.name AS name")
            return {record["name"] for record in result}

    def import_triplet(self, session, item):
        head = item.get('head', '').strip()
        tail = item.get('tail', '').strip()
        raw_relation = item.get('relation', '').strip()
        source_id = item.get('source_id', '').strip()
        evidence = item.get('evidence', '').strip()
        confidence = self.safe_float(item.get('confidence'))

        # 获取实体类别信息（用于 Neo4j 导入时的颜色区分）
        head_type = item.get('head_type', 'Unknown')
        tail_type = item.get('tail_type', 'Unknown')

        # 基础校验
        if not head or not tail or not raw_relation:
            return False

        # 关系名清洗
        safe_relation = self.clean_relation_type(raw_relation)

        # 使用 head_type 和 tail_type 作为节点标签
        head_label = head_type if head_type and head_type != 'Unknown' else 'Entity'
        tail_label = tail_type if tail_type and tail_type != 'Unknown' else 'Entity'
        fact_key = self.make_fact_key(head, raw_relation, tail)
        evidence_key = self.make_evidence_key(fact_key, source_id, evidence)

        query = f"""
        MERGE (h:{head_label} {{name: $head}})
        MERGE (t:{tail_label} {{name: $tail}})
        MERGE (f:Fact {{fact_key: $fact_key}})
        ON CREATE SET
            f.head = $head,
            f.relation = $raw_relation,
            f.tail = $tail,
            f.created_at = datetime()
        SET
            f.updated_at = datetime(),
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
            r.relation = $raw_relation,
            r.updated_at = datetime(),
            r.best_confidence = f.best_confidence
        WITH f
        WHERE $source_id <> '' OR $evidence <> ''
        MERGE (e:Evidence {{evidence_key: $evidence_key}})
        ON CREATE SET
            e.source_id = $source_id,
            e.text = $evidence,
            e.created_at = datetime()
        SET
            e.updated_at = datetime(),
            e.confidence = $confidence,
            e.confidence_detail_json = $confidence_detail_json,
            e.confidence_decision_json = $confidence_decision_json
        MERGE (f)-[:SUPPORTED_BY]->(e)
        """

        session.run(
            query,
            head=head,
            tail=tail,
            raw_relation=raw_relation,
            source_id=source_id,
            evidence=evidence,
            fact_key=fact_key,
            evidence_key=evidence_key,
            confidence=confidence,
            confidence_detail_json=self.json_text(item.get('confidence_detail')),
            confidence_decision_json=self.json_text(item.get('confidence_decision')),
        )
        return True

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
                    head = item.get('head', '').strip()
                    tail = item.get('tail', '').strip()
                    safe_relation = self.clean_relation_type(item.get('relation', '').strip())
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
                            head = item.get('head', '').strip()
                            tail = item.get('tail', '').strip()
                            safe_relation = importer.clean_relation_type(item.get('relation', '').strip())
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
