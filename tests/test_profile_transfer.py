"""Portable preset validation, privacy, compatibility and bounded file access."""
import copy
import itertools
import json

import pytest

from gamepadstudio.profile_transfer import (
    MAX_PROFILE_BYTES, MAX_PROFILE_MAPPINGS, PROFILE_FORMAT, export_profile,
    load_profile_file, preview_profile_import, save_profile_file,
)
from gamepadstudio.mapping_engine import input_sources


def state(family='dualsense', touch=True, fingers=2, buttons=None, **extra):
    result = dict(family=family, device_key='physical:private', instance_id=11,
                  is_gamecontroller=True, available_buttons=list(range(21)) if buttons is None else buttons,
                  available_axes=list(range(6)), touchpad=touch, touchpad_fingers=fingers,
                  model_key='dualsense:054c:0ce6:01234567890123456789012345678901')
    result.update(extra)
    return result


def mapping(action='hold', value='Space', **extra):
    binding = {'action': action}
    if value is not None:
        binding['value'] = value
    binding.update(extra)
    return {'short': binding, 'long': {'action': 'none'}}


def package(mappings=None, family='dualsense', **source):
    return {'format': PROFILE_FORMAT, 'version': 1,
            'source': dict(family=family, input_kind='sdl_gamecontroller', **source),
            'profile': {'name': '我的预设', 'mode': 'kbm',
                        'mappings': {'0': mapping()} if mappings is None else mappings,
                        'options': {'right_stick_mouse': True,
                                    'input': {'stick_press': .24, 'stick_release': .17,
                                              'walk_press': .62, 'walk_release': .72,
                                              'chord_window': .055},
                                    'mouse': {'mode': 'game', 'sensitivity': 24., 'deadzone': .09,
                                              'y_ratio': .7, 'edge_boost': 1.45}}}}


def config(preset, target):
    return {'profiles': {preset['profile']['name']: preset['profile']['mappings']},
            'profile_modes': {preset['profile']['name']: preset['profile']['mode']},
            'profile_options': {preset['profile']['name']: preset['profile']['options']},
            'profile_devices': {preset['profile']['name']: target['device_key']}}


def test_export_and_import_roundtrip_preserves_bindings_and_feel():
    target = state()
    original = package({'0': mapping(), '0+9': mapping('shortcut', 'Ctrl+S'),
                        'TP:tap': mapping('shortcut', 'T')})
    cfg = config(original, target); before = copy.deepcopy(cfg)
    exported = export_profile(cfg, target, '我的预设')
    result = preview_profile_import(exported, target)
    assert result['profile'] == original['profile']
    assert [item['trigger'] for item in result['accepted']] == ['0', '0+9', 'TP:tap']
    assert result['skipped'] == [] and cfg == before
    assert exported is not original and result['profile'] is not exported['profile']


@pytest.mark.parametrize('invert_y', [False, True])
def test_invert_y_roundtrips_through_export_file_and_import_preview(tmp_path, invert_y):
    target = state()
    original = package()
    original['profile']['options']['mouse']['invert_y'] = invert_y
    exported = export_profile(config(original, target), target, '我的预设')
    path = tmp_path / '垂直视角.gamepadstudio-profile.json'
    save_profile_file(path, exported)
    loaded = load_profile_file(path)
    result = preview_profile_import(loaded, target)
    assert result['profile']['options']['mouse']['invert_y'] is invert_y
    assert result['profile'] == original['profile']


def test_old_portable_v1_without_invert_y_is_compatible_and_defaults_off():
    original = package()
    assert 'invert_y' not in original['profile']['options']['mouse']
    result = preview_profile_import(original, state())
    assert result['profile']['options']['mouse'].get('invert_y', False) is False
    assert result['profile'] == original['profile']


