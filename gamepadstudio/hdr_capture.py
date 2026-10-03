"""HDR desktop capture plans and lossless scRGB-to-SDR conversion.

Windows' FP16 desktop is linear BT.709 scRGB: 1.0 represents 80 nits,
not PQ. FFmpeg 7.1's swscale half-float conversion clips values above 1.0,
so capture FP16 unchanged and perform the conversion before any 8-bit step.
This module builds commands; it never starts capture itself.
"""
from __future__ import annotations

import ctypes
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import math
import os
import re
import sys
from typing import Iterable, Mapping
import uuid


class HDRCaptureError(RuntimeError):
    """The requested capture cannot preserve HDR desktop colors."""


@dataclass(frozen=True)
class DXGIOutput:
    adapter_index: int
    output_idx: int
    device_name: str
    left: int
    top: int
    width: int
    height: int
    rotation: int = 1  # DXGI_MODE_ROTATION_IDENTITY
    hdr_enabled: bool | None = None
    color_space: int | None = None
    bits_per_color: int | None = None
    max_luminance_nits: float | None = None
    color_metadata_valid: bool | None = None

    @property
    def bbox(self) -> dict[str, int]:
        return dict(left=self.left, top=self.top, width=self.width, height=self.height)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _RECT(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class _OUTPUT_DESC(ctypes.Structure):
    # Explicit UTF-16 units keep the Windows ABI testable on other platforms.
    _fields_ = [("DeviceName", ctypes.c_uint16 * 32), ("DesktopCoordinates", _RECT),
                ("AttachedToDesktop", ctypes.c_int32), ("Rotation", ctypes.c_uint32),
                ("Monitor", ctypes.c_void_p)]


class _OUTPUT_DESC1(ctypes.Structure):
    _fields_ = _OUTPUT_DESC._fields_ + [
        ("BitsPerColor", ctypes.c_uint32), ("ColorSpace", ctypes.c_uint32),
        ("RedPrimary", ctypes.c_float * 2), ("GreenPrimary", ctypes.c_float * 2),
        ("BluePrimary", ctypes.c_float * 2), ("WhitePoint", ctypes.c_float * 2),
        ("MinLuminance", ctypes.c_float), ("MaxLuminance", ctypes.c_float),
        ("MaxFullFrameLuminance", ctypes.c_float)]


def _wide_name(units) -> str:
    return bytes(units).decode("utf-16-le").split("\0", 1)[0]


def _descriptor_color(desc: _OUTPUT_DESC1, basic: _OUTPUT_DESC) -> dict:
    """Interpret a fresh output description without using bit depth as HDR proof."""
    valid = (bool(desc.AttachedToDesktop) and _wide_name(desc.DeviceName) == _wide_name(basic.DeviceName)
             and bytes(desc.DesktopCoordinates) == bytes(basic.DesktopCoordinates)
             and desc.Rotation == basic.Rotation and 8 <= desc.BitsPerColor <= 32)
    points = [tuple(getattr(desc, key))
              for key in ("RedPrimary", "GreenPrimary", "BluePrimary", "WhitePoint")]
    valid = valid and all(math.isfinite(x) and math.isfinite(y) and
                          0 < x < 1 and 0 < y < 1 and x + y <= 1.001 for x, y in points)
    valid = valid and len(set(points[:3])) == 3
    valid = valid and (math.isfinite(desc.MinLuminance) and desc.MinLuminance >= 0 and
                       math.isfinite(desc.MaxLuminance) and desc.MaxLuminance > desc.MinLuminance and
                       math.isfinite(desc.MaxFullFrameLuminance) and
                       0 < desc.MaxFullFrameLuminance <= desc.MaxLuminance)
    # These are active output color spaces, not the capture texture's format.
    # Unknown/intermediate/WCG spaces remain unknown rather than inferred HDR.
    hdr = {0: False, 12: True}.get(int(desc.ColorSpace)) if valid else None
    return dict(hdr_enabled=hdr, color_space=int(desc.ColorSpace),
                bits_per_color=int(desc.BitsPerColor),
                max_luminance_nits=float(desc.MaxLuminance) if valid else None,
                color_metadata_valid=bool(valid))


def _query_output_color(output, basic: _OUTPUT_DESC) -> dict:
    extended = ctypes.c_void_p()
    try:
        iid = _GUID.from_string("068346e8-aaec-4b84-add7-137f513f77a1")
        query = _com_method(output, 0, ctypes.c_int32, ctypes.POINTER(_GUID),
                            ctypes.POINTER(ctypes.c_void_p))
        result = query(output, ctypes.byref(iid), ctypes.byref(extended))
        if result < 0 or not extended:
            return {}
        desc = _OUTPUT_DESC1()
        get_desc = _com_method(extended, 27, ctypes.c_int32, ctypes.POINTER(_OUTPUT_DESC1))
        if get_desc(extended, ctypes.byref(desc)) < 0:
            return {}
        return _descriptor_color(desc, basic)
    finally:
        _release(extended)


def _com_method(pointer, index, result_type, *argument_types):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)
    return prototype(table[index])


