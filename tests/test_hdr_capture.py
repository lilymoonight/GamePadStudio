"""HDR regressions use generated color patches, never the user's desktop."""
from pathlib import Path
import ctypes
from dataclasses import replace
import shutil
import subprocess

import numpy as np
import pytest

from gamepadstudio.hdr_capture import (
    DXGIOutput, HDRCaptureError, build_hdr_capture_plan,
    hdr_fp16_to_planar_f32, parse_capture_timestamp, resolve_dxgi_output,
    resolve_display_color, scrgb_to_sdr_filter, tone_map_scrgb,
)


def frame(values, *, white_nits=80):
    rgb = np.asarray(values, dtype=np.float32)
    if rgb.ndim == 1:
        rgb = np.repeat(rgb[:, None], 3, axis=1)
    pixels = np.ones((2, len(rgb), 4), dtype="<f2")
    pixels[..., :3] = rgb[None, ...] * (white_nits / 80)
    return pixels.tobytes(), len(rgb), 2


def pixels(raw, width, height):
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)


def test_lossless_half_bridge_preserves_hdr_and_negative_components():
    raw, width, height = frame([[-0.25, 0.5, 2], [4, 8, 16]])
    planes = np.frombuffer(hdr_fp16_to_planar_f32(raw, width, height),
                           dtype="<f4").reshape(3, height, width)
    np.testing.assert_array_equal(planes[:, 0, 0], [0.5, 2, -0.25])
    np.testing.assert_array_equal(planes[:, 1, 1], [8, 16, 4])


@pytest.mark.parametrize("white_nits", [80, 160, 200, 320])
def test_windows_sdr_white_normalization_keeps_midtones_and_neutral_grays(white_nits):
    raw, width, height = frame([0, 0.003, 0.18, 0.5, 0.75], white_nits=white_nits)
    image = pixels(tone_map_scrgb(raw, width, height, white_nits), width, height)
    # Exact sRGB reference for 18% linear gray; all white slider levels match.
    assert abs(int(image[0, 2, 0]) - 118) <= 1
    assert abs(int(image[0, 3, 0]) - 188) <= 1
    assert np.all(image[:, :, 0] == image[:, :, 1])
    assert np.all(image[:, :, 1] == image[:, :, 2])


def test_hdr_highlights_roll_off_without_clipping_at_scrgb_one():
    raw, width, height = frame([0.5, 0.75, 1, 2, 4, 8], white_nits=200)
    image = pixels(tone_map_scrgb(raw, width, height, 200), width, height)
    levels = image[0, :, 0].astype(int)
    assert np.all(np.diff(levels) > 0)
    assert levels[-1] < 255
    assert levels[0] == 188  # The shoulder does not dim midtones.


def test_color_conversion_outputs_expected_transfer_and_sanitizes_invalid_values():
    raw, width, height = frame([[-0.5, 0.18, 0.5], [np.nan, np.inf, -np.inf]])
    srgb = pixels(tone_map_scrgb(raw, width, height, 80), width, height)
    video = pixels(tone_map_scrgb(raw, width, height, 80, transfer="bt709"), width, height)
    assert tuple(srgb[0, 0]) == (0, 118, 188)
    assert tuple(video[0, 0]) == (0, 125, 191)  # display-referred BT.1886 inverse
    assert tuple(srgb[0, 1]) == (0, 255, 0)


@pytest.mark.parametrize("white_nits", [None, 0, -1, float("nan"), float("inf")])
def test_unknown_sdr_white_is_rejected(white_nits):
    raw, width, height = frame([0.5])
    with pytest.raises(HDRCaptureError, match="white"):
        tone_map_scrgb(raw, width, height, white_nits)


def test_partial_capture_frame_is_rejected():
    with pytest.raises(HDRCaptureError, match="Incomplete"):
        tone_map_scrgb(b"\0" * 7, 1, 1, 80)


def test_parallel_lut_conversion_matches_small_frame_without_channel_hue_shift():
    raw, width, height = frame([[0.1, 0.2, 0.3], [1, 2, 4], [-0.5, 0.8, 2],
                               [0, 0, 0], [8, 8, 8], [0.18, 0.18, 0.18],
                               [np.nan, np.inf, -np.inf], [0.7, 0.4, 0.2]])
    expected = pixels(tone_map_scrgb(raw, width, height, 200), width, height)[0]
    tile = np.frombuffer(raw, dtype="<f2").reshape(height, width, 4)[0]
    large = np.tile(tile[None, ...], (1024, 129, 1))
    actual = pixels(tone_map_scrgb(large.tobytes(), 1032, 1024, 200), 1032, 1024)
    np.testing.assert_array_equal(actual, np.tile(expected[None, ...], (1024, 129, 1)))