def test_toggle_bindings_roundtrip_without_changing_old_hold_bindings(tmp_path):
    target = state()
    original = package({'0': mapping('hold', 'W', mode='toggle'),
                        '1': mapping('mouse_hold', 'left', mode='toggle'),
                        '2': mapping('hold', 'S')})
    exported = export_profile(config(original, target), target, '我的预设')
    path = tmp_path / 'toggle.gamepadstudio-profile.json'
    save_profile_file(path, exported)
    result = preview_profile_import(load_profile_file(path), target)
    assert result['profile']['mappings'] == original['profile']['mappings']
    assert 'mode' not in result['profile']['mappings']['2']['short']


@pytest.mark.parametrize('mode', [None, True, 1, '', 'turbo', 'TOGGLE', [], {}])
def test_portable_toggle_rejects_invalid_modes(mode):
    original = package({'0': mapping('hold', 'W', mode=mode)})
    with pytest.raises(ValueError, match='模式'):
        preview_profile_import(original, state())


def test_portable_toggle_rejects_touch_and_non_hold_actions():
    with pytest.raises(ValueError, match='触摸板手势'):
        preview_profile_import(package({'TP:tap': mapping('hold', 'W', mode='toggle')}), state())
    with pytest.raises(ValueError):
        preview_profile_import(package({'0': mapping('shortcut', 'W', mode='toggle')}), state())


@pytest.mark.parametrize('value', [0, 1, 0., 1., 'true', 'false', '0', '1', None, [], {}])
def test_portable_invert_y_rejects_every_non_boolean(value):
    original = package()
    original['profile']['options']['mouse']['invert_y'] = value
    with pytest.raises(ValueError, match='Y 轴反转'):
        preview_profile_import(original, state())
    with pytest.raises(ValueError, match='Y 轴反转'):
        export_profile(config(original, state()), state(), '我的预设')


def test_export_omits_physical_identity_and_unrelated_preferences():
    target = state(serial='private serial', device_path=r'\\?\hid#private')
    cfg = config(package(), target)
    cfg.update(save_dir=r'C:\Users\Private\Captures', active_profile='我的预设',
               device_settings={'physical:private': {'rumble': .8}},
               controller_profiles={'physical:private': '我的预设'},
               application_profiles={'physical:private': {'enabled': True, 'rules': [
                   {'executable': r'C:\Private\Game.exe', 'profile': '我的预设'}]}},
               profile_sources={'我的预设': '无限暖暖'}, nikki_profile_layouts={'我的预设': {'version': 3}})
    text = json.dumps(export_profile(cfg, target, '我的预设'), ensure_ascii=False)
    for private in ('physical:private', 'Private', 'private serial', 'device_path',
                    'device_settings', 'application_profiles', 'controller_profiles', 'nikki_profile_layouts'):
        assert private not in text


@pytest.mark.parametrize('owner', ['another:device', None])
def test_export_cannot_borrow_another_devices_preset(owner):
    target = state(); cfg = config(package(), target)
    cfg['profile_devices']['我的预设'] = owner
    with pytest.raises(ValueError, match='不属于'):
        export_profile(cfg, target, '我的预设')


def test_cross_family_keeps_physical_positions_and_skips_special_buttons():
    preset = package({str(i): mapping('hold', chr(65 + i)) for i in range(21)})
    result = preview_profile_import(preset, state('switch', touch=False))
    assert set(result['profile']['mappings']) == {str(i) for i in range(15)}
    assert result['profile']['mappings']['0']['short']['value'] == 'A'
    assert {row['trigger'] for row in result['skipped']} == {str(i) for i in range(15, 21)}
    assert all('型号专属' in row['reason'] for row in result['skipped'])


def test_same_family_keeps_reported_microphone_and_back_buttons():
    preset = package({'15': mapping(), '16': mapping(), '20': mapping()})
    result = preview_profile_import(preset, state(buttons=[15, 16]))
    assert set(result['profile']['mappings']) == {'15', '16'}
    assert result['skipped'] == [{'trigger': '20', 'reason': '当前设备不支持此输入'}]


