"""Provider boundaries for the cloud pronunciation-assessment engines.

Azure Speech Pronunciation Assessment, SpeechSuper and Speechace are evidence
providers like the local engines, but none of them can be integrated honestly
yet: no credentials are available, so no authenticated response has ever been
seen. The response mapping must be designed from a real response, not from
documentation or memory, so there is deliberately no request or mapping code
here. What exists is the boundary:

* which credentials the engine needs, read from the environment only;
* a `blocked` result, schema-valid and machine-readable, when it cannot run;
* the list of things the first real response has to establish before any field
  is mapped (`VERIFICATION_CHECKLIST`).

A cloud call's latency includes the network, so when these engines are
implemented they report `api_latency_ms` (our clock) and, if the provider states
it, `provider_processing_ms` -- never local-style `inference_ms`.
"""

from __future__ import annotations

import os
from pathlib import Path

from pronunciation_lab.benchmark.base import (
    ErrorType,
    PronunciationEngine,
    unavailable_result,
)
from pronunciation_lab.benchmark.schema import ProcessingError, PronunciationResult

# What the first authenticated response must be inspected for, per engine,
# before anything is mapped into the common schema.
VERIFICATION_CHECKLIST = (
    "locale/dialect actually honoured",
    "phone inventory and alphabet of returned phonemes",
    "phoneme-level timing: present? unit (ms / 100-ns ticks)? offset origin?",
    "phoneme-level scores: scale and meaning",
    "N-best / alternative phonemes: present? scored how?",
    "word-level results and how they nest phonemes",
    "omission / insertion / substitution reporting",
    "stress / prosody fields, if any",
    "provider-reported processing time, if any",
    "raw response size, so it can be preserved verbatim",
)


class CloudEngine(PronunciationEngine):
    """Common boundary: credentials in, `blocked` result out until integrated."""

    mode = "cloud"
    version = None

    #: Environment variables that must all be set.
    credential_env_vars: tuple[str, ...] = ()
    #: Human-readable description of the provider API.
    provider_api: str = ""
    #: What client exists for it, as checked when the boundary was written.
    client: str = ""
    locale: str = "en-US"

    def missing_credentials(self) -> list[str]:
        return [name for name in self.credential_env_vars if not os.environ.get(name)]

    def readiness(self) -> dict[str, object]:
        """Machine-readable statement of whether this engine can run, and why not."""
        missing = self.missing_credentials()
        return {
            "engine": self.name,
            "state": "blocked",
            "reason": (
                ErrorType.CREDENTIALS_UNAVAILABLE
                if missing
                else ErrorType.INTEGRATION_UNVERIFIED
            ),
            "required_env": list(self.credential_env_vars),
            "missing_env": missing,
            "provider_api": self.provider_api,
            "client": self.client,
            "locale": self.locale,
        }

    def analyze(
        self,
        audio_path: Path,
        expected_text: str,
        *,
        recording_id: str,
        original_path: Path | None = None,
    ) -> PronunciationResult:
        missing = self.missing_credentials()

        if missing:
            error = ProcessingError(
                type=ErrorType.CREDENTIALS_UNAVAILABLE,
                message=f"{self.name}: missing environment variables {missing}.",
                stage="credentials",
                retryable=False,
            )
        else:
            # Credentials exist, but nothing has been verified against a real
            # response. Refuse rather than send a request whose response would
            # be mapped from assumptions.
            error = ProcessingError(
                type=ErrorType.INTEGRATION_UNVERIFIED,
                message=(
                    f"{self.name}: credentials are present but the request and "
                    "response mapping have not been built from a real response "
                    "yet; see VERIFICATION_CHECKLIST."
                ),
                stage="mapping",
                retryable=False,
            )

        return unavailable_result(
            self,
            audio_path,
            expected_text,
            recording_id=recording_id,
            original_path=original_path,
            status="blocked",
            error=error,
            engine_evidence={
                "provider": self.name,
                "readiness": self.readiness(),
                "verification_checklist": list(VERIFICATION_CHECKLIST),
            },
        )


class AzurePronunciationEngine(CloudEngine):
    name = "azure_pronunciation"
    credential_env_vars = ("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION")
    provider_api = "Azure AI Speech, Pronunciation Assessment"
    client = (
        "azure-cognitiveservices-speech on PyPI (1.52.0 has a macOS arm64 "
        "wheel); not installed until credentials exist"
    )


class SpeechSuperEngine(CloudEngine):
    name = "speechsuper"
    credential_env_vars = ("SPEECHSUPER_APP_KEY", "SPEECHSUPER_SECRET_KEY")
    provider_api = "SpeechSuper pronunciation assessment HTTP API"
    client = "no Python SDK on PyPI; HTTP API only"


class SpeechaceEngine(CloudEngine):
    name = "speechace"
    credential_env_vars = ("SPEECHACE_API_KEY",)
    provider_api = "Speechace pronunciation scoring HTTP API"
    client = "no Python SDK on PyPI; HTTP API only"
