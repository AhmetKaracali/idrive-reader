"""Helpers shared by several steps."""
from __future__ import annotations

from typing import Optional

from ...domain.ecus import ecu_label
from ...transport.uds import TESTER_PRESENT, read_did_request
from ...transport.uds_client import NoResponseError, UdsResponse
from ..context import CaptureContext, StepAborted, VehicleFindings


TYPICAL_ECU_COUNT = 40  # an F30 has roughly this many modules; used until the inventory is known


def expected_ecu_count(findings: VehicleFindings) -> int:
    return len(findings.ecus) or TYPICAL_ECU_COUNT


def try_request(context: CaptureContext, target: int, request: bytes,
                timeout: float) -> Optional[UdsResponse]:
    """One request; ``None`` when the ECU stays silent."""
    try:
        return context.client.request(target, request, timeout=timeout)
    except NoResponseError:
        return None


def try_read_did(context: CaptureContext, target: int, did: int, timeout: float) -> Optional[UdsResponse]:
    return try_request(context, target, read_did_request(did), timeout)


class SilenceWatchdog:
    """
    Detects an ECU that has gone silent mid-step (ignition switched off, ECU asleep).

    :meth:`record_silence` counts silent requests. At ``threshold`` in a row it probes with
    TesterPresent; if the ECU still does not answer it asks the operator to check the ignition and
    **blocks until they answer**. "Continue" resumes; anything else raises :class:`StepAborted`.
    Data already captured is kept either way.
    """

    def __init__(self, context: CaptureContext, target: int, threshold: int = 8):
        self._context = context
        self._target = target
        self._threshold = threshold
        self._silent_in_a_row = 0

    def record_answer(self) -> None:
        self._silent_in_a_row = 0

    def record_silence(self) -> None:
        """May block for an operator decision; raises StepAborted if they give up."""
        self._silent_in_a_row += 1
        if self._silent_in_a_row >= self._threshold:
            self._wait_for_ecu()

    def _wait_for_ecu(self) -> None:
        while True:
            self._context.raise_if_cancelled()
            if try_request(self._context, self._target, TESTER_PRESENT, timeout=2.0) is not None:
                self._silent_in_a_row = 0
                return
            label = ecu_label(self._target)
            self._context.progress.log(f"{label} cevap vermiyor.")
            keep_going = self._context.operator.confirm(
                "Modul cevap vermiyor",
                f"{label} artik cevap vermiyor.\n\n"
                "Kontak kapanmis olabilir. Kontagi tekrar acin (frene basmadan START), "
                "ENET kablosunu kontrol edin ve 'Evet' ile kaldigi yerden devam edin.\n\n"
                "'Hayir' bu adimi bitirir (o ana kadar okunanlar kaydedilir).")
            if not keep_going:
                raise StepAborted(f"{label} cevap vermedi, adim yarida birakildi")
