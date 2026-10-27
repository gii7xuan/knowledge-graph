"""Local Turtle parsing with lexical RDF literals and an explicit document base.

Uses the pinned RDFLib Turtle parser with a local sink, without changing process
global NORMALIZE_LITERALS or fetching owl:imports.
"""
from rdflib import Graph,Literal,XSD,BNode
from rdflib.parser import create_input_source
from rdflib.plugins.parsers.notation3 import RDFSink,SinkParser,exponent_syntax,decimal_syntax,integer_syntax,numberCharsPlus


class _LexicalSink(RDFSink):
    def newLiteral(self,s,dt,lang):
        return Literal(s,datatype=dt,lang=lang if not dt else None,normalize=False)


class _LexicalParser(SinkParser):
    def anonymousNode(self,label):
        if getattr(self,'preserve_labels',False): return BNode(label)
        return super().anonymousNode(label)

    def nodeOrLiteral(self,argstr,i,res):
        old_line,old_start=self.lines,self.startOfLine
        position=self.skipSpace(argstr,i)
        if position>=0 and argstr[position] in numberCharsPlus:
            for syntax,datatype in ((exponent_syntax,XSD.double),(decimal_syntax,XSD.decimal),(integer_syntax,XSD.integer)):
                match=syntax.match(argstr,position)
                if match:
                    res.append(Literal(argstr[position:match.end()],datatype=datatype,normalize=False))
                    return match.end()
        self.lines,self.startOfLine=old_line,old_start
        return super().nodeOrLiteral(argstr,i,res)


class _LegacySink(RDFSink):
    def newLiteral(self,s,dt,lang):
        return Literal(s,datatype=dt,lang=lang if not dt else None,normalize=True)


def parse_turtle(source,*,base_iri=None,preserve_lexical=True,preserve_bnode_labels=False):
    graph=Graph()
    input_source=create_input_source(data=source.lstrip('\ufeff'),publicID=base_iri,format='turtle')
    try:
        parser_type,sink_type=(_LexicalParser,_LexicalSink) if preserve_lexical else (SinkParser,_LegacySink)
        parser=parser_type(sink_type(graph),baseURI=graph.absolutize(input_source.getPublicId() or input_source.getSystemId() or ''),turtle=True)
        parser.preserve_labels=preserve_bnode_labels
        parser.loadStream(input_source.getCharacterStream() or input_source.getByteStream())
        for prefix,namespace in parser._bindings.items(): graph.bind(prefix,namespace)
    finally: input_source.close()
    return graph
