import os

from neo4j import GraphDatabase

# Neo4j 配置
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

def clear_graph():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    with driver.session() as session:
        # 删除所有节点和关系
        session.run("MATCH (n) DETACH DELETE n")
        print("已清空Neo4j图数据库")
    
    driver.close()

if __name__ == "__main__":
    clear_graph()
