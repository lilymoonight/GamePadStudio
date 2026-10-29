"""Controller families and SDL positional button semantics; no fabricated hardware state."""
CATALOG = {
    'dualsense': dict(name='DualSense', brand='PLAYSTATION', subtitle='PS5 · DualSense / Edge', accent='#83a8ff', layout='ps',
                      note='本机已验证 DualSense；Edge 扩展键取决于驱动。'),
    'dualshock4': dict(name='DualShock 4', brand='PLAYSTATION', subtitle='PS4 · Share 与触摸板', accent='#b7a1ff', layout='ps',
                       note='已适配 SDL 标准输入，尚待实机验证。'),
    'xbox': dict(name='Xbox', brand='XBOX', subtitle='Series · One · 360', accent='#83d58e', layout='offset',
                 note='已适配 SDL / XInput，尚待实机验证。Share 与背键需要驱动上报；Guide 可能被系统接管。'),
    'switch': dict(name='Switch Pro', brand='NINTENDO', subtitle='Pro · Capture 与 Home', accent='#ef929f', layout='offset',
                   note='已适配 Switch Pro 标准输入，尚待实机验证。按键按物理位置显示；Joy-Con 单侧不在此型号适配范围。'),
    'stadia': dict(name='Stadia Controller', brand='GOOGLE', subtitle='Stadia · Capture 与 Assistant', accent='#ff8a65', layout='offset',
                   note='已适配 Google Stadia 有线与蓝牙模式标准输入，支持截图键与扩展按键映射。'),
    'flightstick': dict(name='飞行摇杆 / HOTAS', brand='FLIGHT / SIM', subtitle='多轴多键 · 支持最高 128 键位', accent='#5cc8ff', layout='offset',
                        note='支持 VKB / Virpil / WinWing / 图马思特等飞行摇杆与节流阀台，原生支持 32 键以外扩展键位（最高 128 键）。'),
    'generic': dict(name='通用手柄 / 外设', brand='SDL / XINPUT', subtitle='第三方 · 标准 XInput 统一映射', accent='#77d0d4', layout='offset',
                    note='支持 SDL 已识别的标准手柄及 DirectInput 通用输入外设。未识别设备按原生按键直通。'),
}


def get_catalog_entry(family, lang=None):
    from .i18n import get_language
    is_en = (lang == 'en') or (lang is None and get_language() == 'en')
    entry = CATALOG.get(family, CATALOG['generic']).copy()
    if is_en:
        en_notes = {
            'dualsense': 'DualSense hardware verified; Edge back paddles depend on driver.',
            'dualshock4': 'Adapted for SDL standard input.',
            'xbox': 'Adapted for SDL / XInput. Share & back paddles require driver support; Guide may be captured by OS.',
            'switch': 'Adapted for Switch Pro standard input. Physical button order.',
            'stadia': 'Adapted for Google Stadia USB & Bluetooth modes with Capture & Assistant buttons.',
            'flightstick': 'Supports HOTAS flight sticks, throttles, and button boxes up to 128 buttons.',
            'generic': 'Supports SDL standard gamepads and DirectInput controllers.',
        }
        en_subtitles = {
            'generic': 'Third-party · Standard XInput unified mapping',
            'dualshock4': 'PS4 · Share & Touchpad',
            'switch': 'Pro · Capture & Home',
            'stadia': 'Stadia · Capture & Assistant',
            'flightstick': 'Multi-axis HOTAS · Up to 128 buttons',
        }
        en_names = {
            'generic': 'Universal Gamepad / Peripherals',
            'flightstick': 'Flight Stick / HOTAS',
        }
        if family in en_notes: entry['note'] = en_notes[family]
        if family in en_subtitles: entry['subtitle'] = en_subtitles[family]
        if family in en_names: entry['name'] = en_names[family]
    return entry