def test_unsupported_chord_is_skipped_whole_instead_of_reduced():
    preset = package({'0': mapping(), '0+16': mapping('shortcut', 'Ctrl+S')})
    result = preview_profile_import(preset, state('xbox', touch=False, buttons=list(range(15))))
    assert set(result['profile']['mappings']) == {'0'}
    assert [row['trigger'] for row in result['skipped']] == ['0+16']
    assert result['profile']['mappings']['0']['short']['value'] == 'Space'


def test_touch_sources_follow_actual_finger_capability():
    preset = package({'TP:tap': mapping('shortcut', 'T'),
                      'TP:two_tap': mapping('shortcut', 'V'),
                      'TP:scroll_up': mapping('wheel', 'up')})
    result = preview_profile_import(preset, state(fingers=1))
    assert list(result['profile']['mappings']) == ['TP:tap']
    assert {row['trigger'] for row in result['skipped']} == {'TP:two_tap', 'TP:scroll_up'}
    assert preview_profile_import(preset, state(touch=False))['accepted'] == []


def test_cross_family_touch_supported_by_the_real_target_can_be_imported():
    result = preview_profile_import(package({'TP:tap': mapping('shortcut', 'T')}), state('dualshock4'))
    assert len(result['accepted']) == 1


def test_standard_axis_sources_match_across_families_and_filter_missing_axes():
    preset = package({key: mapping() for key in ('LT', 'RT', 'LS:up', 'RS:down', 'LS:inner')})
    result = preview_profile_import(preset, state('xbox', touch=False, available_axes=[0, 1, 4]))
    assert set(result['profile']['mappings']) == {'LT', 'LS:up', 'LS:inner'}
    assert {row['trigger'] for row in result['skipped']} == {'RT', 'RS:down'}


def test_raw_external_devices_require_the_same_model():
    target = state('generic', touch=False, is_gamecontroller=False, model_key='generic:1234:5678:guid')
    cfg = config(package(family='generic'), target)
    exported = export_profile(cfg, target, '我的预设')
    assert exported['source'] == {'family': 'generic', 'input_kind': 'raw_joystick',
                                 'model': 'generic:1234:5678:guid'}
    assert preview_profile_import(exported, target)['accepted']
    other = dict(target, model_key='generic:1234:0000:other')
    with pytest.raises(ValueError, match='相同型号'):
        preview_profile_import(exported, other)
    with pytest.raises(ValueError, match='相同型号'):
        preview_profile_import(exported, state('generic', touch=False))
    with pytest.raises(ValueError, match='相同型号'):
        preview_profile_import(package(family='generic'), target)


@pytest.mark.parametrize('model', [None, '', 'model:serial:private', 'model:path:private',
                                   'model:session:1', r'C:\Private\device'])
def test_raw_source_never_exports_an_identity_or_file_path(model):
    target = state('generic', is_gamecontroller=False, model_key=model)
    with pytest.raises(ValueError):
        export_profile(config(package(family='generic'), target), target, '我的预设')


@pytest.mark.parametrize('trigger,gesture', [('0', 'short'), ('51', 'short'), ('TP:hold', 'long')])
def test_launch_is_rejected_before_filtering_unsupported_inputs(trigger, gesture):
    preset = package({trigger: {'short': {'action': 'none'}, 'long': {'action': 'none'}}})
    preset['profile']['mappings'][trigger][gesture] = {
        'action': 'launch', 'executable': r'C:\Windows\System32\cmd.exe', 'arguments': '/c payload'}
    with pytest.raises(ValueError, match='启动应用或命令'):
        preview_profile_import(preset, state(touch=False))


def test_unimplemented_sequences_are_rejected_even_when_the_source_is_unavailable():
    preset = package({'51': mapping('gamepad_macro', None, sequence=[])})
    with pytest.raises(ValueError, match='连招序列'):
        preview_profile_import(preset, state())


def test_gamepad_targets_and_turbo_remain_portable_configuration():
    preset = package({'0': mapping('gamepad_button', '2'),
                      '1': mapping('gamepad_chord', '9+2+3'),
                      '2': mapping('gamepad_turbo', 'LT', rate_hz=20)})
    preset['profile']['mode'] = 'gamepad'
    result = preview_profile_import(preset, state())
    assert len(result['accepted']) == 3
    assert result['profile']['mappings']['1']['short']['value'] == '2+3+9'
    assert result['profile']['mappings']['2']['short']['rate_hz'] == 20


