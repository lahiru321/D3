"""Microphones that are connected right now (Windows Core Audio), for the tray's Microphone menu.

Needs COM initialised on the calling thread.
"""

from dataclasses import dataclass

from pycaw.constants import DEVICE_STATE, EDataFlow
from pycaw.pycaw import AudioUtilities


@dataclass(frozen=True)
class InputDevice:
    name: str
    is_default: bool
    # A Bluetooth headset mic only works in the hands-free (phone call) profile: while it's
    # in use the headset's sound drops to mono, low quality. Worth warning about.
    bluetooth: bool = False


def _is_bluetooth(device) -> bool:
    return any(isinstance(v, str) and ("BTHHFENUM" in v.upper() or "BTHENUM" in v.upper())
               for v in device.properties.values())


def active_inputs() -> list[InputDevice]:
    default = AudioUtilities.GetMicrophone()
    default_id = default.GetId() if default else None
    devices = AudioUtilities.GetAllDevices(data_flow=EDataFlow.eCapture.value,
                                           device_state=DEVICE_STATE.ACTIVE.value)
    return sorted((InputDevice(d.FriendlyName, d.id == default_id, _is_bluetooth(d)) for d in devices if d.FriendlyName),
                  key=lambda d: (not d.is_default, d.name.lower()))