def family_for(controller_type=0, vendor=0, product=0, name=''):
    # Recognize third-party controllers (GameSir, Flydigi, 8BitDo, Beitong, etc.) before SDL's generic XInput type=1/2.
    from .device_quirks import (
        THIRD_PARTY_BRANDS,
        VENDOR_BRAND_NAMES,
        detect_third_party_brand_and_model_from_name,
        lookup_controller_family,
    )
    vid = int(vendor or 0)
    if (vid in VENDOR_BRAND_NAMES and VENDOR_BRAND_NAMES[vid] in THIRD_PARTY_BRANDS) or detect_third_party_brand_and_model_from_name(name):
        return lookup_controller_family(vendor, product, name)
    known={1:'xbox',2:'xbox',4:'dualshock4',5:'switch',6:'flightstick',7:'dualsense'}
    if controller_type in known:return known[controller_type]
    return lookup_controller_family(vendor, product, name)


def button_labels(family='generic', controller_type=0, lang=None):
    from .i18n import get_language
    is_en = (lang == 'en') or (lang is None and get_language() == 'en')
    if is_en:
        labels={0:'A',1:'B',2:'X',3:'Y',4:'Back',5:'Guide',6:'Start',7:'Left Stick Click',8:'Right Stick Click',
                9:'LB',10:'RB',11:'D-Pad ↑',12:'D-Pad ↓',13:'D-Pad ←',14:'D-Pad →',
                15:'Auxiliary',16:'Paddle P1',17:'Paddle P2',18:'Paddle P3',19:'Paddle P4',20:'Touchpad'}
        for i in range(21, 128):
            labels[i] = f'Button {i+1}'
        if family == 'flightstick':
            flight_labels = {0: 'Primary Trigger (Fire)', 1: 'Secondary Trigger (Pick)', 2: 'Thumb Button A', 3: 'Thumb Button B',
                             4: 'Function 1', 5: 'Function 2', 6: 'Menu / Mode', 7: 'Stick Click', 8: 'Sub Stick Click',
                             9: 'Left Finger', 10: 'Right Finger', 11: 'POV Hat ↑', 12: 'POV Hat ↓', 13: 'POV Hat ←', 14: 'POV Hat →'}
            labels.update(flight_labels)
            for i in range(15, 128):
                labels[i] = f'Button {i+1}'
        elif family in ('dualsense','dualshock4'):
            labels.update({0:'×  Cross',1:'○  Circle',2:'□  Square',3:'△  Triangle',4:'Create' if family=='dualsense' else 'Share',
                           5:'PS',6:'Options',7:'L3',8:'R3',9:'L1',10:'R1',15:'Mic / Mute' if family=='dualsense' else 'Auxiliary'})
        elif family=='xbox':
            labels.update({4:'Back' if controller_type==1 else 'View',5:'Xbox',6:'Start' if controller_type==1 else 'Menu',7:'LS',8:'RS',15:'Share'})
        elif family=='switch':
            # SDL_GAMECONTROLLER_USE_BUTTON_LABELS=0 makes A/B/X/Y mean south/east/west/north.
            labels.update({0:'B',1:'A',2:'Y',3:'X',4:'−',5:'Home',6:'＋',9:'L',10:'R',15:'Capture'})
        elif family=='stadia':
            labels.update({4:'Options',5:'Stadia',6:'Menu',7:'L3',8:'R3',9:'L1',10:'R1',15:'Capture',16:'Assistant'})
        return labels

    labels={0:'A',1:'B',2:'X',3:'Y',4:'Back',5:'Guide',6:'Start',7:'左摇杆按下',8:'右摇杆按下',
            9:'LB',10:'RB',11:'方向键 ↑',12:'方向键 ↓',13:'方向键 ←',14:'方向键 →',
            15:'辅助键',16:'背键 P1',17:'背键 P2',18:'背键 P3',19:'背键 P4',20:'触摸板'}
    for i in range(21, 128):
        labels[i] = f'按键 {i+1}'
    if family == 'flightstick':
        flight_labels = {0: '主扳机 (Fire)', 1: '副扳机 (Pick)', 2: '拇指键 A', 3: '拇指键 B',
                         4: '功能键 1', 5: '功能键 2', 6: '菜单/模式', 7: '摇杆下压', 8: '副杆下压',
                         9: '左指键', 10: '右指键', 11: '苦力帽 ↑', 12: '苦力帽 ↓', 13: '苦力帽 ←', 14: '苦力帽 →'}
        labels.update(flight_labels)
        for i in range(15, 128):
            labels[i] = f'按键 {i+1}'
    elif family in ('dualsense','dualshock4'):
        labels.update({0:'×  交叉',1:'○  圆圈',2:'□  方块',3:'△  三角',4:'Create' if family=='dualsense' else 'Share',
                       5:'PS',6:'Options',7:'L3',8:'R3',9:'L1',10:'R1',15:'麦克风' if family=='dualsense' else '辅助键'})
    elif family=='xbox':
        labels.update({4:'Back' if controller_type==1 else 'View',5:'Xbox',6:'Start' if controller_type==1 else 'Menu',7:'LS',8:'RS',15:'Share'})
    elif family=='switch':
        # SDL_GAMECONTROLLER_USE_BUTTON_LABELS=0 makes A/B/X/Y mean south/east/west/north.
        labels.update({0:'B',1:'A',2:'Y',3:'X',4:'−',5:'Home',6:'＋',9:'L',10:'R',15:'Capture'})
    elif family=='stadia':
        labels.update({4:'Options',5:'Stadia',6:'Menu',7:'L3',8:'R3',9:'L1',10:'R1',15:'Capture',16:'Assistant'})
    return labels


