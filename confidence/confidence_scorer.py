"""
置信度评分器 - 用于计算三元组的置信度分数
基于多维加权评分机制（根据置信度机制.md）

得分公式：
FinalScore = (0.4 * S_llm + 0.3 * S_sim_new + 0.3 * S_evidence) * M_logic * M_map

其中：
- S_llm: LLM 自评得分 (0-1)
- S_sim_new: 关系映射相似度（Min-Max 缩放后，考虑实体类型匹配）
- S_evidence: 原文证据支持分 (0.0/0.5/1.0)
- M_logic: 逻辑审计系数 (1.2=合理, 1.0=可能, 0.2=荒谬)
- M_map: 关系稳定性系数 (1.2=精确匹配, 1.0=语义对齐, 0.6=模糊/新词)
"""

from openai import OpenAI
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional
import time
import re
import json
import os


class ConfidenceScorer:
    """
    置信度评分器

    功能：
    1. 从 LLM 的 thought 中提取自评得分
    2. 使用向量模型计算关系词与本体关系的相似度（Min-Max 缩放）
    3. 基于原文证据比对计算证据支持分（支持两种模式：嵌入模型 / 大模型）
    4. 计算逻辑审计系数（M_logic）
    5. 计算关系稳定性系数（M_map）
    6. 综合计算置信度并进行分流决策

    证据匹配模式：
    - entailment/llm: 使用大模型判断证据句是否语义蕴含三元组陈述
    - embedding: 使用嵌入模型计算三元组陈述与证据句的整体语义相似度
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        embedding_model: str,
        relations: List[str],
        entity_to_type: Dict[str, str],
        thresholds: Dict[str, float] = None,
        ontology_json_path: str = "ontology/ontology.json",
        evidence_matching_method: str = "entailment",  # "entailment", "llm" or "embedding"
    ):
        """
        初始化置信度评分器

        Args:
            api_key: API 密钥
            base_url: API 基础 URL
            embedding_model: 嵌入模型名称
            relations: 本体关系列表
            entity_to_type: 实体-类型映射 (entity -> type)
            thresholds: 置信度阈值配置
                {
                    "high": float,      # 高置信度阈值 (>0.8)
                    "medium_high": float,  # 中高置信度阈值 (0.6-0.8)
                    "medium_low": float,   # 中低置信度阈值 (0.4-0.6)
                    "low": float,           # 低置信度阈值 (≤0.4)
                }
            ontology_json_path: JSON 本体文件路径（用于获取 domain-range 信息）
            evidence_matching_method: 证据匹配方法
                - "entailment" / "llm": 使用大模型判断证据句是否蕴含三元组陈述
                - "embedding": 使用嵌入模型匹配三元组陈述与证据句
        """
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.embedding_model = embedding_model
        self.relations = relations
        self.entity_to_type = entity_to_type

        # 默认阈值配置（根据置信度机制.md）
        if thresholds is None:
            thresholds = {
                "high": 0.8,        # >0.8: 直接保留
                "medium_high": 0.6, # 0.6-0.8: 自动映射
                "medium_low": 0.4,  # 0.4-0.6: 人工审核
                "low": 0.0,         # ≤0.4: 丢弃
            }
        self.thresholds = thresholds

        # 从 JSON 本体文件加载 domain-range 信息
        self.ontology_json_path = ontology_json_path
        self.ontology_data = self._load_ontology_json(ontology_json_path)

        # 缓存关系嵌入
        self._relation_embeddings = {}

        # 缓存实体类型嵌入（用于父子类判断）
        self._entity_type_embeddings = {}

        # 构建类型到实体的映射（用于父子类判断）
        self._type_to_entities = {}
        for entity, entity_type in entity_to_type.items():
            if entity_type not in self._type_to_entities:
                self._type_to_entities[entity_type] = []
            self._type_to_entities[entity_type].append(entity)

        # 证据匹配方法：embedding 或 llm
        self.evidence_matching_method = evidence_matching_method

    def _load_ontology_json(self, path: str) -> Dict:
        """
        从 JSON 本体文件加载数据

        Args:
            path: JSON 本体文件路径

        Returns:
            本体数据字典
        """
        if not os.path.exists(path):
            print(f"警告：本体 JSON 文件不存在: {path}")
            return {}

        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载本体 JSON 文件失败: {e}")
            return {}

    def _get_relation_domain_range(self, relation_label: str) -> Tuple[List[str], List[str]]:
        """
        从 JSON 本体中获取关系的 domain 和 range

        Args:
            relation_label: 关系标签（如"具有子系统"）

        Returns:
            (domain 类别列表, range 类别列表)
        """
        domains = set()
        ranges = set()

        def add_if_match(prop_data: Dict):
            if not isinstance(prop_data, dict):
                return
            if prop_data.get("label") != relation_label:
                return
            domains.update(prop_data.get("domain", []) or [])
            ranges.update(prop_data.get("range", []) or [])

        object_properties = self.ontology_data.get("object_properties", {})
        if isinstance(object_properties, dict):
            for prop_data in object_properties.values():
                add_if_match(prop_data)
        elif isinstance(object_properties, list):
            for prop_data in object_properties:
                add_if_match(prop_data)

        for class_data in self.ontology_data.get("classes", {}).values():
            if not isinstance(class_data, dict):
                continue
            for prop_data in class_data.get("object_properties", []) or []:
                add_if_match(prop_data)

        return sorted(domains), sorted(ranges)

    def _check_entity_type_match(self, entity: str, expected_types: List[str]) -> float:
        """
        检查实体是否属于预期的类型列表

        Args:
            entity: 实体名称
            expected_types: 预期的类型列表（本体中的类别 ID）

        Returns:
            匹配分数：1.0=同类匹配, 0.8=实体类型是预期类型的子类, 0.0=不匹配
        """
        # 检查是否直接匹配
        if entity in self.entity_to_type:
            entity_type = self.entity_to_type[entity]
            if entity_type in expected_types:
                return 1.0
            for expected_type in expected_types:
                if self._is_subclass_of(entity_type, expected_type):
                    return 0.8

        # 检查是否属于预期类型的子类
        classes = self.ontology_data.get("classes", {})
        for expected_type in expected_types:
            expected_class = classes.get(expected_type, {})
            # 检查是否为子类
            subclasses = expected_class.get("subclasses", [])
            if entity in subclasses:
                return 0.8
            # 检查子类的子类
            for subclass_id in subclasses:
                subclass_data = classes.get(subclass_id, {})
                if entity in subclass_data.get("subclasses", []):
                    return 0.6

        return 0.0

    def _is_subclass_of(self, entity_type: str, expected_type: str) -> bool:
        """
        检查 entity_type 是否是 expected_type 的子类

        Args:
            entity_type: 实体类型（本体中的类别 ID）
            expected_type: 预期类型（本体中的类别 ID）

        Returns:
            True 如果 entity_type 是 expected_type 的子类或相同
        """
        if entity_type == expected_type:
            return True

        classes = self.ontology_data.get("classes", {})
        entity_class = classes.get(entity_type, {})

        # 检查直接父类
        parent = entity_class.get("parent")
        if parent == expected_type:
            return True

        # 递归检查父类
        if parent:
            return self._is_subclass_of(parent, expected_type)

        return False

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """获取文本的嵌入向量；失败或无效时返回 None。"""
        try:
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=[text]
            )
            embedding = response.data[0].embedding
            # 验证嵌入向量有效性
            if embedding and len(embedding) > 0 and sum(x*x for x in embedding) > 1e-10:
                return embedding
            else:
                print("嵌入向量无效（零向量或空），停止自动映射")
                return None
        except Exception as e:
            print(f"获取嵌入向量失败: {e}")
            return None

    def _get_relation_embedding(self, relation: str) -> Optional[List[float]]:
        """获取关系词的嵌入向量（带缓存）"""
        if relation in self._relation_embeddings:
            return self._relation_embeddings[relation]

        embedding = self._get_embedding(relation)
        if embedding is not None:
            self._relation_embeddings[relation] = embedding
        return embedding

    def _get_entity_type_embedding(self, entity_type: str) -> Optional[List[float]]:
        """获取实体类型的嵌入向量（带缓存）"""
        if entity_type in self._entity_type_embeddings:
            return self._entity_type_embeddings[entity_type]

        embedding = self._get_embedding(entity_type)
        if embedding is not None:
            self._entity_type_embeddings[entity_type] = embedding
        return embedding

    def compute_llm_score(self, triplet: Dict) -> float:
        """
        从 triplet 的 thought 中提取 LLM 自评得分

        Args:
            triplet: 三元组字典

        Returns:
            LLM 自评得分（0-1）
        """
        thought = triplet.get("thought", "")
        confidence = triplet.get("confidence", None)

        # 如果 triplet 中已经有 confidence 字段，直接返回
        if confidence is not None:
            try:
                score = float(confidence)
                return max(0.0, min(1.0, score))
            except (ValueError, TypeError):
                pass

        # 从 thought 中尝试提取（正则匹配）
        patterns = [
            r'置信度[：:]\s*(\d\.?\d*)',
            r'confidence[：:]\s*(\d\.?\d*)',
            r'score[：:]\s*(\d\.?\d*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, thought, re.IGNORECASE)
            if match:
                try:
                    score = float(match.group(1))
                    return max(0.0, min(1.0, score))
                except (ValueError, TypeError):
                    pass

        # 尝试从 thought 中提取 0-1 的数字
        number_pattern = re.compile(r'\b([01]\.\d+|\b[01]\b)\b')
        for match in number_pattern.finditer(thought):
            try:
                score = float(match.group(1))
                return max(0.0, min(1.0, score))
            except (ValueError, TypeError):
                pass

        # 默认得分：0.5
        return 0.5

    def _min_max_scale_similarity(self, raw_similarity: float, min_sim: float = 0.4, max_sim: float = 0.6) -> float:
        """
        对原始相似度进行 Min-Max 缩放（根据置信度机制.md）

        痛点：垂直领域的词汇在通用 Embedding 下相似度普遍偏低（0.4-0.6）
        改进：采用动态缩放

        公式：
        S_sim_new = (S_raw - min_sim) / (max_sim - min_sim)

        Args:
            raw_similarity: 原始相似度
            min_sim: 最小相似度阈值（默认0.4）
            max_sim: 最大相似度阈值（默认0.6）

        Returns:
            缩放后的相似度（0-1）
        """
        # 边界情况处理
        if max_sim - min_sim <= 0:
            return 0.5

        # 缩放到 0-1 区间
        scaled = (raw_similarity - min_sim) / (max_sim - min_sim)

        # 限制在 0-1 范围内
        return max(0.0, min(1.0, scaled))

    def compute_similarity_score(self, relation: str) -> Tuple[float, str, float]:
        """
        计算关系词与本体关系的最大相似度（带 Min-Max 缩放和实体类型匹配）

        Args:
            relation: 提取的关系词

        Returns:
            (缩放后的相似度分数, 最佳匹配关系, 原始相似度)
        """
        if not relation:
            return 0.0, "空关系", 0.0

        # 方法1：使用向量相似度（优先）
        try:
            relation_embedding = self._get_relation_embedding(relation)
            if relation_embedding is None:
                return 0.0, "", 0.0

            best_score = 0.0
            best_match = ""

            for ontology_relation in self.relations:
                ontology_embedding = self._get_relation_embedding(ontology_relation)
                if ontology_embedding is None:
                    return 0.0, "", 0.0

                # 计算余弦相似度
                similarity = self._cosine_similarity(relation_embedding, ontology_embedding)

                # 获取本体关系的 domain 和 range
                domain, range_val = self._get_relation_domain_range(ontology_relation)

                # 如果 domain 和 range 不为空，计算实体类型匹配度
                type_match_score = 0.0
                if domain or range_val:
                    # 这里简化处理，假设 head 和 tail 已在 triplet 中
                    # 在实际使用时，应传入 triplet 信息
                    type_match_score = 0.3  # 默认给予 0.3 的奖励

                # 综合得分 = 语义相似度 + 实体类型匹配奖励
                combined_score = similarity + type_match_score

                if combined_score > best_score:
                    best_score = combined_score
                    best_match = ontology_relation

            # 对相似度进行 Min-Max 缩放
            scaled_score = self._min_max_scale_similarity(best_score)

            return scaled_score, best_match, best_score
        except Exception as e:
            print(f"向量相似度计算失败: {e}")
            return 0.0, "", 0.0

    def compute_similarity_score_with_entities(
        self,
        relation: str,
        head: str,
        tail: str
    ) -> Tuple[float, str, float]:
        """
        计算关系词与本体关系的最大相似度（考虑实体类型匹配）

        Args:
            relation: 提取的关系词
            head: 头实体
            tail: 尾实体

        Returns:
            (缩放后的相似度分数, 最佳匹配关系, 原始相似度)
        """
        if not relation:
            return 0.0, "空关系", 0.0

        try:
            relation_embedding = self._get_relation_embedding(relation)
            if relation_embedding is None:
                return 0.0, "", 0.0

            best_score = 0.0
            best_match = ""
            best_original_sim = 0.0

            for ontology_relation in self.relations:
                ontology_embedding = self._get_relation_embedding(ontology_relation)
                if ontology_embedding is None:
                    return 0.0, "", 0.0

                # 计算余弦相似度
                original_sim = self._cosine_similarity(relation_embedding, ontology_embedding)

                # 获取本体关系的 domain 和 range
                domain, range_val = self._get_relation_domain_range(ontology_relation)

                # 计算实体类型匹配度
                head_match = self._check_entity_type_match(head, domain) if domain else 1.0
                tail_match = self._check_entity_type_match(tail, range_val) if range_val else 1.0

                # 类型匹配奖励：完全匹配给予 0.2 的奖励
                type_match_score = 0.0
                if head_match >= 0.5 and tail_match >= 0.5:
                    type_match_score = 0.2
                elif head_match >= 0.5 or tail_match >= 0.5:
                    type_match_score = 0.1

                # 综合得分 = 语义相似度 + 实体类型匹配奖励
                combined_score = original_sim + type_match_score

                if combined_score > best_score:
                    best_score = combined_score
                    best_match = ontology_relation
                    best_original_sim = original_sim

            # 对相似度进行 Min-Max 缩放
            scaled_score = self._min_max_scale_similarity(best_score)

            return scaled_score, best_match, best_original_sim
        except Exception as e:
            print(f"向量相似度计算失败: {e}")
            return 0.0, "", 0.0

    def _cosine_similarity(
        self,
        vec1: Optional[List[float]],
        vec2: Optional[List[float]],
    ) -> float:
        """计算两个向量的余弦相似度"""
        if vec1 is None or vec2 is None:
            return 0.0
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def compute_logic_audit(self, head: str, relation: str, tail: str) -> Tuple[float, str]:
        """
        计算逻辑审计系数（M_logic）——根据置信度机制.md

        询问： "在工程/物理逻辑中，[主语] 与 [宾语] 之间存在 [关系] 是否合理？"
        打分：
        - 1.2 (合理)：如"复进速度-正比于-射速"，常识上完全成立
        - 1.0 (可能)：逻辑不冲突，但不确定
        - 0.2 (荒谬)：如"射速-属于-液压阻力"，逻辑上牛头不对马嘴

        Args:
            head: 头实体
            relation: 关系词
            tail: 尾实体

        Returns:
            (逻辑审计系数, 详细信息)
        """
        # 构建提示
        prompt = f"""在工程/物理逻辑中，"{head}" 与 "{tail}" 之间存在 "{relation}" 关系是否合理？

