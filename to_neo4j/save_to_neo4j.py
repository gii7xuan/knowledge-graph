import json
import re
import os
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

    def get_existing_entity_names(self) -> set:
        """获取数据库中已存在的所有实体名称"""
        with self.driver.session() as session:
            result = session.run("MATCH (c:C_Gun) RETURN c.name AS name UNION MATCH (c:C_Ammunition) RETURN c.name AS name UNION MATCH (c:C_ComponentMechanism) RETURN c.name AS name")
            return {record["name"] for record in result}
    
    def import_data(self, triplets):
        print("正在连接 Neo4j 并写入数据...")
        
        # 获取已存在的实体名称
        existing_names = self.get_existing_entity_names()
        print(f"数据库中已存在 {len(existing_names)} 个实体")

        # 统计计数
        success_count = 0

        with self.driver.session() as session:
            for item in tqdm(triplets, desc="Importing"):
                head = item.get('head', '').strip()
                tail = item.get('tail', '').strip()
                raw_relation = item.get('relation', '').strip()
                source_id = item.get('source_id', '').strip()
                
                # 获取实体类别信息（用于 Neo4j 导入时的颜色区分）
                head_type = item.get('head_type', 'Unknown')
                tail_type = item.get('tail_type', 'Unknown')

                # 基础校验
                if not head or not tail or not raw_relation:
                    continue

                # 关系名清洗
                safe_relation = self.clean_relation_type(raw_relation)

                # 构建 Cypher
                # 使用 MERGE 避免重复创建节点
                # 使用 head_type 和 tail_type 作为节点标签（如 :C_Gun, :C_Ammunition）
                # 如果类别为空或 Unknown，则使用默认标签
                head_label = head_type if head_type and head_type != 'Unknown' else 'Entity'
                tail_label = tail_type if tail_type and tail_type != 'Unknown' else 'Entity'
                
                query = f"""
                MERGE (h:{head_label} {{name: $head}})
                MERGE (t:{tail_label} {{name: $tail}})
                MERGE (h)-[:{safe_relation} {{source_id: $source_id}}]->(t)
                """

                try:
                    session.run(query, head=head, tail=tail, source_id=source_id)
                    success_count += 1
                except Exception as e:
                    print(f"写入失败 [{head} -> {safe_relation} -> {tail}]: {e}")

        print(f"\n导入完成!成功写入 {success_count} 条关系。")


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
                        head = item.get('head', '').strip()
                        tail = item.get('tail', '').strip()
                        raw_relation = item.get('relation', '').strip()
                        source_id = item.get('source_id', '').strip()
                        
                        # 获取实体类别信息（用于 Neo4j 导入时的颜色区分）
                        head_type = item.get('head_type', 'Unknown')
                        tail_type = item.get('tail_type', 'Unknown')

                        # 基础校验
                        if not head or not tail or not raw_relation:
                            continue
                        
                        # 关系名清洗
                        safe_relation = importer.clean_relation_type(raw_relation)
                        
                        # 使用 head_type 和 tail_type 作为节点标签
                        head_label = head_type if head_type and head_type != 'Unknown' else 'Entity'
                        tail_label = tail_type if tail_type and tail_type != 'Unknown' else 'Entity'
                        
                        query = f"""
                        MERGE (h:{head_label} {{name: $head}})
                        MERGE (t:{tail_label} {{name: $tail}})
                        MERGE (h)-[:{safe_relation} {{source_id: $source_id}}]->(t)
                        """
                        
                        try:
                            session.run(query, head=head, tail=tail, source_id=source_id)
                            file_success += 1
                            total_success += 1
                        except Exception as e:
                            print(f"写入失败 [{head} -> {safe_relation} -> {tail}]: {e}")
                
                print(f"文件 [{os.path.basename(json_file)}] 导入完成: {file_success}/{file_triplets} 条关系")
                
            except Exception as e:
                print(f"处理文件 {json_file} 时出错: {e}")
                continue
    
    finally:
        importer.close()
    
    print(f"\n所有文件导入完成!")
    print(f"共处理 {len(json_files)} 个文件, {total_triplets} 条三元组")
    print(f"成功写入 {total_success} 条关系到 Neo4j。")


if __name__ == "__main__":
    main()