def test_output_mapping_uses_actual_adapter_output_and_crop_coordinates():
    outputs = [DXGIOutput(0, 0, r"\\.\DISPLAY8", 0, 0, 1920, 1080),
               DXGIOutput(2, 3, r"\\.\DISPLAY2", -2560, -200, 2560, 1440)]
    bbox = dict(left=-2540, top=-190, width=1280, height=720)
    display = dict(device_name=r"\\.\DISPLAY2", hdr_enabled=True, sdr_white_nits=200)
    plan = build_hdr_capture_plan(bbox, display, 60, outputs=outputs)
    assert plan.output is outputs[1]
    assert "d3d11va=hdr:2" in plan.capture_args
    source = plan.capture_args[plan.capture_args.index("-i") + 1]
    assert "output_idx=3" in source
    assert "offset_x=20:offset_y=10" in source
    assert "output_fmt=16bit:allow_fallback=0" in source
    assert "dup_frames=0" in source
    assert "force_fmt" not in source
    assert "-r" not in plan.capture_args
    assert plan.capture_args[plan.capture_args.index("-enc_time_base") + 1] == "filter"
    assert plan.frame_bytes == 1280 * 720 * 8
    assert plan.converted_frame_bytes == 1280 * 720 * 3
    assert plan.output_color_args == ("-color_primaries", "bt709", "-color_trc", "bt709",
                                      "-colorspace", "bt709", "-color_range", "tv")


def test_wrong_name_and_cross_display_capture_fail_instead_of_mss_fallback():
    outputs = [DXGIOutput(0, 0, "left", -1920, 0, 1920, 1080),
               DXGIOutput(1, 0, "right", 0, 0, 1920, 1080)]
    with pytest.raises(HDRCaptureError, match="cross-display"):
        resolve_dxgi_output(dict(left=-1920, top=0, width=3840, height=1080), outputs=outputs)
    with pytest.raises(HDRCaptureError):
        resolve_dxgi_output(dict(left=0, top=0, width=1920, height=1080), "left", outputs)
    with pytest.raises(HDRCaptureError, match="HDR"):
        build_hdr_capture_plan(outputs[1].bbox,
                               dict(hdr_enabled=None, sdr_white_nits=80), outputs=outputs)


def test_capture_timestamp_parser_preserves_gaps_and_rejects_unavailable_pts():
    assert parse_capture_timestamp(b"HDR_FRAME 2 550000 1/1000000\n") == (2, 0.55)
    assert parse_capture_timestamp("HDR_FRAME 0 -10 1/1000") == (0, -0.01)
    assert parse_capture_timestamp("HDR_FRAME 2 9223372036854775807 1/30") is None
    assert parse_capture_timestamp("HDR_FRAME 2 0 0/0") is None
    assert parse_capture_timestamp("ffmpeg diagnostic") is None


def test_rotated_display_is_rejected_instead_of_returning_wrong_crop():
    output = DXGIOutput(0, 0, "portrait", 0, 0, 1080, 1920, rotation=2)
    with pytest.raises(HDRCaptureError, match="rotated"):
        build_hdr_capture_plan(output.bbox, dict(hdr_enabled=True, sdr_white_nits=80),
                               outputs=[output])