请根据工程常识判断：
- 合理 (1.2)：关系符合物理/工程逻辑，常识上完全成立
- 可能 (1.0)：逻辑不冲突，但需要更多证据确认
- 荒谬 (0.2)：关系明显不合理，逻辑上牛头不对马嘴

请仅输出数字：1.2、1.0 或 0.2"""

        try:
            response = self.client.chat.completions.create(
                model="Qwen3-Coder-Next",
                messages=[
                    {"role": "system", "content": "你是一位工程/物理逻辑审计专家。请严格根据工程常识判断三元组的合理性。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )

            content = response.choices[0].message.content.strip()

            # 解析结果
            if '1.2' in content:
                return 1.2, f"逻辑合理 (工程/物理常识支持)"
            elif '0.2' in content:
                return 0.2, f"逻辑荒谬 (违反工程/物理常识)"
            else:
                return 1.0, f"逻辑可能成立 (需要更多证据)"

        except Exception as e:
            print(f"逻辑审计失败: {e}")
            return 1.0, "逻辑审计失败(默认中等)"

    def _build_triplet_statement(self, triplet: Dict) -> str:
        """将三元组转为用于证据判断的完整陈述。"""
        head = str(triplet.get("head", "")).strip()
        relation = str(triplet.get("relation", "")).strip()
        tail = str(triplet.get("tail", "")).strip()
        return f"{head} {relation} {tail}".strip()

    def _split_evidence_sentences(self, evidence_text: str) -> List[str]:
        """将证据文本切成可独立判断的证据句。"""
        return [
            sentence.strip()
            for sentence in re.split(r"[。！？；.!?;\n]+", evidence_text)
            if sentence.strip()
        ]

    def _parse_entailment_score(self, content: str) -> float:
        """解析大模型返回的蕴含分，并归一到 0.0/0.5/1.0。"""
        try:
            parsed = json.loads(content)
            score = float(parsed.get("score", 0.0))
        except Exception:
            matched = re.search(r"\b(?:1(?:\.0)?|0\.5|0(?:\.0)?)\b", content)
            score = float(matched.group(0)) if matched else 0.0

        if score >= 0.75:
            return 1.0
        if score >= 0.25:
            return 0.5
        return 0.0

    def compute_evidence_score_embedding(self, triplet: Dict, evidence_text: str) -> Tuple[float, str]:
        """
        基于嵌入模型的整体语义匹配证据支持分

        逻辑：
        - 将三元组转成完整陈述
        - 计算“三元组陈述”与证据句的整体语义相似度
        - 相似度 > 0.7 -> 1.0（完全匹配）
        - 相似度 > 0.5 -> 0.5（部分匹配，如同义词、不同表达）
        - 相似度 <= 0.5 -> 0.0（无语义支撑）

        Args:
            triplet: 三元组字典
            evidence_text: 原文证据文本

        Returns:
            (证据支持分, 详细信息)
        """
        if not evidence_text:
            return 0.0, "无证据支持"

        triplet_statement = self._build_triplet_statement(triplet)
        if not triplet_statement:
            return 0.0, "三元组陈述为空"

        try:
            statement_emb = self._get_embedding(triplet_statement)
            if statement_emb is None:
                return 0.0, "嵌入模型失败，停止证据自动匹配"

            sentences = self._split_evidence_sentences(evidence_text)
            max_score = 0.0

            for sent in sentences:
                sent_emb = self._get_embedding(sent)
                if sent_emb is None:
                    return 0.0, "嵌入模型失败，停止证据自动匹配"

                max_score = max(max_score, self._cosine_similarity(statement_emb, sent_emb))

            # 根据相似度阈值打分
            if max_score > 0.7:
                return 1.0, f"三元组陈述与证据句整体语义匹配 (相似度={max_score:.2f})"
            elif max_score > 0.5:
                return 0.5, f"三元组陈述与证据句整体语义部分匹配 (相似度={max_score:.2f})"
            else:
                return 0.0, f"证据句未整体支持三元组陈述 (相似度={max_score:.2f})"

        except Exception as e:
            print(f"嵌入模型证据匹配失败: {e}")
            return 0.0, "证据匹配失败"

    def compute_evidence_score_llm(self, triplet: Dict, evidence_text: str) -> Tuple[float, str]:
        """
        基于大模型的三元组陈述-证据句语义蕴含判断

        逻辑：
        - 将三元组转成完整陈述
        - 判断证据句是否语义蕴含该三元组陈述
        - 完全蕴含 (1.0)：证据句明确支持完整主谓宾关系
        - 部分支持 (0.5)：证据句相关但关系、方向或对象不完整
        - 不蕴含 (0.0)：证据句不支持该三元组陈述

        Args:
            triplet: 三元组字典
            evidence_text: 原文证据文本

        Returns:
            (证据支持分, 详细信息)
        """
        if not evidence_text:
            return 0.0, "无证据支持"

        triplet_statement = self._build_triplet_statement(triplet)
        evidence_sentences = self._split_evidence_sentences(evidence_text)
        if not triplet_statement:
            return 0.0, "三元组陈述为空"
        if not evidence_sentences:
            return 0.0, "无可判断的证据句"

        evidence_block = "\n".join(
            f"{index + 1}. {sentence}"
            for index, sentence in enumerate(evidence_sentences)
        )

        prompt = f"""请判断证据句是否语义蕴含三元组陈述。