def _release(pointer):
    if pointer:
        _com_method(pointer, 2, ctypes.c_uint32)(pointer)


def _check_hresult(result: int, operation: str):
    if result < 0:
        raise HDRCaptureError(f"{operation} failed (0x{result & 0xffffffff:08x})")


def enumerate_dxgi_outputs() -> tuple[DXGIOutput, ...]:
    """Enumerate actual adapter/output indices without reading screen pixels."""
    if sys.platform != "win32":
        raise HDRCaptureError("HDR desktop capture requires Windows DXGI")
    try:
        dxgi = ctypes.WinDLL("dxgi.dll")
        create = dxgi.CreateDXGIFactory1
        create.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_int32
        factory = ctypes.c_void_p()
        iid = _GUID.from_string("770aae78-f26f-4dba-a829-253c83d1b387")
        _check_hresult(create(ctypes.byref(iid), ctypes.byref(factory)), "CreateDXGIFactory1")
        outputs = []
        try:
            enum_adapters = _com_method(factory, 12, ctypes.c_int32, ctypes.c_uint32,
                                        ctypes.POINTER(ctypes.c_void_p))
            adapter_index = 0
            while True:
                adapter = ctypes.c_void_p()
                result = enum_adapters(factory, adapter_index, ctypes.byref(adapter))
                if result & 0xffffffff == 0x887A0002:  # DXGI_ERROR_NOT_FOUND
                    break
                _check_hresult(result, "EnumAdapters1")
                try:
                    enum_outputs = _com_method(adapter, 7, ctypes.c_int32, ctypes.c_uint32,
                                               ctypes.POINTER(ctypes.c_void_p))
                    output_idx = 0
                    while True:
                        output = ctypes.c_void_p()
                        result = enum_outputs(adapter, output_idx, ctypes.byref(output))
                        if result & 0xffffffff == 0x887A0002:
                            break
                        _check_hresult(result, "EnumOutputs")
                        try:
                            desc = _OUTPUT_DESC()
                            get_desc = _com_method(output, 7, ctypes.c_int32,
                                                   ctypes.POINTER(_OUTPUT_DESC))
                            _check_hresult(get_desc(output, ctypes.byref(desc)), "IDXGIOutput::GetDesc")
                            if desc.AttachedToDesktop:
                                name = _wide_name(desc.DeviceName)
                                rect = desc.DesktopCoordinates
                                color = _query_output_color(output, desc)
                                outputs.append(DXGIOutput(adapter_index, output_idx, name,
                                                          rect.left, rect.top,
                                                          rect.right - rect.left,
                                                          rect.bottom - rect.top, int(desc.Rotation),
                                                          **color))
                        finally:
                            _release(output)
                        output_idx += 1
                finally:
                    _release(adapter)
                adapter_index += 1
        finally:
            _release(factory)
        return tuple(outputs)
    except HDRCaptureError:
        raise
    except (OSError, AttributeError) as exc:
        raise HDRCaptureError(f"Windows DXGI enumeration unavailable: {exc}") from exc


