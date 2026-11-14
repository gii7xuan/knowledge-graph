"""Labeled-subset semantic metrics, distinct from contract and judgment coverage."""


def report(rows):
    def metric(identity,title,known,numerator,population,not_applicable=0):
        return {'id':identity,'title':title,'value':numerator/len(known) if known else None,
            'numerator':numerator,'denominator':len(known),'population':population,
            'unknown_or_missing':population-len(known)-not_applicable,'not_applicable':not_applicable,
            'status':'evaluated' if known else 'not_evaluable',
            'label_source':'exact-packet named human annotations',
            'scope':'labeled comparable subset; unknown cases are reported separately'}
    source=[(p,a) for p,a in rows if a and a['four_axes']['source'] in {'supported','contradicted','insufficient'}
        and p['system'].get('source_support_result') in {'supported','contradicted','insufficient'}]
    needed=[(p,a) for p,a in rows if a and a.get('needs_revision') is True and p['system'].get('recommendation') is not None]
    manual=[(p,a) for p,a in rows if p['system'].get('recommendation') in {'manual_review','wait_mapping'}]
    labeled_manual=[(p,a) for p,a in manual if a and type(a.get('avoidable_manual_review')) is bool]
    return {'version':'generalization_semantic_metrics_v1','metrics':[
        metric('source_support_judgment_accuracy','原文支持判断正确率',source,
            sum(p['system']['source_support_result']==a['four_axes']['source'] for p,a in source),len(rows)),
        metric('necessary_revision_recommendation_recall','必要修订建议覆盖率',needed,
            sum(p['system']['recommendation']=='revise' for p,a in needed),len(rows),
            sum(bool(a) and a.get('needs_revision') is False for _,a in rows)),
        metric('reasonable_manual_recommendation_rate','合理转人工建议率',labeled_manual,
            sum(a['avoidable_manual_review'] is False for _,a in labeled_manual),len(manual)),
        metric('avoidable_manual_recommendation_rate','可避免转人工建议率',labeled_manual,
            sum(a['avoidable_manual_review'] is True for _,a in labeled_manual),len(manual))],
        'handoff_scope':'recommendations only; actual manual handling is not inferred from shadow decisions',
        'source_judgment_coverage':{'determinate_predictions':sum(p['system'].get('source_support_result') in
            {'supported','contradicted','insufficient'} for p,_ in rows),'population':len(rows)},
        'independent_generalization_established':False}
