"""
Well-known B58 DME measurement identifiers (Service 0x22), used to sample the important channels
more often during live capture and to show decoded min/max values in the report.

Source: the community "B58 Enhanced PID map" (thesecretingredient.neocities.org/bmw/dme/b58,
github.com/jcevanco/rcp_bmw_service_0x22), tested by its author on a G29 Supra. NOT verified on an
F30 340i / MG1CS003 yet — the capture itself is what verifies them. Value = raw * multiply / divide + add.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Measurement:
    did: int
    name: str
    unit: str
    length: int
    signed: bool
    multiply: float
    divide: float = 1.0
    add: float = 0.0

    def decode(self, value: bytes) -> Optional[float]:
        """The physical value, or ``None`` if the answer is shorter than expected."""
        if len(value) < self.length:
            return None
        raw = int.from_bytes(value[:self.length], "big", signed=self.signed)
        return raw * self.multiply / self.divide + self.add


_KELVIN = -273.14

KEY_MEASUREMENTS = (
    Measurement(0x4807, "Motor devri", "1/dk", 2, True, 1, 2),
    Measurement(0x4AB1, "Arac hizi", "km/s", 2, True, 0.01),
    Measurement(0x4300, "Motor (sogutma) sicakligi", "C", 2, True, 0.1, 1, _KELVIN),
    Measurement(0x4402, "Yag sicakligi (filtre sonrasi)", "C", 2, False, 0.75, 1, -48),
    Measurement(0x4408, "Yag sicakligi (filtresiz)", "C", 2, True, 0.1, 1, _KELVIN),
    Measurement(0x5822, "Yag sicakligi", "C", 1, False, 1, 1, -60),
    Measurement(0x4436, "Yag basinci (mutlak)", "hPa", 2, False, 1),
    Measurement(0x580F, "Emme havasi sicakligi (IAT)", "C", 1, False, 0.75, 1, -48),
    Measurement(0x5512, "Dis hava sicakligi", "C", 1, False, 0.5, 1, -40),
    Measurement(0x4201, "Ortam basinci", "hPa", 2, False, 10, 256),
    Measurement(0x4205, "Turbo (sarj) basinci", "hPa", 2, False, 20, 256),
    Measurement(0x4AB0, "Turbo basinci hedefi", "hPa", 2, False, 10, 256),
    Measurement(0x4A2D, "Manifold mutlak basinci", "hPa", 2, False, 20, 256),
    Measurement(0x5927, "Wastegate konumu", "%", 1, False, 100, 256),
    Measurement(0x480B, "Gaz pedali", "%", 2, True, 0.01220703),
    Measurement(0x4600, "Gaz kelebegi acisi", "%", 2, True, 0.0078125),
    Measurement(0x5818, "Hava kutle akisi", "kg/sa", 1, False, 4),
    Measurement(0x5640, "Lambda (sonda)", "-", 2, False, 0.00024414),
    Measurement(0x5658, "Ray basinci (filtreli)", "MPa", 2, False, 0.002),
    Measurement(0x56C1, "Ray basinci hedefi", "MPa", 2, False, 0.002),
    Measurement(0x59FA, "Atesleme acisi (gercek)", "derece", 1, True, 0.75),
    Measurement(0x59FD, "Atesleme acisi (tork mudahalesi hedefi)", "derece", 1, True, 0.75),
    Measurement(0x4A49, "Atesleme acisi silindir 1", "derece", 2, True, 1, 10),
    Measurement(0x4A4A, "Atesleme acisi silindir 2", "derece", 2, True, 1, 10),
    Measurement(0x4A4C, "Atesleme acisi silindir 3", "derece", 2, True, 1, 10),
    Measurement(0x4A4D, "Atesleme acisi silindir 4", "derece", 2, True, 1, 10),
    Measurement(0x4A4E, "Atesleme acisi silindir 5", "derece", 2, True, 1, 10),
    Measurement(0x4A4F, "Atesleme acisi silindir 6", "derece", 2, True, 1, 10),
    Measurement(0x5883, "Vuruntu sinyali silindir 1", "V*ms", 4, False, 0.05, 65536),
    Measurement(0x5885, "Vuruntu sinyali silindir 2", "V*ms", 4, False, 0.05, 65536),
    Measurement(0x5886, "Vuruntu sinyali silindir 3", "V*ms", 4, False, 0.05, 65536),
    Measurement(0x5888, "Vuruntu sinyali silindir 4", "V*ms", 4, False, 0.05, 65536),
    Measurement(0x5894, "Vuruntu sinyali silindir 5", "V*ms", 4, False, 0.05, 65536),
    Measurement(0x5895, "Vuruntu sinyali silindir 6", "V*ms", 4, False, 0.05, 65536),
    Measurement(0x4A6C, "Yakit miktari duzeltme faktoru", "%", 1, True, 1, 1000),
    Measurement(0x550D, "Yakittaki alkol (etanol) orani", "%", 1, False, 0.39215686),
    Measurement(0x5938, "Etanol sensoru ham deger", "-", 2, False, 0.00003052),
    Measurement(0x5881, "Vites", "-", 1, False, 1),
    Measurement(0x5608, "Aku gerilimi", "V", 2, True, 0.1),
)

MEASUREMENTS_BY_DID: Dict[int, Measurement] = {m.did: m for m in KEY_MEASUREMENTS}