@pytest.mark.parametrize('binding', [
    {'action': 'gamepad_button', 'value': '0+1'},
    {'action': 'gamepad_chord', 'value': '0+0'},
    {'action': 'gamepad_chord', 'value': 'TP:tap'},
    {'action': 'gamepad_turbo', 'value': '0', 'rate_hz': 31},
    {'action': 'gamepad_turbo', 'value': '0', 'rate_hz': True},
    {'action': 'gamepad_turbo', 'value': '0', 'rate_hz': 15.0},
    {'action': 'hold', 'value': 1},
    {'action': 'shortcut', 'value': 'unknown keyboard name'},
    {'action': 'mouse_click', 'value': 'extra'},
    {'action': 'wheel', 'value': 'left'},
    {'action': 'none', 'executable': 'hidden.exe'},
    {'action': 'none', 'value': 'hidden'},
    {'action': 'hold', 'value': 'W', 'script': 'hidden'},
    {'action': 'new_action'},
    {'value': 'W'},
])
def test_invalid_or_extra_action_parameters_are_rejected(binding):
    preset = package({'0': {'short': binding}})
    with pytest.raises(ValueError):
        preview_profile_import(preset, state())


@pytest.mark.parametrize('section,values', [
    ('input', {'stick_press': .2, 'stick_release': .3}),
    ('input', {'walk_press': .8, 'walk_release': .6}),
    ('input', {'trigger_press': .3}),
    ('input', {'chord_window': .001}),
    ('input', {'stick_press': True, 'stick_release': .1}),
    ('input', {'trigger_curves': {}}),
    ('mouse', {'mode': 'hidden'}),
    ('mouse', {'sensitivity': 0}),
    ('mouse', {'deadzone': 1.}),
    ('mouse', {'y_ratio': float('nan')}),
    ('mouse', {'edge_boost': float('inf')}),
    ('mouse', {'sensitivity': 10 ** 1000}),
    ('mouse', {'pointer': 'hidden'}),
])
def test_feel_options_reject_invalid_types_ranges_and_unknown_fields(section, values):
    preset = package(); preset['profile']['options'] = {section: values}
    with pytest.raises(ValueError):
        preview_profile_import(preset, state())


@pytest.mark.parametrize('value', [1, 'yes', None])
def test_pointer_enabled_option_is_strict_boolean(value):
    preset = package(); preset['profile']['options']['right_stick_mouse'] = value
    with pytest.raises(ValueError):
        preview_profile_import(preset, state())


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(version=True),
    lambda p: p.update(version=2),
    lambda p: p.update(format='studio-config'),
    lambda p: p.update(active_profile='hidden'),
    lambda p: p['source'].update(device_key='private'),
    lambda p: p['source'].update(family=[]),
    lambda p: p['source'].update(input_kind={}),
    lambda p: p['profile'].update(name=''),
    lambda p: p['profile'].update(name='a' * 81),
    lambda p: p['profile'].update(name='hidden\nrow'),
    lambda p: p['profile'].update(mode='unknown'),
    lambda p: p['profile'].update(profile_sources={'hidden': 'factory'}),
    lambda p: p['profile'].update(mappings=[]),
    lambda p: p['profile'].update(options={'rumble': .8}),
    lambda p: p['profile']['mappings']['0'].update(extra='hidden'),
    lambda p: p['profile']['mappings']['0'].update(long_press='0.7'),
    lambda p: p['profile']['mappings']['0'].update(long_press=True),
])
def test_unknown_schema_versions_fields_and_wrong_types_are_rejected(mutate):
    preset = package(); mutate(preset)
    with pytest.raises(ValueError):
        preview_profile_import(preset, state())