def _bbox(value: Mapping) -> dict[str, int]:
    try:
        result = {key: int(value[key]) for key in ("left", "top", "width", "height")}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HDRCaptureError("Capture requires a valid physical-pixel rectangle") from exc
    if result["width"] <= 0 or result["height"] <= 0:
        raise HDRCaptureError("Capture rectangle must have a positive size")
    return result


def resolve_dxgi_output(bbox: Mapping, device_name: str | None = None,
                        outputs: Iterable[DXGIOutput] | None = None) -> DXGIOutput:
    """Match a crop to an actual DXGI output; MSS monitor order is irrelevant."""
    rect = _bbox(bbox)
    available = tuple(enumerate_dxgi_outputs() if outputs is None else outputs)
    expected_name = (device_name or "").rstrip("\0").casefold()
    matches = []
    for output in available:
        if expected_name and output.device_name.casefold() != expected_name:
            continue
        if (output.left <= rect["left"] and output.top <= rect["top"] and
                rect["left"] + rect["width"] <= output.left + output.width and
                rect["top"] + rect["height"] <= output.top + output.height):
            matches.append(output)
    if len(matches) != 1:
        raise HDRCaptureError("HDR capture must fit one uniquely identified display; "
                              "cross-display capture is not supported")
    return matches[0]


def resolve_display_color(display: Mapping, bbox: Mapping,
                           *, outputs: Iterable[DXGIOutput] | None = None) -> dict:
    """Resolve unknown Windows HDR state from a fresh, uniquely matched output.

    Known QueryDisplayConfig values take precedence. Clone targets, incomplete
    descriptions, unsupported output APIs and ambiguous geometry stay unknown.
    SDR white remains the Windows queried value; this API cannot supply it.
    """
    resolved = dict(display)
    if resolved.get("hdr_enabled") in (True, False):
        return resolved
    if len(resolved.get("targets") or ()) > 1:
        return resolved
    try:
        output = resolve_dxgi_output(bbox, resolved.get("device_name"), outputs)
    except HDRCaptureError:
        return resolved
    resolved.update(dxgi_color_space=output.color_space,
                    dxgi_bits_per_color=output.bits_per_color,
                    dxgi_color_metadata_valid=output.color_metadata_valid,
                    dxgi_max_luminance_nits=output.max_luminance_nits)
    if output.hdr_enabled in (True, False) and output.color_metadata_valid is True:
        resolved.update(hdr_enabled=output.hdr_enabled, color_source="IDXGIOutput6::GetDesc1")
    return resolved


def _white_level(white_nits: float) -> float:
    try:
        white = float(white_nits)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HDRCaptureError("The display's SDR white level is unavailable") from exc
    if not math.isfinite(white) or white <= 0:
        raise HDRCaptureError("The display's SDR white level must be positive and finite")
    return white


def _knee(value: float) -> float:
    knee = float(value)
    if not math.isfinite(knee) or not 0 < knee < 1:
        raise ValueError("Highlight knee must be between 0 and 1")
    return knee


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise HDRCaptureError("HDR color conversion requires NumPy") from exc
    return np


def _scrgb_rgb(fp16bytes: bytes, width: int, height: int):
    width, height = int(width), int(height)
    if width <= 0 or height <= 0 or len(fp16bytes) != width * height * 8:
        raise HDRCaptureError("Incomplete FP16 desktop frame")
    np = _numpy()
    pixels = np.frombuffer(fp16bytes, dtype="<f2").reshape(height, width, 4)
    return pixels[..., :3].astype(np.float32)


def hdr_fp16_to_planar_f32(fp16bytes: bytes, width: int, height: int) -> bytes:
    """Convert packed RGBA half to FFmpeg G/B/R float planes without clipping."""
    np = _numpy()
    rgb = _scrgb_rgb(fp16bytes, width, height)
    return np.ascontiguousarray(rgb[..., [1, 2, 0]].transpose(2, 0, 1),
                               dtype="<f4").tobytes()


