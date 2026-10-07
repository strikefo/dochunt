"""Application-owned structured output shapes; local validation remains authoritative."""


def _object(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def _evidence_schema(kind):
    string = {'type': 'string'}
    if kind == 'evidence_review':
        verdict = _object({'lead': string, 'verdict': {'type': 'string', 'enum': ['approve', 'reject']},
                           'problems': {'type': 'array', 'items': string, 'maxItems': 12}})
        return _object({'verdicts': {'type': 'array', 'items': verdict, 'maxItems': 3}})
    claim = _object({'text': string, 'source': string, 'quote': string})
    record = _object({'description': string, 'could_reveal': string, 'essential': {'type': 'boolean'},
                      'kind': {'type': 'string', 'enum': ['underlying', 'seed']},
                      'route': {'type': 'string', 'enum': ['public_download', 'records_request', 'unknown']},
                      'custodian': string, 'location': string, 'how': string, 'source': string, 'quote': string})
    lead = _object({'headline': string,
                    'established': {'type': 'array', 'items': claim, 'maxItems': 8},
                    'qualifications': {'type': 'array', 'items': claim, 'maxItems': 8},
                    'hypothesis': string, 'already_answered': string,
                    'records': {'type': 'array', 'items': record, 'maxItems': 6},
                    'request_text': string,
                    'newsworthiness': {'type': 'integer', 'minimum': 1, 'maximum': 10}})
    return _object({'leads': {'type': 'array', 'items': lead, 'maxItems': 3}, 'no_lead_reason': string})


def output_schema(kind, limits):
    if kind in {'evidence_analysis', 'evidence_review'}:
        return _evidence_schema(kind)
    raise ValueError('Unsupported structured output stage')