def test_aliases_are_canonicalized_and_duplicates_rejected():
    result = preview_profile_import(package({'LB+A': mapping()}), state())
    assert list(result['profile']['mappings']) == ['0+9']
    with pytest.raises(ValueError, match='重复'):
        preview_profile_import(package({'0': mapping(), 'A': mapping()}), state())


def test_touch_chords_or_long_output_are_rejected():
    with pytest.raises(ValueError):
        preview_profile_import(package({'0+TP:tap': mapping()}), state())
    preset = package({'TP:tap': mapping('shortcut', 'T')})
    preset['profile']['mappings']['TP:tap']['long'] = {'action': 'hold', 'value': 'P'}
    with pytest.raises(ValueError, match='一次触发'):
        preview_profile_import(preset, state())


def test_preview_signature_includes_semantics_as_well_as_available_inputs():
    first = preview_profile_import(package(), state())['input_signature']
    second = preview_profile_import(package(), state('switch'))['input_signature']
    assert first['inputs'] == second['inputs'] == sorted(input_sources(state()))
    assert first != second
    assert first['input_kind'] == 'sdl_gamecontroller' and first['model'] == ''


def test_preview_results_are_independent_copies_and_do_not_mutate_package():
    preset = package(); before = copy.deepcopy(preset)
    result = preview_profile_import(preset, state())
    result['accepted'][0]['mapping']['short']['value'] = 'X'
    assert result['profile']['mappings']['0']['short']['value'] == 'Space'
    assert preset == before


def test_empty_preset_and_missing_gesture_defaults_are_supported():
    assert preview_profile_import(package({}), state())['accepted'] == []
    preset = package({'0': {'short': {'action': 'none', 'value': ''}}})
    result = preview_profile_import(preset, state())
    assert result['profile']['mappings']['0'] == {'short': {'action': 'none'}, 'long': {'action': 'none'}}


def test_mapping_count_is_bounded():
    triggers = ['+'.join(pair) for pair in itertools.combinations([str(i) for i in range(40)], 2)]
    preset = package({key: mapping() for key in triggers[:MAX_PROFILE_MAPPINGS + 1]})
    with pytest.raises(ValueError, match='512'):
        preview_profile_import(preset, state())


def test_atomic_file_roundtrip_accepts_unicode_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / '暖暖.gpspreset.json'; preset = package()
    assert save_profile_file(path, preset) == path
    assert load_profile_file(path) == preset
    assert list(tmp_path.iterdir()) == [path]


def test_loader_accepts_bom_and_rejects_duplicate_fields(tmp_path):
    path = tmp_path / 'preset.json'
    path.write_text(json.dumps(package(), ensure_ascii=False), encoding='utf-8-sig')
    assert load_profile_file(path)['profile']['name'] == '我的预设'
    content = json.dumps(package()).replace('"version": 1', '"version": 1, "version": 1')
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError, match='重复字段'):
        load_profile_file(path)


@pytest.mark.parametrize('data', [b'not json', b'\xff\xfe', b'{"bad": NaN}', b'{"bad": Infinity}'])
def test_invalid_utf8_json_and_nonfinite_constants_are_rejected(tmp_path, data):
    path = tmp_path / 'preset.json'; path.write_bytes(data)
    with pytest.raises(ValueError):
        load_profile_file(path)


def test_file_byte_limit_is_enforced_before_parsing(tmp_path):
    path = tmp_path / 'preset.json'
    content = json.dumps(package()).encode('utf-8')
    path.write_bytes(b' ' * (MAX_PROFILE_BYTES - len(content)) + content)
    assert load_profile_file(path)['format'] == PROFILE_FORMAT
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='256 KiB'):
        load_profile_file(path)


def test_oversized_in_memory_package_and_failed_save_leave_old_file_intact(tmp_path):
    path = tmp_path / 'preset.json'; original = package(); save_profile_file(path, original)
    malicious = package(); malicious['profile']['name'] = 'x' * MAX_PROFILE_BYTES
    with pytest.raises(ValueError, match='256 KiB'):
        save_profile_file(path, malicious)
    assert load_profile_file(path) == original
    assert list(tmp_path.iterdir()) == [path]