三元组陈述: "{triplet_statement}"
证据句:
{evidence_block}

判断标准：
- 1.0：至少一个证据句明确蕴含完整三元组陈述，包括主语、关系、宾语和关系方向。
- 0.5：证据句只支持其中一部分，或关系方向、关系类型、宾语范围仍需推断。
- 0.0：证据句不蕴含该三元组陈述；只同时出现主语和宾语也不能算支持。

请只输出 JSON，格式如下：
{{"score": 1.0, "reason": "简短理由", "sentence_index": 1}}
"""

        try:
            response = self.client.chat.completions.create(
                model="Qwen3-Coder-Next",
                messages=[
                    {"role": "system", "content": "你是一位严谨的信息抽取证据审计专家。只判断证据句是否语义蕴含三元组陈述。"},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0
            )

            content = response.choices[0].message.content.strip()
            score = self._parse_entailment_score(content)
            try:
                parsed = json.loads(content)
                reason = parsed.get("reason", "")
                sentence_index = parsed.get("sentence_index")
            except Exception:
                reason = ""
                sentence_index = None
            suffix = f"，证据句 {sentence_index}" if sentence_index else ""
            if score == 1.0:
                return 1.0, f"证据句语义蕴含三元组陈述{suffix}: {reason}".strip()
            if score == 0.5:
                return 0.5, f"证据句部分支持三元组陈述{suffix}: {reason}".strip()
            return 0.0, f"证据句不蕴含三元组陈述: {reason}".strip()

        except Exception as e:
            print(f"大模型证据蕴含判断失败: {e}")
            return 0.0, "证据蕴含判断失败"

    def compute_evidence_score(self, triplet: Dict, evidence_text: str) -> Tuple[float, str]:
        """
        计算证据支持分（S_evidence）——根据置信度机制.md

        根据 evidence_matching_method 配置选择匹配方法：
        - "entailment" / "llm": 使用大模型判断证据句是否蕴含三元组陈述
        - "embedding": 使用嵌入模型匹配三元组陈述与证据句

        Args:
            triplet: 三元组字典
            evidence_text: 原文证据文本

        Returns:
            (证据支持分, 详细信息)
        """
        method = (self.evidence_matching_method or "entailment").lower()
        if method == "embedding":
            return self.compute_evidence_score_embedding(triplet, evidence_text)
        else:  # entailment / llm
            return self.compute_evidence_score_llm(triplet, evidence_text)

    def compute_map_coefficient(self, relation: str, matched_relation: str) -> Tuple[float, str]:
        """
        计算关系稳定性系数（M_map）——根据置信度机制.md

        信息来源：关系映射结果
        打分：
        - 1.2 (精确匹配)：关系词与本体关系完全一致（字符串相等）
        - 1.0 (语义对齐)：Min-Max 缩放后的相似度 > 0.5（语义相近）
        - 0.6 (模糊/新词)：相似度较低或无法匹配

        Args:
            relation: 提取的关系词
            matched_relation: 匹配到的本体关系

        Returns:
            (关系稳定性系数, 详细信息)
        """
        # 如果关系在本体关系列表中，给予高分（说明是本体定义的关系）
        if relation in self.relations:
            return 1.2, f"本体关系 ({relation})"

        # 精确匹配（字符串相等）
        if relation == matched_relation:
            return 1.2, f"精确匹配 ({relation})"

        # 计算字符串相似度
        similarity = SequenceMatcher(None, relation, matched_relation).ratio()

        if similarity > 0.5:
            return 1.0, f"语义对齐 ({relation} -> {matched_relation}, sim={similarity:.2f})"

        return 0.6, f"模糊匹配/新词 ({relation} -> {matched_relation})"

    def compute_confidence(self, triplet: Dict, evidence_text: str = "") -> Dict:
        """
        计算三元组的综合置信度分数

        得分公式：
        FinalScore = (0.4 * S_llm + 0.3 * S_sim_new + 0.3 * S_evidence) * M_logic * M_map

        Args:
            triplet: 三元组字典，包含 head, relation, tail, thought, confidence 等字段
            evidence_text: 原文证据文本

        Returns:
            {
                "final_score": float,        # 0-1 概率化后的综合置信度分数
                "raw_final_score": float,    # 裁剪前的原始加权分数
                "S_llm": float,              # LLM 自评得分
                "S_sim_new": float,          # 关系映射相似度（缩放后）
                "S_evidence": float,         # 证据支持分
                "M_logic": float,            # 逻辑审计系数
                "M_map": float,              # 关系稳定性系数
                "logic_info": str,           # 逻辑审计详细信息
                "map_info": str,             # 关系映射详细信息
                "evidence_info": str,        # 证据匹配详细信息
                "match_relation": str,         # 匹配到的关系
                "original_sim": float,       # 原始相似度
                "threshold_info": str,       # 阈值分流信息
            }
        """
        # 1. 提取 LLM 自评得分
        s_llm = self.compute_llm_score(triplet)

        # 2. 计算关系映射相似度
        head = triplet.get("head", "")
        relation = triplet.get("relation", "")
        tail = triplet.get("tail", "")

        s_sim_new, match_relation, original_sim = self.compute_similarity_score_with_entities(
            relation, head, tail
        )

        # 3. 计算证据支持分
        s_evidence, evidence_info = self.compute_evidence_score(triplet, evidence_text)

        # 4. 计算逻辑审计系数
        m_logic, logic_info = self.compute_logic_audit(head, relation, tail)

        # 5. 计算关系稳定性系数
        m_map, map_info = self.compute_map_coefficient(relation, match_relation)

        # 6. 计算综合置信度
        # FinalScore = (0.4 * S_llm + 0.3 * S_sim_new + 0.3 * S_evidence) * M_logic * M_map
        base_score = 0.4 * s_llm + 0.3 * s_sim_new + 0.3 * s_evidence
        raw_final_score = base_score * m_logic * m_map
        final_score = max(0.0, min(1.0, raw_final_score))

        # 7. 确定分流决策
        threshold_info = self._get_threshold_info(final_score)

        return {
            "final_score": round(final_score, 4),
            "raw_final_score": round(raw_final_score, 4),
            "S_llm": round(s_llm, 4),
            "S_sim_new": round(s_sim_new, 4),
            "S_evidence": s_evidence,
            "M_logic": m_logic,
            "M_map": m_map,
            "logic_info": logic_info,
            "map_info": map_info,
            "evidence_info": evidence_info,
            "match_relation": match_relation,
            "original_sim": round(original_sim, 4),
            "threshold_info": threshold_info,
        }

    def _get_threshold_info(self, score: float) -> str:
        """
        根据置信度分数确定分流决策

        Args:
            score: 置信度分数

        Returns:
            阈值分流信息
        """
        if score > self.thresholds["high"]:
            return "分流: 直接保留 (>0.8)"
        elif score > self.thresholds["medium_high"]:
            return "分流: 自动映射 (0.6-0.8)"
        elif score > self.thresholds["medium_low"]:
            return "分流: 人工审核 (0.4-0.6)"
        else:
            return "分流: 丢弃 (≤0.4)"

    def analyze_shunt(self, confidence_result: Dict) -> Dict:
        """
        分析三元组的分流决策

        Args:
            confidence_result: compute_confidence 返回的结果

        Returns:
            {
                "shunt": str,           # 分流决策
                "reason": str,          # 分流原因
                "action": str,          # 建议操作
            }
        """
        score = confidence_result["final_score"]
        threshold_info = confidence_result["threshold_info"]

        if score > self.thresholds["high"]:
            return {
                "shunt": "high",
                "reason": f"置信度 {score:.2f} > 0.8，证据和逻辑均较强",
                "action": "直接保留到高置信度三元组库",
            }
        elif score > self.thresholds["medium_high"]:
            return {
                "shunt": "medium_high",
                "reason": f"置信度 {score:.2f} 在 0.6-0.8 之间，语义匹配良好但证据稍弱",
                "action": "自动映射到本体，无需人工审核",
            }
        elif score > self.thresholds["medium_low"]:
            return {
                "shunt": "medium_low",
                "reason": f"置信度 {score:.2f} 在 0.4-0.6 之间，存在一定不确定性",
                "action": "进入人工审核队列",
            }
        else:
            return {
                "shunt": "low",
                "reason": f"置信度 {score:.2f} ≤ 0.4，证据或逻辑存在明显问题",
                "action": "丢弃或标记为低置信度",
            }
