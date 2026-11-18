"""Named, source-scoped modelling definitions for a shared candidate ontology.

These definitions describe an evidence representation. They do not certify new
physical laws, alter the old ontology, or approve individual fact mappings.
"""
import hashlib
from copy import deepcopy

NAMESPACE = 'urn:kg:shared-source-model:20261118:'

# key, display name, definition, parent key. Source-scoped objects can be real,
# hypothetical, or grouped, as explicitly described in their source passage.
CLASSES = {
 'Concept': ('来源限定概念','在给定来源中被明确指称的概念描述；保留来源身份和局部含义，不从词面推导跨来源等价。',None),
 'Subject': ('来源限定对象','给定来源明确指称的对象或受限对象集合的描述；不据本类成员身份断言对象实际存在或断言集合为全体。',None),
 'ConceptGroup': ('来源概念组','由来源作为组合讨论的概念描述组；组的陈述不自动分配给每个成员。','Concept'),
 'Passage': ('来源篇章片段','具有文档版本和精确跨度的篇章位置描述；篇章先后位置不是物理时间。',None),
 'Claim': ('来源限定命题','来源中具有内容、极性、归属和适用范围的命题描述；保存命题不表示系统批准该命题。',None),
 'Context': ('来源分析情境','来源明确限定的分析、建模或推导情境；不表示其假设适用于所有物理系统。',None),
 'Condition': ('来源条件命题','来源明确给出的适用条件描述；合取、析取和附着对象须保留。','Claim'),
 'Classification': ('来源分类判据','描述来源中的分类、模型归类或命名判据及其条件和量词；不自动作为全称类公理。','Claim'),
 'Interpretation': ('来源归属解释','具有作者解释口吻、引用归属或局部表征范围的解释命题；不自动成为对象的无条件本质属性。','Claim'),
 'FlowContext': ('来源流动情境','来源中带流体对象和条件的流动情境描述。','Context'),
 'Behavior': ('来源模型行为','在来源语境下讨论的模型行为描述；模型趋近不等于实际对象无条件属于该模型。','Subject'),
 'DerivationResult': ('来源推导结果项','作为给定推导之结果的方程或物理量描述；类型不意味着该结果已由本系统证明。','Concept'),
 'Event': ('来源事件描述','来源所述事件、过程或可能事件的描述，分别保存参与角色、方式、程度、尺度和语气；不自动断言事件已发生。','Claim'),
 'Derivation': ('来源推导描述','来源描述的推导或确定过程，区分输入、操作、结果与适用范围；不自动补齐省略的输入或执行计算。','Event'),
 'Equation': ('来源方程描述','在来源中有明确身份的方程或方程表述描述；同号但不同文档的方程不自动同一。','Concept'),
 'Law': ('来源物理定律概念','给定来源所称物理定律及其陈述身份的概念描述；与该定律的具体表述命题和应用范围分开。','Concept'),
 'UnitSpec': ('来源单位说明','给定来源对量的单位或量纲表达的说明；不等于一次测量的数值。','Concept'),
 'Requirement': ('来源要求命题','来源提出的分析或记号使用要求，保留规范性语气、理由及作者实际采用或忽略的说明。','Claim'),
 'ControlBoundary': ('来源控制体边界对象','给定来源指定的控制体边界或端面的对象描述；不同情境中的边界不自动同一。','Subject'),
 'ControlSurfaceRole': ('来源控制面角色','来源赋给某控制体边界对象的控制面角色描述；角色指定不等于所有边界对象的普遍分类。','Concept'),
 'Symbol': ('来源算子符号','来源中被说明的数学算子符号描述；符号与其表达式转写及排版证明分开。','Concept'),
 'Ratio': ('来源物理量比值','来源中具有所比物理量和局部用途的量比值概念描述；比值不等于任一个被比的量。','Concept'),
 'Intensity': ('来源强度概念','来源在特定现象语境下所称强度的概念描述；其表征量须由来源说明。','Concept'),
 'Variables': ('来源变量组','在给定函数关系中共同讨论的变量描述组；保留已知与未知、部分量词及参与角色。','Concept'),
 'Function': ('来源函数关系','给定来源中用于关联变量或描述状态的函数关系描述；函数关联不自动构成物理因果。','Concept'),
 'MaterialContext': ('来源物质条件','函数关系或状态描述所针对的物质及其限定情境；不与函数关系本身混同。','Context'),
 'Quantity': ('来源结果量描述','在来源中作为结果或被解释对象讨论的物理量描述；与其值、成因及表征方式分开。','Concept'),
 'ControlVolume': ('来源控制体对象','来源分析选择的控制体描述，保留真实或假想的说明及分析范围。','Subject'),
 'Assumption': ('来源结构假设','来源分析对结构角色与数量作出的假设命题；假设的保存不等于该结构普遍存在。','Claim'),
 'FluidGroup': ('来源流体对象组','来源限定的流体对象或子集描述，保留 Many 等量词，不能自动提升为所有流体。','Subject'),
 'Capacity': ('来源承载能力命题','来源对对象能否承受某类负载及程度的描述，保留 can、little or no 和负载组合。','Claim'),
 'Density': ('来源密度约束命题','来源对密度的空间比较和时间变化的条件性约束描述；定性常数不是已知数值。','Claim'),
 'Term': ('来源方程项','在特定来源方程中有位置或角色身份的项描述；不同方程的同序项不自动同一。','Concept'),
 'Unspecified': ('来源未指定内容命题','来源明确说明某方程或表述没有指定的内容；这种否定不表示所述物理量不存在。','Claim'),
 'PropertyCount': ('来源独立性质数目概念','给定状态描述所需独立性质数目的概念描述；数目与决定该数目的原理分开。','Concept'),
 'Principle': ('来源状态公设概念','给定来源所指状态公设的概念描述，保留其所讨论系统的范围。','Concept'),
 'Premise': ('来源推导前提','给定推导以之为前提的命题描述；不是对所有对象的无条件属性断言。','Claim'),
 'SystemContext': ('来源系统情境','来源中具有质量、过程等明确限定的系统情境描述；不自动声明该过程已经发生。','Context'),
 'Notation': ('来源记号惯例','来源所讨论的数学或物理记号使用惯例描述；与作者本次实际书写或忽略惯例的做法分开。','Concept'),
 'Comparison': ('来源比较命题','来源对多个变化量共同给出的比较命题，保留 more/higher 的作用域；不自动拆成独立全称因果。','Claim'),
 'StateEquation': ('一般物质状态方程','本轮来源将描述物质状态变量之间、随物质而异的函数关系称为 equation of state；此类表达该通用概念，不合并旧本体中两个气象应用描述节点。','Function'),
 'ContinuityEquation': ('连续方程（质量守恒）','本轮来源将质量守恒方程同称为 continuity equation；此类表示该质量守恒方程概念，不泛指任意守恒方程或其全部具体形式。','Equation'),
}

