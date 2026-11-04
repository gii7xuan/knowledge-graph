"""Canonical spellings for the datatype names supported by the application."""

XSD = 'http://www.w3.org/2001/XMLSchema#'
RDF_LANG_STRING = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#langString'
XSD_NAMES = frozenset({'string', 'boolean', 'integer', 'int', 'long', 'short',
    'byte', 'nonNegativeInteger', 'positiveInteger', 'decimal', 'double',
    'float', 'date', 'dateTime', 'anyURI'})


def canonical_datatype(value):
    if not isinstance(value, str):
        return value
    if value in XSD_NAMES:
        return XSD + value
    if value.startswith('xsd:'):
        return XSD + value[4:]
    if value == 'rdf:langString':
        return RDF_LANG_STRING
    return value
