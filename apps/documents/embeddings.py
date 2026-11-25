import hashlib
import math
from abc import ABC, abstractmethod

from django.conf import settings


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(ABC):
    model_name = None
    dimensions = None

    @abstractmethod
    def embed_documents(self, texts):
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, text):
        raise NotImplementedError


class SentenceTransformersProvider(EmbeddingProvider):
    """Local BGE model provider; model load is deferred until the first request."""

    QUERY_INSTRUCTION = 'Represent this sentence for searching relevant passages: '

    def __init__(self, model_name=None, dimensions=None, device=None):
        self.model_name = model_name or settings.SOP_EMBEDDING_MODEL
        self.dimensions = dimensions or settings.SOP_EMBEDDING_DIMENSIONS
        self.device = device
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise EmbeddingError(
                    'sentence-transformers is not installed. Install requirements.txt before preparing SOPs.'
                ) from error
            try:
                self._model = SentenceTransformer(self.model_name, device=self.device)
            except Exception as error:
                raise EmbeddingError(
                    f'Could not load embedding model {self.model_name}. Check model access and network/cache settings.'
                ) from error
        model_dimensions = self._model.get_sentence_embedding_dimension()
        if model_dimensions != self.dimensions:
            raise EmbeddingError(
                f'Model {self.model_name} returned dimension {model_dimensions}; configured dimension is {self.dimensions}.'
            )
        return self._model

    def _encode_many(self, texts):
        if not texts:
            return []
        try:
            model = self._load_model()
            results = model.encode(
                texts, normalize_embeddings=True, convert_to_numpy=True,
                show_progress_bar=False, batch_size=32,
            )
            vectors = [[float(value) for value in result] for result in results]
        except EmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingError('The sentence-transformers embedding call failed.') from error
        if any(
            len(vector) != self.dimensions or not all(math.isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise EmbeddingError('The embedding model returned an invalid vector.')
        return vectors

    def embed_documents(self, texts):
        return self._encode_many(list(texts))

    def embed_query(self, text):
        return self._encode_many([self.QUERY_INSTRUCTION + text])[0]


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """Small deterministic test double; never use it to prepare production data."""

    def __init__(self, dimensions=32, model_name='deterministic-test-double'):
        self.dimensions = dimensions
        self.model_name = model_name

    def _embed(self, text):
        vector = [0.0] * self.dimensions
        tokens = text.casefold().split()
        for token in tokens:
            digest = hashlib.sha256(token.encode('utf-8')).digest()
            index = int.from_bytes(digest[:4], 'big') % self.dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)