ROLE_KEYS = {
 '概念':'Concept','来源解释命题':'Interpretation','教材概念组':'ConceptGroup','来源篇章':'Passage',
 '受限对象或对象组':'Subject','模型或分类判据':'Classification','流动情境':'FlowContext',
 '限定方程命题':'Claim','模型行为':'Behavior','条件命题':'Condition','方程或量':'DerivationResult',
 '推导描述':'Derivation','方程描述':'Equation','定律概念':'Law','量的单位说明':'UnitSpec',
 '分析情境':'Context','能量考虑命题':'Requirement','控制体边界对象':'ControlBoundary',
 '控制面角色':'ControlSurfaceRole','算子符号':'Symbol','量比值':'Ratio','强度概念':'Intensity',
 '变量组':'Variables','函数关系描述':'Function','物质条件':'MaterialContext','来源命题':'Claim',
 '原因事件描述':'Event','结果事件描述':'Event','结果量描述':'Quantity','控制体对象':'ControlVolume',
 '带角色及数量的假设命题':'Assumption','流体对象组':'FluidGroup','负载能力命题':'Capacity',
 '密度约束命题':'Density','流体情境':'FlowContext','有来源身份的方程项':'Term','解释命题':'Interpretation',
 '有来源身份的方程':'Equation','未指定内容命题':'Unspecified','参与对象':'Subject',
 '事件描述':'Event','独立性质数目概念':'PropertyCount','状态公设概念':'Principle',
 '推导情境':'Context','前提命题':'Premise','定律表述':'Law','系统情境':'SystemContext',
 '记号惯例':'Notation','要求命题':'Requirement','比较对象组':'Subject','比较命题':'Comparison',
}
ALIASES = {
 'StateEquation': ['通用物质状态方程','equation of state'],
 'ContinuityEquation': ['连续方程','质量守恒方程','continuity equation','equation of conservation of mass'],
}
PROPERTY_CASES = {
 'isResultOfDerivation':['PILOT-006','PILOT-032'],
 'expressesLaw':['PILOT-026'],
 'participatesInFunctionalRelation':['PILOT-013'],
 'variesWithSubstance':['PILOT-016'],
 'usesControlVolume':['PILOT-017'],
 'hasStructuralAssumption':['PILOT-024'],
 'hasTermInterpretation':['PILOT-020'],
 'doesNotSpecifyInContext':['PILOT-025'],
 'hasComparativeClaim':['PILOT-015'],
 'hasCauseInContext':['PILOT-034'],
}


def identity(key, prefix='C'):
    iri = NAMESPACE + ('class:' if prefix == 'C' else 'property:') + key
    return iri, prefix + '_' + hashlib.sha256(iri.encode('utf-8')).hexdigest()[:20]