@lru_cache(maxsize=2)
def _transfer_lut(transfer: str):
    np = _numpy()
    linear = np.linspace(0.0, 1.0, 262144, dtype=np.float32)
    if transfer == "srgb":
        encoded = np.where(linear <= 0.0031308, linear * 12.92,
                           1.055 * np.power(linear, 1.0 / 2.4) - 0.055)
    else:
        # Desktop pixels are display referred; zimg's BT.709 path likewise
        # uses inverse BT.1886 rather than a camera's scene-referred OETF.
        encoded = np.power(linear, 1.0 / 2.4)
    lut = np.rint(encoded * 255.0).astype(np.uint8)
    lut.setflags(write=False)
    return lut


@lru_cache(maxsize=4)
def _tone_luts(white: float, knee: float):
    """Normalize every half-bit pattern once, retaining a shared RGB shoulder."""
    np = _numpy()
    bits = np.arange(65536, dtype="<u2")
    with np.errstate(invalid="ignore"):
        normalized = bits.view("<f2").astype(np.float32)
        normalized *= 80.0 / white
    np.nan_to_num(normalized, copy=False, nan=0.0,
                  posinf=65504.0 * 80.0 / white, neginf=-65504.0 * 80.0 / white)
    # Positive finite half values have the same order as their unsigned bits.
    # Negative and NaN components do not determine the max-RGB signal.
    positive = bits.copy()
    positive[bits >= 0x8000] = 0
    positive[(bits > 0x7c00) & (bits < 0x8000)] = 0
    positive[bits == 0x7c00] = 0x7bff
    signal = np.maximum(normalized, 0.0)
    over = np.maximum(signal - knee, 0.0)
    mapped = knee + (1.0 - knee) * over / (over + 1.0 - knee)
    scale = np.ones_like(signal)
    np.divide(mapped, signal, out=scale, where=signal > knee)
    for table in (normalized, positive, scale):
        table.setflags(write=False)
    return normalized, positive, scale


@lru_cache(maxsize=1)
def _tone_pool():
    return ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1),
                              thread_name_prefix="HDRColor")


def _tone_tile(half, normalized, positive, scale_lut, transfer_lut):
    np = _numpy()
    maximum = positive[half[:, 0]]
    np.maximum(maximum, positive[half[:, 1]], out=maximum)
    np.maximum(maximum, positive[half[:, 2]], out=maximum)
    scale = scale_lut[maximum]
    output = np.empty((len(half), 3), dtype=np.uint8)
    for channel in range(3):
        linear = normalized[half[:, channel]]
        linear *= scale
        np.clip(linear, 0.0, 1.0, out=linear)
        linear *= len(transfer_lut) - 1
        np.rint(linear, out=linear)
        output[:, channel] = transfer_lut[linear.astype(np.uint32)]
    return output


def tone_map_scrgb(fp16bytes: bytes, width: int, height: int, white_nits: float,
                   *, transfer: str = "srgb", knee: float = 0.75) -> bytes:
    """Return SDR RGB24, with SDR midtones intact and smooth highlight rolloff.

    Normalize by 80 / Windows SDR-white nits. Below ``knee`` linear values are
    unchanged. Above it a continuously differentiable shoulder approaches 1;
    all channels share the same scale to preserve neutral gray and color ratios.
    Select sRGB for PNG or BT.709 for BT.709-tagged video; tags alone cannot
    transform the transfer function. Out-of-SDR-gamut components are finally
    clipped after tone mapping. This is not a display-profile color transform.
    """
    if transfer not in ("srgb", "bt709"):
        raise ValueError("Output transfer must be srgb or bt709")
    white, threshold = _white_level(white_nits), _knee(knee)
    np = _numpy()
    width, height = int(width), int(height)
    if width <= 0 or height <= 0 or len(fp16bytes) != width * height * 8:
        raise HDRCaptureError("Incomplete FP16 desktop frame")
    half = np.frombuffer(fp16bytes, dtype="<u2").reshape(-1, 4)
    normalized, positive, scale = _tone_luts(white, threshold)
    transfer_lut = _transfer_lut(transfer)
    # Small strips fit cache and bound scratch memory; NumPy releases the GIL.
    # Tone scaling remains common to all three channels, preserving color hue.
    tile_size = width * 128
    tiles = (half[offset:offset + tile_size] for offset in range(0, len(half), tile_size))
    def convert(tile):
        return _tone_tile(tile, normalized, positive, scale, transfer_lut)
    if width * height >= 1024 * 1024 and (os.cpu_count() or 1) > 1:
        encoded = _tone_pool().map(convert, tiles)
    else:
        encoded = map(convert, tiles)
    return np.concatenate(tuple(encoded), axis=0).tobytes()


