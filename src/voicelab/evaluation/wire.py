"""Closed metadata views derived from the canonical server payload registry.

Removed content is never reconstructed. These models check retained structure,
types and bounds; full-wire validators run at capture before the privacy filter.
Neither a projection nor a producer receipt proves omitted content independently.
"""
from __future__ import annotations

from copy import copy
from functools import lru_cache
import types
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter, create_model, model_validator

from voicelab.contracts import Model, ServerMessageVariant
from voicelab.harness.privacy import CONTROL_VALUES, FIELDS, observed_metadata


# Dependencies of canonical validators on models that lose fields. Reuse their
# implementations whenever every dependency survives; omitted content is never
# filled with placeholders. New validators require an explicit projection audit.
_VALIDATOR_FIELDS = {
    ('LicensedControlEvidence', 'opened_observation'): {'approval_observation', 'asset_observation'},
    ('TaskPayload', 'control_view_is_descriptive'): {
        'control_only', 'task_applicability', 'spoken_correctness', 'effect_committed', 'ok'},
    ('AudioPayload', 'persisted_reference'): {'artifact_persisted', 'artifact_hash'},
    ('ControllerSpanEvent', 'ordered_span'): {'start_ns', 'end_ns'},
    ('EndedPayload', 'snapshot_reference'): {'ledger', 'ledger_artifact'},
}


def _retained(name: str) -> bool:
    normalized = name.lower().replace('_', '')
    return (name in FIELDS and normalized not in {'text', 'pcm16', 'audio', 'apikey', 'cookie', 'cookies'}
            and not any(part in normalized for part in
                        ('nonce', 'auth', 'header', 'transcript', 'token', 'credential', 'secret')))


def _type(annotation):
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _model(annotation)
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        return Annotated[_type(args[0]), *args[1:]]
    if origin in (Union, types.UnionType):
        return Union[tuple(_type(arg) for arg in args)]
    if origin is list:
        return list[_type(args[0])]
    if origin is dict:
        return dict[args[0], _type(args[1])]
    return annotation


@lru_cache(maxsize=None)
def _model(model):
    fields = {}
    for name, original in model.model_fields.items():
        if not _retained(name):
            continue
        annotation, field = _type(original.annotation), copy(original)
        # The projection may suppress an out-of-allowlist control string. Its
        # absence is permitted, while a retained value still has its wire type.
        if name in CONTROL_VALUES and field.is_required():
            annotation, field.default = Union[annotation, None], None
        field.annotation = annotation
        fields[name] = (annotation, field)
    if set(fields) == set(model.model_fields):
        return create_model(model.__name__ + 'Metadata', __base__=model, **fields)
    if model.__pydantic_decorators__.field_validators:
        raise ValueError('canonical field validator requires metadata projection audit: ' + model.__name__)
    validators = {}
    for name, validator in model.__pydantic_decorators__.model_validators.items():
        dependencies = _VALIDATOR_FIELDS.get((model.__name__, name))
        if dependencies is None or validator.info.mode != 'after':
            raise ValueError('canonical validator requires metadata projection audit: ' + model.__name__ + '.' + name)
        if not dependencies <= fields.keys():
            if (model.__name__, name) == ('ControllerSpanEvent', 'ordered_span'):
                continue  # Both span bounds are omitted, so ordering remains unknown.
            raise ValueError('canonical validator dependencies removed by metadata projection: ' + model.__name__ + '.' + name)
        validators[name] = model_validator(mode='after')(validator.func)
    return create_model(model.__name__ + 'Metadata', __base__=Model, __validators__=validators, **fields)


@lru_cache(maxsize=1)
def _adapter():
    return TypeAdapter(_type(ServerMessageVariant))


def validate_server_metadata(message: dict) -> None:
    if not isinstance(message, dict) or observed_metadata(message) != message:
        raise ValueError('server metadata is not the closed privacy projection')
    _adapter().validate_python(message)