def build_graph(base, family_requirements):
    from rdflib import URIRef, Literal, RDF, RDFS, OWL, SKOS, XSD
    from ontologyBuild.rdf_graph import parse_turtle
    from ontologyBuild.rdf_artifact import canonical_turtle
    from ontologyBuild.owl_to_project_schema import convert_turtle
    from ontologyBuild.ontology_adapter import iter_classes, iter_object_properties, iter_datatype_properties
    before_source = (base.path / 'ontology.ttl').read_text(encoding='utf-8')
    graph = parse_turtle(before_source, preserve_lexical=True)
    before = set(graph)
    old_nodes = [*iter_classes(base.ontology), *iter_object_properties(base.ontology), *iter_datatype_properties(base.ontology)]
    iri_map = {x['iri']:x['id'] for x in old_nodes}
    annotations = {name:URIRef(NAMESPACE + 'annotation:' + name) for name in ('modelingOrigin','sourceSampleIds','baseRelease')}
    for node in annotations.values():
        graph.add((node,RDF.type,OWL.AnnotationProperty))
    catalog = []
    for key,(label,definition,parent) in CLASSES.items():
        iri, cid = identity(key); node = URIRef(iri); iri_map[iri] = cid
        graph.add((node,RDF.type,OWL.Class)); graph.add((node,RDFS.label,Literal(label,lang='zh')))
        graph.add((node,SKOS.definition,Literal(definition,lang='zh')))
        graph.add((node,annotations['modelingOrigin'],Literal('source_scoped_engineering_definition_pending_semantic_review')))
        if parent: graph.add((node,RDFS.subClassOf,URIRef(identity(parent)[0])))
        for alias in ALIASES.get(key,[]):
            graph.add((node,SKOS.altLabel,Literal(alias,lang='en' if alias.isascii() else 'zh')))
        catalog.append({'key':key,'id':cid,'iri':iri,'label':label,'definition':definition,
            'definition_origin':'engineering_representation_definition_with_source_materials',
            'source_support_is_not_proof_of_all_class_axioms':True})
    # Canonical generic concept is distinct from old application-scoped nodes.
    for cid in ('C_eaa767de9c851cd73be7','C_b944bb2feba8240d5c86'):
        old = next(x for x in old_nodes if x['id']==cid)
        graph.add((URIRef(identity('StateEquation')[0]),RDFS.seeAlso,URIRef(old['iri'])))
    properties = []
    for family in family_requirements:
        sigs = deepcopy(family['proposed_signatures'])
        # The expression repair keeps 015 as an intact comparative proposition,
        # rather than presuming its independently distributable causal edges.
        if family['family'] == 'causal':
            sigs = [s for s in sigs if s['label'] != 'causesInContext']
            sigs.append({'label':'hasComparativeClaim','domain_role':'比较对象组','range_role':'比较命题',
                'definition':'记录来源对比较对象组给出的完整比较命题，保留共同作用域，不自动拆分因果。'})
        for s in sigs:
            key=s['label']; literal = '字面量' in s['range_role']
            iri,rid = identity(key,'D' if literal else 'R'); node=URIRef(iri); iri_map[iri]=rid
            graph.add((node,RDF.type,OWL.DatatypeProperty if literal else OWL.ObjectProperty))
            graph.add((node,RDFS.label,Literal(key,lang='en')))
            definition=s['definition']+' 本关系的使用必须保留来源、限定信息和完整命题；建模定义不代表候选事实已获批准。'
            graph.add((node,SKOS.definition,Literal(definition,lang='zh')))
            domain=ROLE_KEYS[s['domain_role']]
            graph.add((node,RDFS.domain,URIRef(identity(domain)[0])))
            range_key=None if literal else ROLE_KEYS[s['range_role']]
            graph.add((node,RDFS.range,XSD.string if literal else URIRef(identity(range_key)[0])))
            ids = PROPERTY_CASES.get(key,family['sample_ids'])
            graph.add((node,annotations['sourceSampleIds'],Literal(','.join(ids))))
            graph.add((node,annotations['baseRelease'],Literal(base.manifest['release_id'])))
            properties.append({'key':key,'id':rid,'iri':iri,'family':family['family'],'definition':definition,
                'kind':'datatype_property' if literal else 'object_property','domain_key':domain,
                'range_key':range_key,'datatype':str(XSD.string) if literal else None,'source_sample_ids':ids})
    sources={key:set() for key in CLASSES}
    sources['StateEquation'].add('PILOT-035');sources['ContinuityEquation'].add('PILOT-037')
    for p in properties:
        for role in ('domain_key','range_key'):
            if p[role]:sources[p[role]].update(p['source_sample_ids'])
    for key in CLASSES:
        parent=CLASSES[key][2]
        while parent:
            sources[parent].update(sources[key]);parent=CLASSES[parent][2]
    for item in catalog:
        item['source_sample_ids']=sorted(sources[item['key']])
        assert item['source_sample_ids'], 'missing modelling source examples: '+item['key']
    turtle=canonical_turtle(graph)
    converted=convert_turtle(turtle,iri_id_map=iri_map,preserve_lexical=True)
    assert before <= set(graph)
    for fn in (iter_classes,iter_object_properties,iter_datatype_properties):
        old={x['id']:x for x in fn(base.ontology)}; new={x['id']:x for x in fn(converted['project_schema'])}
        assert all(new[k]==v for k,v in old.items()), 'base schema object changed'
    return {'turtle':turtle,'converted':converted,'new_classes':catalog,'new_properties':properties,
        'base_source':before_source,'added_triple_count':len(set(graph)-before),'removed_triple_count':0}