def color_descriptor(color_space=12, bits=10):
    from gamepadstudio.hdr_capture import _OUTPUT_DESC, _OUTPUT_DESC1
    basic = _OUTPUT_DESC()
    encoded_name = "display".encode("utf-16-le")
    for index in range(len(encoded_name) // 2):
        basic.DeviceName[index] = int.from_bytes(encoded_name[index * 2:index * 2 + 2], "little")
    basic.DesktopCoordinates.right = 1920
    basic.DesktopCoordinates.bottom = 1080
    basic.AttachedToDesktop = 1
    basic.Rotation = 1
    extended = _OUTPUT_DESC1()
    for field, _type in _OUTPUT_DESC._fields_:
        setattr(extended, field, getattr(basic, field))
    extended.BitsPerColor = bits
    extended.ColorSpace = color_space
    for key, point in dict(RedPrimary=(.64, .33), GreenPrimary=(.30, .60),
                           BluePrimary=(.15, .06), WhitePoint=(.3127, .3290)).items():
        getattr(extended, key)[:] = point
    extended.MinLuminance = .01
    extended.MaxLuminance = 1000
    extended.MaxFullFrameLuminance = 400
    return basic, extended


@pytest.mark.parametrize("space,bits,expected", [(0, 10, False), (12, 10, True),
                                                (0, 8, False), (12, 12, True),
                                                (1, 16, None), (13, 10, None),
                                                (27, 10, None)])
def test_output6_uses_current_color_space_not_channel_depth(space, bits, expected):
    from gamepadstudio.hdr_capture import _descriptor_color
    basic, extended = color_descriptor(space, bits)
    metadata = _descriptor_color(extended, basic)
    assert metadata["hdr_enabled"] is expected
    assert metadata["color_space"] == space
    assert metadata["bits_per_color"] == bits
    assert metadata["color_metadata_valid"] is True


@pytest.mark.parametrize("invalid", ["luminance_zero", "luminance_nan", "primary_zero",
                                      "primary_nan", "geometry", "name", "bits"])
def test_output6_invalid_or_mismatched_description_stays_unknown(invalid):
    from gamepadstudio.hdr_capture import _descriptor_color
    basic, extended = color_descriptor()
    if invalid == "luminance_zero":
        extended.MaxLuminance = 0
    elif invalid == "luminance_nan":
        extended.MaxLuminance = float("nan")
    elif invalid == "primary_zero":
        extended.RedPrimary[:] = (0, 0)
    elif invalid == "primary_nan":
        extended.RedPrimary[0] = float("nan")
    elif invalid == "geometry":
        extended.DesktopCoordinates.right -= 1
    elif invalid == "name":
        extended.DeviceName[0] = ord("x")
    else:
        extended.BitsPerColor = 0
    metadata = _descriptor_color(extended, basic)
    assert metadata["hdr_enabled"] is None
    assert metadata["color_metadata_valid"] is False


def test_unknown_hdr_fallback_matches_named_display_and_keeps_windows_white():
    output = DXGIOutput(1, 2, "HDR", -1920, 0, 1920, 1080, hdr_enabled=True,
                        color_space=12, bits_per_color=10, color_metadata_valid=True)
    display = dict(device_name="HDR", hdr_enabled=None, sdr_white_nits=200,
                   color_source="QueryDisplayConfig")
    resolved = resolve_display_color(display, output.bbox, outputs=[output])
    assert resolved["hdr_enabled"] is True
    assert resolved["color_source"] == "IDXGIOutput6::GetDesc1"
    assert resolved["sdr_white_nits"] == 200
    assert display["hdr_enabled"] is None
    no_white = resolve_display_color(dict(device_name="HDR", hdr_enabled=None),
                                     output.bbox, outputs=[output])
    assert "sdr_white_nits" not in no_white


def test_known_hdr_source_and_clone_targets_never_get_overridden():
    output = DXGIOutput(0, 0, "display", 0, 0, 1920, 1080, hdr_enabled=True,
                        color_space=12, bits_per_color=10, color_metadata_valid=True)
    known = dict(device_name="display", hdr_enabled=False, color_source="QueryDisplayConfig")
    assert resolve_display_color(known, output.bbox, outputs=[output]) == known
    clone = dict(device_name="display", hdr_enabled=None, targets=[{}, {}])
    assert resolve_display_color(clone, output.bbox, outputs=[output]) == clone
    invalid = resolve_display_color(dict(hdr_enabled=None), output.bbox,
                                     outputs=[replace(output, color_metadata_valid=False)])
    assert invalid["hdr_enabled"] is None


def test_output6_query_uses_correct_interface_method_and_releases_it(monkeypatch):
    import gamepadstudio.hdr_capture as hdr
    basic, extended = color_descriptor()
    calls = []
    def com_method(pointer, index, _result, *_args):
        calls.append(index)
        if index == 0:
            def query(_pointer, guid, result):
                actual = ctypes.cast(guid, ctypes.POINTER(hdr._GUID)).contents
                assert bytes(actual) == bytes(hdr._GUID.from_string("068346e8-aaec-4b84-add7-137f513f77a1"))
                ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
                return 0
            return query
        if index == 27:
            def get_desc(_pointer, result):
                ctypes.memmove(result, ctypes.byref(extended), ctypes.sizeof(extended))
                return 0
            return get_desc
        if index == 2:
            return lambda _pointer: 0
        pytest.fail(f"Unexpected COM method {index}")
    monkeypatch.setattr(hdr, "_com_method", com_method)
    assert hdr._query_output_color(ctypes.c_void_p(1), basic)["hdr_enabled"] is True
    assert calls == [0, 27, 2]


@pytest.fixture
def ffmpeg():
    bundled = Path(__file__).resolve().parents[1] / "bin" / "ffmpeg.exe"
    exe = str(bundled) if bundled.is_file() else shutil.which("ffmpeg")
    if not exe:
        pytest.skip("FFmpeg unavailable for synthetic color validation")
    return exe


def run_ffmpeg(exe, args, data):
    result = subprocess.run([exe, "-hide_banner", "-v", "error", "-nostdin", *args],
                            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result


def test_ffmpeg_fp16_capture_transport_preserves_hdr_bytes_and_source_pts(ffmpeg):
    raw, width, height = frame([[-0.25, 2, 8], [1, 4, 16]])
    result = run_ffmpeg(ffmpeg, ["-f", "rawvideo", "-pixel_format", "rgbaf16le",
                                "-video_size", f"{width}x{height}", "-framerate", "30",
                                "-i", "pipe:0", "-vf", "setpts='if(eq(N,0),0,if(eq(N,1),6,18))'",
                                "-fps_mode", "passthrough", "-enc_time_base", "filter",
                                "-stats_enc_pre", "pipe:2", "-stats_enc_pre_fmt", "HDR_FRAME {n} {pts} {tb}",
                                "-c:v", "rawvideo", "-pix_fmt", "rgbaf16le", "-f", "rawvideo", "pipe:1"],
                        raw * 3)
    assert result.stdout == raw * 3
    timestamps = [parse_capture_timestamp(line) for line in result.stderr.splitlines()]
    assert timestamps == [(0, 0.0), (1, 0.2), (2, 0.6)]


@pytest.mark.parametrize("transfer", ["srgb", "bt709"])
def test_synthetic_ffmpeg_float_graph_matches_direct_cpu_colors(ffmpeg, transfer):
    raw, width, height = frame([0, 0.003, 0.18, 0.5, 0.75, 1, 2, 8], white_nits=200)
    graph = scrgb_to_sdr_filter(200, transfer=transfer)
    if transfer == "bt709":
        graph += ",scale=in_range=tv:out_range=pc:in_color_matrix=bt709,format=rgb24"
    result = run_ffmpeg(ffmpeg, ["-f", "rawvideo", "-pixel_format", "gbrpf32le",
                                "-video_size", f"{width}x{height}", "-i", "pipe:0", "-vf", graph,
                                "-frames:v", "1", "-c:v", "rawvideo", "-pix_fmt", "rgb24",
                                "-f", "rawvideo", "pipe:1"],
                        hdr_fp16_to_planar_f32(raw, width, height))
    expected = pixels(tone_map_scrgb(raw, width, height, 200, transfer=transfer), width, height)
    decoded = pixels(result.stdout, width, height)
    assert np.max(np.abs(decoded.astype(int) - expected.astype(int))) <= 2
    assert len(set(int(value) for value in decoded[0, 4:, 0])) == 4


def test_synthetic_sdr_video_keeps_bt709_tags_and_neutral_colors(ffmpeg):
    raw, width, height = frame([0, 0.18, 0.5, 0.75, 1, 2, 4, 8], white_nits=200)
    plan = build_hdr_capture_plan(dict(left=0, top=0, width=width, height=height),
                                  dict(hdr_enabled=True, sdr_white_nits=200),
                                  outputs=[DXGIOutput(0, 0, "synthetic", 0, 0, width, height)])
    encoded = run_ffmpeg(ffmpeg, ["-f", "rawvideo", "-pixel_format", "rgb24",
                                 "-video_size", f"{width}x{height}", "-i", "pipe:0",
                                 "-vf", plan.rgb24_encode_filter.replace("yuv420p", "yuv444p"),
                                 "-frames:v", "1", "-c:v", "libx264", "-crf", "0",
                                 *plan.output_color_args, "-f", "mpegts", "pipe:1"],
                         plan.convert_frame(raw))
    decoded = subprocess.run([ffmpeg, "-hide_banner", "-v", "info", "-i", "pipe:0",
                              "-vf", "showinfo,format=rgb24", "-frames:v", "1",
                              "-f", "rawvideo", "pipe:1"], input=encoded.stdout,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert decoded.returncode == 0, decoded.stderr.decode("utf-8", "replace")
    info = decoded.stderr.decode("utf-8", "replace")
    assert "color_range:tv color_space:bt709 color_primaries:bt709 color_trc:bt709" in info
    expected = pixels(plan.convert_frame(raw), width, height)
    actual = pixels(decoded.stdout, width, height)
    assert np.max(np.abs(actual.astype(int) - expected.astype(int))) <= 2
    assert np.all(actual[:, :, 0] == actual[:, :, 1])
    assert np.all(actual[:, :, 1] == actual[:, :, 2])
