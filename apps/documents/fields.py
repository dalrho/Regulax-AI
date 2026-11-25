import json
import math

from django.core.exceptions import ValidationError
from django.db import models


class EmbeddingVectorField(models.Field):
    """A pgvector column in PostgreSQL and JSON-text vector in local SQLite."""

    description = 'Fixed-dimension embedding vector'

    def __init__(self, *args, dimensions=768, **kwargs):
        self.dimensions = dimensions
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        if connection.vendor == 'postgresql':
            return f'vector({self.dimensions})'
        return 'text'

    def get_internal_type(self):
        return 'TextField'

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs['dimensions'] = self.dimensions
        return name, path, args, kwargs

    def _validated(self, value):
        if value is None:
            return None
        try:
            vector = [float(component) for component in value]
        except (TypeError, ValueError) as error:
            raise ValidationError('Embedding must be a numeric vector.') from error
        if len(vector) != self.dimensions:
            raise ValidationError(f'Embedding must contain exactly {self.dimensions} values.')
        if not all(math.isfinite(component) for component in vector):
            raise ValidationError('Embedding values must be finite numbers.')
        return vector

    def get_prep_value(self, value):
        vector = self._validated(value)
        if vector is None:
            return None
        return '[' + ','.join(format(component, '.9g') for component in vector) + ']'

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValidationError('Stored embedding is not a valid vector.') from error
        return self._validated(value)

    def to_python(self, value):
        if value is None or isinstance(value, list):
            return self._validated(value)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValidationError('Stored embedding is not a valid vector.') from error
        return self._validated(value)
