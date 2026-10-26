"""Emit browser boundary source from canonical Python AST. No app/model imports.

This is deterministic static source construction, not schema/runtime acceptance.
Unsupported annotations fail visibly; custom validators are listed, never executed.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
PRODUCT = WEB.parent
SOURCES = ('src/voicelab/contracts.py', 'src/voicelab/evaluation/adjudication.py',
           'src/voicelab/evaluation/reproduction.py')
OUTPUT = WEB / 'src/generated/contracts.ts'

def static_value(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [static_value(value) for value in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -static_value(node.operand)
    if isinstance(node, ast.BinOp):
        a, b = static_value(node.left), static_value(node.right)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            raise ValueError('non-numeric static expression')
        if isinstance(node.op, ast.Pow) and 0 <= b <= 32:
            return a ** b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Mult):
            return a * b
    raise ValueError('unsupported static value: ' + ast.dump(node))

def name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ''

def arguments(node):
    return list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]

def field_constraints(node):
    result = {}
    if isinstance(node, ast.Call) and name(node.func) == 'Field':
        for keyword in node.keywords:
            if keyword.arg in {'min_length', 'max_length', 'pattern', 'ge', 'gt', 'le', 'lt', 'allow_inf_nan', 'strict'}:
                result[keyword.arg] = static_value(keyword.value)
    return result

def rule(node):
    if isinstance(node, ast.Constant) and node.value is None:
        return {'kind': 'null'}
    if isinstance(node, ast.Name):
        return {'kind': {'str':'string','int':'integer','float':'number','bool':'boolean','object':'unknown','Any':'unknown'}.get(node.id, 'ref'), **({'name':node.id} if node.id not in {'str','int','float','bool','object','Any'} else {})}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return {'kind':'union','items':[rule(node.left),rule(node.right)]}
    if isinstance(node, ast.Subscript):
        kind, args = name(node.value), arguments(node)
        if kind == 'Annotated':
            value = rule(args[0])
            for metadata in args[1:]:
                value = {**value, **field_constraints(metadata)}
            return value
        if kind == 'Literal':
            return {'kind':'literal','values':[static_value(value) for value in args]}
        if kind == 'Union':
            return {'kind':'union','items':[rule(value) for value in args]}
        if kind == 'list':
            return {'kind':'array','item':rule(args[0])}
        if kind == 'dict':
            return {'kind':'record','key':rule(args[0]),'value':rule(args[1])}
    raise ValueError('unsupported annotation: ' + ast.unparse(node))

def ts(value):
    kind = value['kind']
    if kind == 'literal':
        return ' | '.join(json.dumps(v, ensure_ascii=True) for v in value['values'])
    if kind == 'union':
        return '(' + ' | '.join(ts(v) for v in value['items']) + ')'
    if kind == 'array':
        return 'Array<' + ts(value['item']) + '>'
    if kind == 'record':
        key = ts(value['key'])
        expression = 'Record<' + key + ', ' + ts(value['value']) + '>'
        return 'Partial<' + expression + '>' if value['key']['kind'] != 'string' else expression
    return {'string':'string','integer':'number','number':'number','boolean':'boolean','null':'null','unknown':'unknown'}.get(kind, value.get('name', 'unknown'))

def produce():
    trees, hashes = {}, {}
    for relative in SOURCES:
        raw = (PRODUCT / relative).read_bytes()
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        trees[relative] = ast.parse(raw.decode('utf-8-sig'), filename=relative)
    aliases, constants, candidates, origin, validators = {}, {}, {}, {}, {}
    http_routes = {}
    review_roles = None
    for relative, tree in trees.items():
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                label = node.targets[0].id
                if label == 'HTTP_RESPONSE_MODELS':
                    if not isinstance(node.value, ast.Dict):
                        raise ValueError('canonical HTTP registry must be a closed dictionary')
                    http_routes = {static_value(k): name(v) for k, v in zip(node.value.keys, node.value.values)}
                if isinstance(node.value, ast.Subscript) and name(node.value.value) in {'Annotated','Literal','Union'}:
                    aliases[label] = rule(node.value)
                elif label.isupper() and isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, str, bool)):
                    constants[label] = static_value(node.value)
            if isinstance(node, ast.ClassDef):
                candidates[node.name] = node
                origin[node.name] = relative
            if isinstance(node, ast.FunctionDef) and node.name == 'validate_packet':
                for part in ast.walk(node):
                    if isinstance(part, ast.Set) and all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in part.elts):
                        values = [v.value for v in part.elts]
                        if {'input_audio','rendered_audio','confirmation_audio','confirmation','journal','trace'} == set(values):
                            review_roles = values
    classes = {}
    def model(label):
        if label in classes:
            return classes[label]
        node = candidates[label]
        fields = {}
        for base in node.bases:
            parent = name(base)
            if parent in candidates and parent not in {'Model','BaseModel'}:
                fields.update(model(parent))
        custom = []
        for part in node.body:
            if isinstance(part, ast.AnnAssign) and isinstance(part.target, ast.Name):
                fields[part.target.id] = {'required':part.value is None,'rule':{**rule(part.annotation),**field_constraints(part.value)}}
            if isinstance(part, ast.FunctionDef):
                for decorator in part.decorator_list:
                    if isinstance(decorator, ast.Call) and name(decorator.func) in {'field_validator','model_validator'}:
                        custom.append(part.name)
        classes[label] = fields
        if custom:
            validators[label] = custom
        return fields
    def is_model(label, chain=()):
        if label in chain:
            raise ValueError('cyclic inheritance')
        return any(name(base) in {'Model','BaseModel'} or name(base) in candidates and is_model(name(base),chain+(label,)) for base in candidates[label].bases)
    for label in candidates:
        if label != 'Model' and is_model(label):
            model(label)
    if not review_roles:
        raise ValueError('canonical closed review artifact roles unavailable')
    # Every ref must be backed by these exact source snapshots; never invent a DTO.
    def refs(value):
        if isinstance(value, dict):
            if value.get('kind') == 'ref' and value['name'] not in aliases and value['name'] not in classes:
                raise ValueError('unresolved canonical reference: ' + value['name'])
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(aliases);refs(classes)
    if not http_routes or any(model not in classes for model in http_routes.values()):
        raise ValueError('canonical HTTP route registry unavailable')
    literals = {}
    for label, value in aliases.items():
        if value['kind'] == 'literal':
            literals[label] = value['values']
    for label, fields in classes.items():
        for field, description in fields.items():
            if description['rule']['kind'] == 'literal':
                literals[label+'_'+field] = description['rule']['values']
    literals['ReviewArtifactRoles'] = review_roles
    output = ['// GENERATED by web/tools/generate_bindings.py. Do not edit.',
              '// Canonical Python AST only; custom validators are NOT executed or certified.']
    for label in sorted(aliases):
        output.append('export type '+label+' = '+ts(aliases[label])+';')
    for label in sorted(classes):
        output.append('export interface '+label+' {')
        for field, description in classes[label].items():
            output.append('  '+json.dumps(field)+('' if description['required'] else '?')+': '+ts(description['rule'])+';')
        output.append('}')
    output.append('export interface CanonicalTypes {')
    for label in sorted(set(aliases) | set(classes)):
        output.append('  '+json.dumps(label)+': '+label+';')
    output.append('}')
    for label, value in [('HTTP_RESPONSE_MODELS',http_routes),('SOURCE_HASHES',hashes),('CONSTANTS',constants),('LITERALS',literals),('MODEL_FIELDS',{k:list(v) for k,v in classes.items()}),('CUSTOM_VALIDATORS_UNEXECUTED',validators)]:
        output.append('export const '+label+' = '+json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'))+' as const;')
    output.append('export const SHAPES: Record<string, unknown> = '+json.dumps({**aliases,**{k:{'kind':'model','fields':v} for k,v in classes.items()}},sort_keys=True,ensure_ascii=True,separators=(',',':'))+';')
    output.append('export const MODEL_ORIGINS = '+json.dumps({k:origin[k] for k in classes},sort_keys=True,separators=(',',':'))+' as const;')
    return '\n'.join(output)+'\n'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='static comparison only; no writes')
    args=parser.parse_args()
    content=produce()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding='utf-8') != content:
            raise SystemExit('generated browser contracts are absent/stale; run generate:bindings')
        print('Static generated source comparison: current')
    else:
        OUTPUT.parent.mkdir(parents=True,exist_ok=True)
        OUTPUT.write_text(content,encoding='utf-8',newline='\n')
        print('Wrote static browser bindings from '+str(len(SOURCES))+' canonical source files; no models imported')
if __name__ == '__main__':
    main()