def capture_button(family, available=None):
    available=set(range(21) if available is None else available)
    # View is a game input, not the Xbox equivalent of Create. XInput often
    # exposes only the original 15 buttons, even with newer physical hardware.
    if family=='xbox':return 15 if 15 in available else None
    preferred=15 if family in ('xbox','switch','stadia') and 15 in available else 4
    return preferred if preferred in available else None


def controller_defaults(family, available=None):
    available=set(range(21) if available is None else available)
    key=capture_button(family,available);mapping={}
    if key is not None:mapping[str(key)]={'short':{'action':'capture'},'long':{'action':'replay_record'}}
    if 5 in available and family!='xbox':mapping['5']={'short':{'action':'home'},'long':{'action':'none'}}
    return mapping


def axis_labels(family, lang=None):
    from .i18n import get_language
    is_en = (lang == 'en') or (lang is None and get_language() == 'en')
    triggers=('L2','R2') if family in ('dualsense','dualshock4') else ('ZL','ZR') if family=='switch' else ('LT','RT')
    if is_en:
        return ['Left Stick X','Left Stick Y','Right Stick X','Right Stick Y',*triggers]
    return ['左摇杆 X','左摇杆 Y','右摇杆 X','右摇杆 Y',*triggers]


def button_order(family):
    # Face buttons follow each controller's south / east / west / north labels.
    front=[0,1,2,3,9,10,7,8,11,12,13,14]
    system=[15,4,6,5,20] if family=='xbox' else [4,6,5,20,15]
    return front+system+[16,17,18,19]


def desktop_defaults(family,available=None):
    available=set(range(21) if available is None else available)
    mapping=controller_defaults(family,available)
    for key,value in [(0,'Enter'),(1,'Esc'),(2,'Space'),(3,'Tab'),(11,'Up'),(12,'Down'),(13,'Left'),(14,'Right')]:
        if key in available:mapping[str(key)]={'short':{'action':'hold','value':value},'long':{'action':'none'}}
    if family=='dualsense' and 15 in available:mapping['15']={'short':{'action':'volume_mute'},'long':{'action':'none'}}
    return mapping