def tone_map_edr(fp16bytes: bytes, width: int, height: int, *, transfer='bt709') -> bytes:
    """Apple extended-linear-sRGB uses relative SDR white 1, not measured nits.

    The shared shoulder accepts Windows' 80-nit scRGB reference as a unit
    conversion of exactly one. No macOS SDR luminance measurement is invented.
    This is also the CPU reference for the native Core Image float pipeline.
    """
    return tone_map_scrgb(fp16bytes, width, height, 80.0, transfer=transfer)


def scrgb_to_sdr_filter(white_nits: float, *, transfer: str = "bt709",
                        knee: float = 0.75, output_format: str | None = None) -> str:
    """A high-precision FFmpeg graph for already converted GBRPF32LE input.

    Never feed RGB half through an automatically inserted swscale conversion.
    GEQ expresses the same asymptotic shoulder as ``tone_map_scrgb``.
    """
    white, threshold = _white_level(white_nits), _knee(knee)
    if transfer not in ("srgb", "bt709"):
        raise ValueError("Output transfer must be srgb or bt709")
    norm = format(80.0 / white, ".12g")
    shoulder = format(threshold, ".12g")
    room = format(1.0 - threshold, ".12g")
    signal = f"max(max(r(X,Y),g(X,Y)),b(X,Y))*{norm}"
    # AVExpr registers prevent repeated RGB loads in each channel expression.
    common = (f"st(0,max({signal},0));st(1,max(ld(0)-{shoulder},0));"
              f"st(2,if(gt(ld(0),{shoulder}),"
              f"({shoulder}+{room}*ld(1)/(ld(1)+{room}))/max(ld(0),1e-20),1));")
    channel_exprs = [f"{channel}='{common}clip({channel}(X,Y)*{norm}*ld(2),0,1)'"
                     for channel in ("r", "g", "b")]
    graph = ("format=gbrpf32le,setparams=range=full:color_primaries=bt709:"
             "color_trc=linear:colorspace=gbr,geq=" + ":".join(channel_exprs) +
             ":interpolation=nearest")
    if transfer == "srgb":
        graph += (",zscale=pin=bt709:tin=linear:min=gbr:rin=full:p=bt709:"
                  "t=iec61966-2-1:m=gbr:r=full:dither=error_diffusion,format=gbrp,format=rgb24")
    else:
        graph += (",zscale=pin=bt709:tin=linear:min=gbr:rin=full:p=bt709:"
                  "t=bt709:m=bt709:r=limited:dither=error_diffusion,format=yuv420p")
    if output_format:
        graph += f",format={output_format}"
    return graph


_TIMESTAMP_RE = re.compile(r"^HDR_FRAME\s+(\d+)\s+(-?\d+)\s+(\d+)/(\d+)\s*$")


def parse_capture_timestamp(line: str | bytes) -> tuple[int, float] | None:
    """Read source-preserving capture stats emitted on FFmpeg stderr."""
    if isinstance(line, bytes):
        line = line.decode("ascii", "replace")
    match = _TIMESTAMP_RE.match(line.strip())
    if not match:
        return None
    index, pts, num, den = map(int, match.groups())
    if den <= 0 or num <= 0 or pts == 9223372036854775807:
        return None
    return index, pts * num / den


