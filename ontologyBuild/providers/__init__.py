"""Artifact-based ontology providers (no model calls during import)."""

from .base import OntologyArtifact, OntologyImportRequest, OntologyProvider
from .legacy import LegacyOntologyProvider
from .neon_import import NeonImportProvider

__all__ = ["OntologyArtifact", "OntologyImportRequest", "OntologyProvider",
           "LegacyOntologyProvider", "NeonImportProvider"]