@dataclass(frozen=True)
class HDRCapturePlan:
    output: DXGIOutput
    width: int
    height: int
    white_nits: float
    capture_args: tuple[str, ...]
    filter_graph: str
    output_color_args: tuple[str, ...]
    transfer: str = "bt709"

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 8

    @property
    def converted_frame_bytes(self) -> int:
        return self.width * self.height * 3

    @property
    def rgb24_encode_filter(self) -> str:
        """Tag converted frames as well as the codec; first-frame tags matter."""
        if self.transfer == "srgb":
            return ("format=rgb24,setparams=range=full:color_primaries=bt709:"
                    "color_trc=iec61966-2-1:colorspace=gbr")
        return ("scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,"
                "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709")

    def convert_frame(self, frame: bytes) -> bytes:
        return tone_map_scrgb(frame, self.width, self.height, self.white_nits,
                              transfer=self.transfer)


def build_hdr_capture_plan(bbox: Mapping, display: Mapping, fps: float = 30,
                            *, outputs: Iterable[DXGIOutput] | None = None,
                            draw_mouse: bool = False, transfer: str = "bt709") -> HDRCapturePlan:
    """Build a single-display HDR plan; unsupported cases fail explicitly.

    ``capture_args`` includes the raw FP16 output on stdout and source PTS on
    stderr. Pair each ``HDR_FRAME`` stat with its full frame, then preserve that
    time in the encoder's timestamped input. No input ``-r`` rewrites timestamps.
    ``filter_graph`` is for the optional GBRPF32LE bridge, not RGB24 output of
    ``convert_frame`` (which has already been converted and tone mapped).
    """
    if display.get("hdr_enabled") is not True:
        raise HDRCaptureError("HDR display state is unavailable or HDR is disabled")
    white = _white_level(display.get("sdr_white_nits"))
    if transfer not in ("srgb", "bt709"):
        raise ValueError("Output transfer must be srgb or bt709")
    rate = float(fps)
    if not math.isfinite(rate) or not 0 < rate <= 240:
        raise HDRCaptureError("HDR capture frame rate must be between 0 and 240")
    rect = _bbox(bbox)
    output = resolve_dxgi_output(rect, display.get("device_name"), outputs)
    if output.rotation not in (0, 1):
        raise HDRCaptureError("HDR capture of a rotated display is not supported")
    source = (f"ddagrab=output_idx={output.output_idx}:framerate={rate:.12g}:"
              f"offset_x={rect['left'] - output.left}:offset_y={rect['top'] - output.top}:"
              f"video_size={rect['width']}x{rect['height']}:"
              f"draw_mouse={int(draw_mouse)}:output_fmt=16bit:allow_fallback=0:dup_frames=0")
    capture_args = ("-hide_banner", "-loglevel", "error", "-nostats", "-nostdin",
                    "-init_hw_device", f"d3d11va=hdr:{output.adapter_index}",
                    "-filter_hw_device", "hdr", "-f", "lavfi", "-i", source,
                    "-vf", "hwdownload,format=rgbaf16le", "-an", "-fps_mode", "passthrough",
                    "-enc_time_base", "filter", "-stats_enc_pre", "pipe:2",
                    "-stats_enc_pre_fmt", "HDR_FRAME {n} {pts} {tb}",
                    "-c:v", "rawvideo", "-pix_fmt", "rgbaf16le", "-f", "rawvideo", "pipe:1")
    if transfer == "srgb":
        color_args = ("-color_primaries", "bt709", "-color_trc", "iec61966-2-1",
                      "-colorspace", "rgb", "-color_range", "pc")
    else:
        color_args = ("-color_primaries", "bt709", "-color_trc", "bt709",
                      "-colorspace", "bt709", "-color_range", "tv")
    return HDRCapturePlan(output, rect["width"], rect["height"], white, capture_args,
                          scrgb_to_sdr_filter(white, transfer=transfer), color_args, transfer)
