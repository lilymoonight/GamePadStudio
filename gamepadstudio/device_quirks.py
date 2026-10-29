"""Unified hardware recognition, 3-tier SDL_GameControllerDB / usb.ids lookup, DInput normalization, and gyro helpers."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

from .i18n import tr

SONY_VENDOR_ID = 0x054C
DUALSENSE_PRODUCT_ID = 0x0CE6
DUALSENSE_EDGE_PRODUCT_ID = 0x0DF2

SDL_GAMECONTROLLERDB_URLS = (
    'https://cdn.jsdelivr.net/gh/mdqinc/SDL_GameControllerDB@master/gamecontrollerdb.txt',
    'https://raw.githubusercontent.com/mdqinc/SDL_GameControllerDB/master/gamecontrollerdb.txt',
)

USB_IDS_URLS = (
    'https://cdn.jsdelivr.net/gh/vcrhonek/hwdata@master/usb.ids',
    'http://www.linux-usb.org/usb.ids',
)

# Tier-1 built-in seed models (0ms lookup, offline-ready)
COMMON_CONTROLLER_MODELS: dict[tuple[int, int], dict[str, Any]] = {
    # Sony PlayStation (HID / DInput)
    (0x054C, 0x0CE6): {'brand': 'Sony', 'model': 'DualSense Wireless Controller', 'family': 'dualsense', 'protocol': 'hidapi', 'gyro': True, 'touchpad': True},
    (0x054C, 0x0DF2): {'brand': 'Sony', 'model': 'DualSense Edge Wireless Controller', 'family': 'dualsense', 'protocol': 'hidapi', 'gyro': True, 'touchpad': True, 'back_paddles': 2},
    (0x054C, 0x05C4): {'brand': 'Sony', 'model': 'DualShock 4 (CUH-ZCT1)', 'family': 'dualshock4', 'protocol': 'hidapi', 'gyro': True, 'touchpad': True},
    (0x054C, 0x09CC): {'brand': 'Sony', 'model': 'DualShock 4 (CUH-ZCT2)', 'family': 'dualshock4', 'protocol': 'hidapi', 'gyro': True, 'touchpad': True},
    (0x054C, 0x0BA0): {'brand': 'Sony', 'model': 'DualShock 4 USB Wireless Adaptor', 'family': 'dualshock4', 'protocol': 'hidapi', 'gyro': True, 'touchpad': True},
    (0x054C, 0x0268): {'brand': 'Sony', 'model': 'DualShock 3 / Sixaxis', 'family': 'dualshock4', 'protocol': 'dinput'},
    # Nintendo Switch (HID)
    (0x057E, 0x2009): {'brand': 'Nintendo', 'model': 'Switch Pro Controller', 'family': 'switch', 'protocol': 'hidapi', 'gyro': True},
    (0x057E, 0x200E): {'brand': 'Nintendo', 'model': 'Switch Joy-Con Charging Grip', 'family': 'switch', 'protocol': 'hidapi', 'gyro': True},
    (0x057E, 0x2006): {'brand': 'Nintendo', 'model': 'Switch Joy-Con (L)', 'family': 'switch', 'protocol': 'hidapi', 'gyro': True},
    (0x057E, 0x2007): {'brand': 'Nintendo', 'model': 'Switch Joy-Con (R)', 'family': 'switch', 'protocol': 'hidapi', 'gyro': True},
    # Google Stadia (HID / Bluetooth)
    (0x18D1, 0x9400): {'brand': 'Google', 'model': 'Stadia Controller', 'family': 'stadia', 'protocol': 'hidapi'},
    # 8BitDo 八位堂 (DirectInput & Multi-mode)
    (0x2DC8, 0x6001): {'brand': '8BitDo 八位堂', 'model': 'SN30 Pro (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b'},
    (0x2DC8, 0x6002): {'brand': '8BitDo 八位堂', 'model': 'Pro+ / Pro 2 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x2DC8, 0x6003): {'brand': '8BitDo 八位堂', 'model': 'Pro 2 Wired (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x2DC8, 0x3016): {'brand': '8BitDo 八位堂', 'model': 'Ultimate 猎户座 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x2DC8, 0x3109): {'brand': '8BitDo 八位堂', 'model': 'Ultimate 2C 猎户座 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x2DC8, 0x9015): {'brand': '8BitDo 八位堂', 'model': 'M30 6-Button (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b'},
    (0x2DC8, 0x2100): {'brand': '8BitDo 八位堂', 'model': 'SN30 Pro / Pro 2 (Switch Mode)', 'family': 'switch', 'protocol': 'hidapi', 'gyro': True},
    (0x2DC8, 0x2101): {'brand': '8BitDo 八位堂', 'model': 'Ultimate (Switch Mode)', 'family': 'switch', 'protocol': 'hidapi', 'gyro': True},
    # Flydigi 飞智 (DirectInput & Multi-mode)
    (0x04B4, 0x2410): {'brand': 'Flydigi 飞智', 'model': 'Flydigi Apex 八爪鱼 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4, 'gyro': True},
    (0x04B4, 0x2412): {'brand': 'Flydigi 飞智', 'model': 'Vader / Apex 黑武士/八爪鱼 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4, 'gyro': True},
    (0x04B4, 0x2411): {'brand': 'Flydigi 飞智', 'model': 'Flydigi Controller (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4},
    (0x1915, 0x0040): {'brand': 'Flydigi 飞智', 'model': 'Flydigi Vader 2 黑武士 2', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4, 'gyro': True},
    (0x37D7, 0x2414): {'brand': 'Flydigi 飞智', 'model': 'Flydigi Direwolf 冰原狼', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True},
    (0xFADE, 0xC001): {'brand': 'Flydigi 飞智', 'model': 'Flydigi Vader 4 / 5 Pro', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4, 'gyro': True},
    # Beitong 北通 (DirectInput / BFM)
    (0x20BC, 0x5500): {'brand': 'Beitong 北通', 'model': 'Asura / Zeus 阿修罗/宙斯 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4},
    (0x20BC, 0x5501): {'brand': 'Beitong 北通', 'model': 'Kunpeng 鲲鹏 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4},
    (0x20BC, 0x5264): {'brand': 'Beitong 北通', 'model': 'Beitong Wireless (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x20BC, 0x504D): {'brand': 'Beitong 北通', 'model': 'Beitong BFM Controller', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x20BC, 0x5052): {'brand': 'Beitong 北通', 'model': 'Beitong G3 Controller', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x11C0, 0x5505): {'brand': 'Beitong 北通', 'model': 'Beitong Wired Controller', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x11C0, 0x5506): {'brand': 'Beitong 北通', 'model': 'Beitong Wireless Controller', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    # GameSir 盖世小鸡 (DirectInput / XInput / Multi-mode)
    (0x3537, 0x1001): {'brand': 'GameSir 盖世小鸡', 'model': 'T4 / Cyclone / Nova (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True},
    (0x3537, 0x1002): {'brand': 'GameSir 盖世小鸡', 'model': 'G7 / Kaleid (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x3537, 0x1003): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir G7 SE / G7 HE', 'family': 'generic', 'protocol': 'xinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x3537, 0x1004): {
        'brand': 'GameSir 盖世小鸡', 'model': 'GameSir T4 Kaleid', 'family': 'generic', 'protocol': 'dinput',
        'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True,
        'sdl_mapping': '03000000373500000410000000000000,GameSir T4 Kaleid,a:b0,b:b1,back:b6,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,dpup:h0.1,guide:b8,leftshoulder:b4,leftstick:b9,lefttrigger:a2,leftx:a0,lefty:a1,rightshoulder:b5,rightstick:b10,righttrigger:a5,rightx:a3,righty:a4,start:b7,x:b2,y:b3,',
    },
    (0x3537, 0x1007): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Nova / RG P01', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True},
    (0x3537, 0x100B): {
        'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Cyclone 2 (风行者 2)', 'family': 'generic', 'protocol': 'dinput',
        'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True,
        'sdl_mapping': '03000000373500000b10000019010000,GameSir Cyclone 2,a:b0,b:b1,back:b6,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,dpup:h0.1,guide:b8,leftshoulder:b4,leftstick:b9,lefttrigger:a2,leftx:a0,lefty:a1,rightshoulder:b5,rightstick:b10,righttrigger:a5,rightx:a3,righty:a4,start:b7,x:b2,y:b3,',
    },
    (0x3537, 0x100E): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Tarantula Pro (九尾狐)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True},
    (0x3537, 0x1010): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Super Nova (超新星)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True},
    (0x3537, 0x1022): {
        'brand': 'GameSir 盖世小鸡', 'model': 'GameSir G7 Pro', 'family': 'generic', 'protocol': 'dinput',
        'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True,
        'sdl_mapping': '03000000373500002210000030020000,GameSir G7 Pro,a:b0,b:b1,x:b3,y:b4,back:b10,guide:b12,start:b11,leftstick:b13,rightstick:b14,leftshoulder:b6,rightshoulder:b7,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1,rightx:a2,righty:a3,lefttrigger:a5,righttrigger:a4,',
    },
    (0x3537, 0x1041): {
        'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Nova Lite (启明星)', 'family': 'generic', 'protocol': 'dinput',
        'dinput_layout': 'type_b', 'back_paddles': 2,
        'sdl_mapping': '03000000373500004110000000010000,GameSir Nova Lite,a:b0,b:b1,x:b3,y:b4,back:b10,guide:b12,start:b11,leftstick:b13,rightstick:b14,leftshoulder:b6,rightshoulder:b7,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,leftx:a0,lefty:a1,rightx:a2,righty:a3,lefttrigger:a5,righttrigger:a4,',
    },
    (0x3537, 0x1046): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Wireless Controller', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x3537, 0x1058): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir K1 Kaleid', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x3537, 0x1094): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Tegenaria Lite', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x3537, 0x1097): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir Kaleid Flux', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x3537, 0x1104): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir X4A Controller', 'family': 'generic', 'protocol': 'xinput', 'dinput_layout': 'type_b', 'back_paddles': 2},
    (0x3537, 0x1116): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir X5 Lite', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b'},
    (0x8555, 0x061B): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir G4 Pro', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 2, 'gyro': True},
    (0x20BC, 0x5656): {'brand': 'GameSir 盖世小鸡', 'model': 'GameSir T4w / T4 Pro', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b', 'back_paddles': 4},
    # Logitech 罗技 DirectInput 经典系列 & 模拟外设
    (0x046D, 0xC216): {'brand': 'Logitech 罗技', 'model': 'Dual Action / F310 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    (0x046D, 0xC218): {'brand': 'Logitech 罗技', 'model': 'RumblePad 2 / F510 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    (0x046D, 0xC219): {'brand': 'Logitech 罗技', 'model': 'Cordless RumblePad 2 / F710 (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    (0x046D, 0xC215): {'brand': 'Logitech 罗技', 'model': 'Extreme 3D Pro Flight Stick', 'family': 'flightstick', 'protocol': 'dinput'},
    # HORI & Qanba 街机摇杆 / 格斗手柄 (DirectInput / HID)
    (0x0F0D, 0x00C1): {'brand': 'HORI', 'model': 'Horipad / Fighting Commander (Switch/DInput)', 'family': 'switch', 'protocol': 'dinput'},
    (0x0F0D, 0x0092): {'brand': 'HORI', 'model': 'Pokken / Arcade Controller (DInput)', 'family': 'generic', 'protocol': 'dinput'},
    (0x2C22, 0x2000): {'brand': 'Qanba 拳霸', 'model': 'Obsidian / Drone Arcade Joystick (DInput)', 'family': 'generic', 'protocol': 'dinput'},
    # Classic DirectInput Generic Chips (DragonRise / Twin USB / ShanWan / GreenAsia)
    (0x0079, 0x0006): {'brand': 'DragonRise', 'model': 'Generic USB Joystick (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    (0x0079, 0x0011): {'brand': 'DragonRise', 'model': 'USB Gamepad (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    (0x0810, 0x0001): {'brand': 'Twin USB', 'model': 'Twin USB Joystick (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    (0x0810, 0x0003): {'brand': 'Twin USB', 'model': 'USB Gamepad (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    (0x2563, 0x0523): {'brand': 'ShanWan 山弯', 'model': 'USB WirelessGamepad (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b'},
    (0x2563, 0x0575): {'brand': 'ShanWan 山弯', 'model': '2.4G Wireless Controller (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_b'},
    (0x0E8F, 0x0003): {'brand': 'GreenAsia', 'model': 'USB Joystick (DInput)', 'family': 'generic', 'protocol': 'dinput', 'dinput_layout': 'type_a'},
    # HOTAS Flight Sticks (DirectInput 128-button)
    (0x231D, 0x0200): {'brand': 'VKB Sim', 'model': 'Gladiator NXT EVO (DInput)', 'family': 'flightstick', 'protocol': 'dinput'},
    (0x3344, 0x4130): {'brand': 'Virpil', 'model': 'VPC Constellation ALPHA (DInput)', 'family': 'flightstick', 'protocol': 'dinput'},
    (0x044F, 0x0402): {'brand': 'Thrustmaster 图马思特', 'model': 'HOTAS Warthog Joystick (DInput)', 'family': 'flightstick', 'protocol': 'dinput'},
    (0x044F, 0xB10A): {'brand': 'Thrustmaster 图马思特', 'model': 'T.16000M FCS (DInput)', 'family': 'flightstick', 'protocol': 'dinput'},
    # Microsoft Xbox (HID / XInput)
    (0x045E, 0x028E): {'brand': 'Microsoft', 'model': 'Xbox 360 Controller', 'family': 'xbox', 'protocol': 'xinput'},
    (0x045E, 0x02A1): {'brand': 'Microsoft', 'model': 'Xbox 360 Wireless Receiver', 'family': 'xbox', 'protocol': 'xinput'},
    (0x045E, 0x02D1): {'brand': 'Microsoft', 'model': 'Xbox One Controller', 'family': 'xbox', 'protocol': 'xinput'},
    (0x045E, 0x02DD): {'brand': 'Microsoft', 'model': 'Xbox One Controller (Firmware 2015)', 'family': 'xbox', 'protocol': 'xinput'},
    (0x045E, 0x02E3): {'brand': 'Microsoft', 'model': 'Xbox Elite Wireless Controller', 'family': 'xbox', 'protocol': 'xinput', 'back_paddles': 4},
    (0x045E, 0x02EA): {'brand': 'Microsoft', 'model': 'Xbox One S Controller', 'family': 'xbox', 'protocol': 'xinput'},
    (0x045E, 0x02FD): {'brand': 'Microsoft', 'model': 'Xbox One S Controller (Bluetooth)', 'family': 'xbox', 'protocol': 'hidapi'},
    (0x045E, 0x0B00): {'brand': 'Microsoft', 'model': 'Xbox Elite Wireless Controller Series 2 (USB)', 'family': 'xbox', 'protocol': 'xinput', 'back_paddles': 4},
    (0x045E, 0x0B05): {'brand': 'Microsoft', 'model': 'Xbox Elite Wireless Controller Series 2 (Bluetooth)', 'family': 'xbox', 'protocol': 'hidapi', 'back_paddles': 4},
    (0x045E, 0x0B12): {'brand': 'Microsoft', 'model': 'Xbox Series X|S Controller (USB)', 'family': 'xbox', 'protocol': 'xinput'},
    (0x045E, 0x0B13): {'brand': 'Microsoft', 'model': 'Xbox Series X|S Controller (Bluetooth)', 'family': 'xbox', 'protocol': 'hidapi'},
    (0x045E, 0x0B22): {'brand': 'Microsoft', 'model': 'Xbox Elite Series 2 Core', 'family': 'xbox', 'protocol': 'hidapi', 'back_paddles': 4},
}

VENDOR_BRAND_NAMES: dict[int, str] = {
    0x054C: 'Sony',
    0x057E: 'Nintendo',
    0x045E: 'Microsoft',
    0x18D1: 'Google',
    0x2DC8: '8BitDo 八位堂',
    0x04B4: 'Flydigi 飞智',
    0x1915: 'Flydigi 飞智',
    0x37D7: 'Flydigi 飞智',
    0xFADE: 'Flydigi 飞智',
    0x20BC: 'Beitong 北通',
    0x11C0: 'Beitong 北通',
    0x3537: 'GameSir 盖世小鸡',
    0x8555: 'GameSir 盖世小鸡',
    0x046D: 'Logitech 罗技',
    0x1532: 'Razer 雷蛇',
    0x0F0D: 'HORI',
    0x2C22: 'Qanba 拳霸',
    0x0079: 'DragonRise',
    0x0810: 'Twin USB',
    0x2563: 'ShanWan 山弯',
    0x0E8F: 'GreenAsia',
    0x231D: 'VKB Sim',
    0x3344: 'Virpil',
    0x4098: 'WinWing 翼胜',
    0x044F: 'Thrustmaster 图马思特',
    0x20D6: 'PowerA',
    0x0E6F: 'PDP',
    0x0738: 'Mad Catz',
    0x06A3: 'Saitek / Logitech Flight',
    0x2F24: 'Mayflash 魔派',
    0x10F5: 'Turtle Beach',
}

KNOWN_GAMING_VENDOR_IDS: tuple[int, ...] = tuple(sorted(set(VENDOR_BRAND_NAMES.keys())))

THIRD_PARTY_BRANDS: frozenset[str] = frozenset({
    'GameSir 盖世小鸡',
    'Flydigi 飞智',
    '8BitDo 八位堂',
    'Beitong 北通',
    'GuliKit 谷粒',
    'Machenike 机械师',
    'ThundeRobot 雷神',
    'BIGBIG WON 墨将',
    'EasySMX 易速马',
    'Logitech 罗技',
    'Razer 雷蛇',
    'HORI',
    'Qanba 拳霸',
    'PowerA',
    'PDP',
    'Mayflash 魔派',
    'Turtle Beach',
})

BRAND_NAME_TOKENS: tuple[tuple[str, str], ...] = (
    ('gamesir', 'GameSir 盖世小鸡'), ('盖世小鸡', 'GameSir 盖世小鸡'), ('小鸡', 'GameSir 盖世小鸡'),
    ('cyclone 2', 'GameSir 盖世小鸡'), ('cyclone pro', 'GameSir 盖世小鸡'),
    ('kaleid', 'GameSir 盖世小鸡'), ('tarantula', 'GameSir 盖世小鸡'), ('tegenaria', 'GameSir 盖世小鸡'),
    ('super nova', 'GameSir 盖世小鸡'), ('nova lite', 'GameSir 盖世小鸡'), ('t4n', 'GameSir 盖世小鸡'),
    ('t4 pro', 'GameSir 盖世小鸡'), ('t4w', 'GameSir 盖世小鸡'), ('t3s', 'GameSir 盖世小鸡'),
    ('g7 se', 'GameSir 盖世小鸡'), ('g7 pro', 'GameSir 盖世小鸡'), ('g7 he', 'GameSir 盖世小鸡'),
    ('flydigi', 'Flydigi 飞智'), ('vader', 'Flydigi 飞智'), ('apex', 'Flydigi 飞智'),
    ('direwolf', 'Flydigi 飞智'), ('冰原狼', 'Flydigi 飞智'), ('黑武士', 'Flydigi 飞智'),
    ('八爪鱼', 'Flydigi 飞智'), ('飞智', 'Flydigi 飞智'),
    ('8bitdo', '8BitDo 八位堂'), ('八位堂', '8BitDo 八位堂'),
    ('beitong', 'Beitong 北通'), ('betop', 'Beitong 北通'), ('北通', 'Beitong 北通'),
    ('宙斯', 'Beitong 北通'), ('鲲鹏', 'Beitong 北通'), ('阿修罗', 'Beitong 北通'),
    ('gulikit', 'GuliKit 谷粒'), ('kingkong', 'GuliKit 谷粒'), ('谷粒', 'GuliKit 谷粒'), ('kk3', 'GuliKit 谷粒'),
    ('machenike', 'Machenike 机械师'), ('机械师', 'Machenike 机械师'),
    ('thunderobot', 'ThundeRobot 雷神'), ('雷神', 'ThundeRobot 雷神'),
    ('bigbig won', 'BIGBIG WON 墨将'), ('墨将', 'BIGBIG WON 墨将'), ('rainbow 2', 'BIGBIG WON 墨将'),
    ('easysmx', 'EasySMX 易速马'), ('易速马', 'EasySMX 易速马'),
    ('logitech', 'Logitech 罗技'), ('rumblepad', 'Logitech 罗技'),
    ('hori', 'HORI'), ('qanba', 'Qanba 拳霸'), ('razer', 'Razer 雷蛇'),
    ('stadia', 'Google'), ('dualsense', 'Sony'), ('dualshock', 'Sony'),
    ('switch', 'Nintendo'), ('xbox', 'Microsoft'),
)

FLIGHTSTICK_NAME_TOKENS: tuple[str, ...] = (
    'hotas', 'flight', 'vkb', 'virpil', 'winwing', 'warthog', 't.16000', 'gladiator', 'extreme 3d',
)


def extract_vid_pid_from_sdl_guid(guid: str) -> tuple[int, int]:
    """Extract (vendor_id, product_id) from a 32-hex-char SDL2 joystick GUID."""
    try:
        raw = bytes.fromhex(str(guid or '').strip())
    except ValueError:
        return 0, 0
    if len(raw) != 16:
        return 0, 0
    if raw[:6] == b'xinput':
        return 0, 0
    vendor = int.from_bytes(raw[4:6], 'little')
    product = int.from_bytes(raw[8:10], 'little')
    return vendor, product


def current_sdl_platform_name() -> str:
    if sys.platform == 'win32':
        return 'Windows'
    if sys.platform == 'darwin':
        return 'Mac OS X'
    return 'Linux'


def parse_sdl_gamecontrollerdb(text: str, platform_name: Optional[str] = None) -> dict[str, Any]:
    """Parse SDL_GameControllerDB (`gamecontrollerdb.txt`) lines into GUID and (VID, PID) lookup tables."""
    by_guid: dict[str, dict[str, Any]] = {}
    by_vid_pid: dict[tuple[int, int], dict[str, Any]] = {}
    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split(',') if p.strip()]
        if len(parts) < 3:
            continue
        guid = parts[0].lower()
        if len(guid) != 32:
            continue
        name = parts[1]
        plat = None
        for token in parts[2:]:
            if token.startswith('platform:'):
                plat = token.split(':', 1)[1].strip()
                break
        if platform_name and plat and plat.lower() != platform_name.lower():
            continue
        vid, pid = extract_vid_pid_from_sdl_guid(guid)
        dinput_layout = 'type_a' if 'a:b1' in line and 'x:b0' in line else 'type_b'
        paddles = sum(1 for k in ('paddle1:', 'paddle2:', 'paddle3:', 'paddle4:') if k in line)
        entry = {
            'guid': guid,
            'vendor': vid,
            'product': pid,
            'model': name,
            'platform': plat or '',
            'dinput_layout': dinput_layout,
            'back_paddles': paddles,
            'sdl_mapping': line if line.endswith(',') else line + ',',
        }
        by_guid[guid] = entry
        if vid and pid and (vid, pid) not in by_vid_pid:
            by_vid_pid[(vid, pid)] = entry
    return {'by_guid': by_guid, 'by_vid_pid': by_vid_pid}


def adapt_sdl_mapping_for_guid(sdl_mapping: str, target_guid: str, target_name: Optional[str] = None) -> str:
    """Rewrite an SDL_GameControllerDB mapping line to bind to `target_guid`."""
    line = (sdl_mapping or '').strip()
    target_guid = (target_guid or '').strip().lower()
    if not line or len(target_guid) != 32:
        return line
    parts = line.split(',')
    if len(parts) < 3:
        return line
    parts[0] = target_guid
    if target_name:
        safe_name = str(target_name).replace(',', ' ').strip()
        if safe_name:
            parts[1] = safe_name
    rebuilt = ','.join(parts)
    return rebuilt if rebuilt.endswith(',') else rebuilt + ','


# Standard XInput slots supported by XUSB_REPORT (buttons 0..14 + LT/RT + LS/RS)
SDL_FIELD_TO_XINPUT_SLOT: dict[str, str] = {
    'a': '0',              # South (A)
    'b': '1',              # East (B)
    'x': '2',              # West (X)
    'y': '3',              # North (Y)
    'back': '4',           # Back / View
    'guide': '5',          # Guide / Xbox
    'start': '6',          # Start / Menu
    'leftstick': '7',      # LS / L3
    'rightstick': '8',     # RS / R3
    'leftshoulder': '9',   # LB
    'rightshoulder': '10', # RB
    'dpup': '11',          # D-Pad Up
    'dpdown': '12',        # D-Pad Down
    'dpleft': '13',        # D-Pad Left
    'dpright': '14',       # D-Pad Right
    'lefttrigger': 'LT',
    'righttrigger': 'RT',
    'leftx': 'LS:x',
    'lefty': 'LS:y',
    'rightx': 'RS:x',
    'righty': 'RS:y',
}

# Non-XInput extended tags in SDL_GameControllerDB (not natively present in XInput XUSB_REPORT)
SDL_EXTENDED_NON_XINPUT_FIELDS: dict[str, str] = {
    'misc1': 'Capture / Share',
    'paddle1': 'Paddle P1',
    'paddle2': 'Paddle P2',
    'paddle3': 'Paddle P3',
    'paddle4': 'Paddle P4',
    'touchpad': 'Touchpad',
}


def generate_xinput_candidate_sequences(max_count: int = 128) -> list[dict[str, Any]]:
    """Deterministically generate conflict-free XInput emission sequences for unused buttons or L2 gestures.

    Uses combinations of XInput modifiers (LB='9', RB='10', LS='7', RS='8', Back='4') and
    action buttons (A='0', B='1', X='2', Y='3', Up='11', Down='12', Left='13', Right='14').
    """
    modifiers = ('9', '10', '7', '8', '4')
    actions = ('0', '1', '2', '3', '11', '12', '13', '14')
    sequences: list[dict[str, Any]] = []
    # Tier 1: 2-button XInput chords (Modifier + Action, e.g. LB+A, LB+B, RB+X...)
    for mod in modifiers:
        for act in actions:
            sequences.append({'action': 'gamepad_chord', 'value': f'{mod}+{act}'})
            if len(sequences) >= max_count:
                return sequences
    # Tier 2: Dual-modifier + Action chords (e.g. LB+RB+A...)
    for i, mod1 in enumerate(modifiers):
        for mod2 in modifiers[i + 1:]:
            for act in actions:
                sequences.append({'action': 'gamepad_chord', 'value': f'{mod1}+{mod2}+{act}'})
                if len(sequences) >= max_count:
                    return sequences
    return sequences[:max_count]


def partition_dinput_buttons_by_sdl_db(num_buttons: int, sdl_mapping: str = '') -> dict[str, Any]:
    """Use an `SDL_GameControllerDB` mapping line to partition a device's raw buttons into:

    1. `xinput_mapped`: `{raw_button_id: xinput_slot}` — raw buttons already bound to standard XInput keys.
    2. `unused_buttons`: `[raw_button_id, ...]` — raw buttons NOT bound to any standard XInput slot
       (including mid-range holes like `b5`, `b8`, `b12` or high-range paddles `b15..bN`).
    3. `extended_hints`: `{raw_button_id: semantic_label}` — if SDL_GameControllerDB tagged an unused
       button as `paddle1..4` or `misc1`.
    4. `allocated_sequences`: `{raw_button_id: xinput_sequence_binding}` — automatically generated
       XInput emission sequences so every unused button emits a valid XInput sequence.
    """
    total = max(0, min(128, int(num_buttons or 0)))
    xinput_mapped: dict[int, str] = {}
    extended_hints: dict[int, str] = {}

    line = (sdl_mapping or '').strip()
    if line:
        for token in line.split(','):
            token = token.strip()
            if ':' not in token:
                continue
            field, source = token.split(':', 1)
            field = field.strip().lstrip('+-~')
            source = source.strip().lstrip('+-~')
            if not source.startswith('b') or not source[1:].isdigit():
                continue
            raw_btn = int(source[1:])
            if not (0 <= raw_btn < total):
                continue
            if field in SDL_FIELD_TO_XINPUT_SLOT:
                xinput_mapped[raw_btn] = SDL_FIELD_TO_XINPUT_SLOT[field]
            elif field in SDL_EXTENDED_NON_XINPUT_FIELDS:
                extended_hints[raw_btn] = SDL_EXTENDED_NON_XINPUT_FIELDS[field]
    else:
        # Fallback when no SDL_GameControllerDB entry exists yet: standard 0..14 base slots
        for b in range(min(15, total)):
            xinput_mapped[b] = str(b)

    unused_buttons = [b for b in range(total) if b not in xinput_mapped]
    candidates = generate_xinput_candidate_sequences(len(unused_buttons))
    allocated_sequences = {
        raw_btn: candidates[idx]
        for idx, raw_btn in enumerate(unused_buttons)
    }

    # Assign conflict-free canonical slots (>= 16) for unused buttons so mid-range
    # raw buttons (e.g. b5, b8, b9, b12) never collide with canonical XInput 0..15 (slot 15 is SHARE/CAPTURE)
    reserved_canonical = set(range(16)) | {b for b in unused_buttons if b >= 16}
    next_free_slot = 16
    canonical_unused_slots: dict[int, int] = {}
    for raw_btn in unused_buttons:
        if raw_btn >= 16:
            canonical_unused_slots[raw_btn] = raw_btn
        else:
            while next_free_slot in reserved_canonical and next_free_slot < 127:
                next_free_slot += 1
            canonical_unused_slots[raw_btn] = next_free_slot
            reserved_canonical.add(next_free_slot)

    canonical_allocated_sequences = {
        str(canonical_unused_slots[raw_btn]): copy.deepcopy(allocated_sequences[raw_btn])
        for raw_btn in unused_buttons
    }
    return {
        'total_buttons': total,
        'xinput_mapped': xinput_mapped,
        'unused_buttons': unused_buttons,
        'extended_hints': extended_hints,
        'allocated_sequences': allocated_sequences,
        'canonical_unused_slots': canonical_unused_slots,
        'canonical_allocated_sequences': canonical_allocated_sequences,
    }


def build_l1_unused_button_mappings(num_buttons: int, sdl_mapping: str = '', use_canonical_slots: bool = False) -> dict[str, dict[str, Any]]:
    """Build ready-to-apply profile mappings for all unused DInput buttons (`L1` base tier)."""
    part = partition_dinput_buttons_by_sdl_db(num_buttons, sdl_mapping)
    result: dict[str, dict[str, Any]] = {}
    if use_canonical_slots:
        for slot_str, seq in part['canonical_allocated_sequences'].items():
            result[slot_str] = {'short': copy.deepcopy(seq), 'long': {'action': 'none'}}
    else:
        for raw_btn, seq in part['allocated_sequences'].items():
            result[str(raw_btn)] = {'short': copy.deepcopy(seq), 'long': {'action': 'none'}}
    return result


def parse_usb_ids(text: str, target_vendor: Optional[int] = None, target_product: Optional[int] = None) -> dict[str, Any]:
    """Parse standard Linux `usb.ids` text to resolve USB vendor and product names."""
    vendors: dict[int, str] = {}
    products: dict[tuple[int, int], str] = {}
    current_vid: Optional[int] = None
    for raw_line in (text or '').splitlines():
        if not raw_line or raw_line.startswith('#'):
            continue
        if raw_line.startswith('\t\t'):
            continue
        if raw_line.startswith('\t'):
            if current_vid is None:
                continue
            if target_vendor is not None and current_vid != target_vendor:
                continue
            body = raw_line[1:].strip()
            if len(body) >= 6:
                hex_part = body[:4]
                try:
                    pid = int(hex_part, 16)
                except ValueError:
                    continue
                pname = body[4:].strip()
                if pname:
                    products[(current_vid, pid)] = pname
                    if target_vendor == current_vid and target_product == pid:
                        break
        elif len(raw_line) >= 6 and raw_line[:4].isalnum():
            try:
                vid = int(raw_line[:4], 16)
            except ValueError:
                current_vid = None
                continue
            current_vid = vid
            vname = raw_line[4:].strip()
            if vname:
                vendors[vid] = vname
    return {'vendors': vendors, 'products': products}


_GENERIC_XINPUT_NAMES: frozenset[str] = frozenset({
    '',
    'controller',
    'joystick',
    'gamepad',
    'usb gamepad',
    'usb controller',
    'wireless controller',
    'xinput controller',
    'xinput controller #1',
    'xinput controller #2',
    'xinput controller #3',
    'xinput controller #4',
    'xbox 360 controller',
    'xbox 360 controller for windows',
    'controller (xbox 360 for windows)',
    'controller (xbox one for windows)',
    'xbox one controller',
    'xbox wireless controller',
})

_THIRD_PARTY_MODEL_KEYWORDS: tuple[tuple[str, tuple[int, int]], ...] = (
    ('cyclone 2', (0x3537, 0x100B)),
    ('cyclone pro', (0x3537, 0x1028)),
    ('t4 kaleid', (0x3537, 0x102A)),
    ('kaleid flux', (0x3537, 0x1097)),
    ('kaleid', (0x3537, 0x1071)),
    ('super nova', (0x3537, 0x1068)),
    ('nova lite', (0x3537, 0x1042)),
    ('t4n lite', (0x3537, 0x104F)),
    ('tarantula pro', (0x3537, 0x107B)),
    ('tarantula', (0x3537, 0x107B)),
    ('tegenaria', (0x3537, 0x1094)),
    ('g7 pro', (0x3537, 0x105A)),
    ('g7 se', (0x3537, 0x1010)),
    ('g7 he', (0x3537, 0x1010)),
    ('gamesir-g7', (0x3537, 0x100E)),
    ('gamesir g7', (0x3537, 0x100E)),
    ('t4 pro', (0x20BC, 0x5656)),
    ('t4w', (0x20BC, 0x5656)),
    ('t3s', (0x3537, 0x1026)),
    ('g4 pro', (0x8555, 0x061B)),
    ('x2 pro', (0x3537, 0x100B)),
    ('x3 type-c', (0x3537, 0x1013)),
    ('x4a', (0x3537, 0x1104)),
    ('x5 lite', (0x3537, 0x1116)),
    ('vader 4', (0x04B4, 0x2412)),
    ('vader 3', (0x04B4, 0x2412)),
    ('vader 2', (0x04B4, 0x2411)),
    ('apex 4', (0x04B4, 0x2413)),
    ('apex 3', (0x04B4, 0x2413)),
    ('direwolf', (0x37D7, 0x2401)),
    ('冰原狼', (0x37D7, 0x2401)),
    ('黑武士', (0x04B4, 0x2412)),
    ('八爪鱼', (0x04B4, 0x2413)),
    ('ultimate 2c', (0x2DC8, 0x310A)),
    ('sn30 pro+', (0x2DC8, 0x6002)),
    ('sn30 pro', (0x2DC8, 0x6001)),
    ('zeus', (0x20BC, 0x5044)),
    ('宙斯', (0x20BC, 0x5044)),
    ('kunpeng', (0x20BC, 0x5501)),
    ('鲲鹏', (0x20BC, 0x5501)),
    ('asura', (0x20BC, 0x5500)),
    ('阿修罗', (0x20BC, 0x5500)),
)


def is_generic_controller_name(name: str) -> bool:
    low = str(name or '').strip().lower()
    if low in _GENERIC_XINPUT_NAMES:
        return True
    if low.startswith('xinput controller'):
        return True
    return False


def clean_controller_model_name(raw_name: str, brand: str = '') -> str:
    """Unwrap Windows 'Controller (GameSir-T4w)' style strings into a clean display model."""
    text = str(raw_name or '').strip()
    if not text:
        return f"{brand.split()[0]} Controller".strip() if brand else 'Controller'
    for prefix in ('controller (', 'xinput controller (', 'xbox 360 controller ('):
        if text.lower().startswith(prefix) and text.endswith(')'):
            inner = text[len(prefix):-1].strip()
            if inner and inner.lower() not in ('xbox 360 for windows', 'xbox one for windows'):
                text = inner
                break
    text = text.replace('Gamesir-', 'GameSir ').replace('GameSir-', 'GameSir ')
    if is_generic_controller_name(text) and brand and brand in THIRD_PARTY_BRANDS:
        return f"{brand.split()[0]} Controller"
    return text


def infer_default_back_paddles(brand: str, model_or_name: str = '') -> int:
    """Infer physical back paddle count for recognized third-party brands when VID:PID is generic/spoofed."""
    low = str(model_or_name or '').lower()
    if 'x5 lite' in low or 'sn30 pro' in low and 'pro+' not in low:
        return 0
    if any(k in low for k in ('t4 pro', 't4w', 'tarantula', 'vader', 'apex', '黑武士', '八爪鱼', 'zeus', '宙斯', 'kunpeng', '鲲鹏', 'kk3', 'g5 pro max', 'rainbow 2', 'elite')):
        return 4
    if brand == 'Flydigi 飞智':
        return 2 if ('direwolf' in low or '冰原狼' in low) else 4
    if brand in (
        'GameSir 盖世小鸡',
        '8BitDo 八位堂',
        'Beitong 北通',
        'GuliKit 谷粒',
        'Machenike 机械师',
        'ThundeRobot 雷神',
        'BIGBIG WON 墨将',
        'EasySMX 易速马',
    ):
        return 2
    return 0


def detect_third_party_brand_and_model_from_name(name: str) -> Optional[dict[str, Any]]:
    """Detect third-party controller brand, model, family, and back paddles from a name or path string."""
    raw = str(name or '').strip()
    if not raw:
        return None
    low = raw.lower().replace('_', ' ')
    normalized_low = low.replace('-', ' ')
    for keyword, vid_pid in _THIRD_PARTY_MODEL_KEYWORDS:
        if keyword in low or keyword in normalized_low:
            entry = dict(COMMON_CONTROLLER_MODELS.get(vid_pid, {}))
            if entry:
                entry.setdefault('vendor', vid_pid[0])
                entry.setdefault('product', vid_pid[1])
                return entry
    for token, brand in BRAND_NAME_TOKENS:
        if brand not in THIRD_PARTY_BRANDS:
            continue
        if token in low or token in normalized_low:
            cleaned = clean_controller_model_name(raw, brand)
            paddles = infer_default_back_paddles(brand, cleaned)
            return {
                'brand': brand,
                'model': cleaned,
                'family': 'generic',
                'back_paddles': paddles,
                'dinput_layout': 'type_b',
            }
    return None


_HOST_USB_CACHE_TS: float = 0.0
_HOST_USB_CACHE_ITEMS: list[dict[str, Any]] = []
_MOCK_HOST_USB_CONTROLLERS: Optional[list[dict[str, Any]]] = None


def probe_host_usb_controllers(force: bool = False) -> list[dict[str, Any]]:
    """Inspect connected USB/HID devices on the host OS to recover real third-party VID:PID and iProduct
    when SDL2 on Windows/macOS reports a spoofed/legacy XInput identity (`045e:028e` or `0000:0000`)."""
    global _HOST_USB_CACHE_TS, _HOST_USB_CACHE_ITEMS
    if _MOCK_HOST_USB_CONTROLLERS is not None:
        return list(_MOCK_HOST_USB_CONTROLLERS)
    if 'pytest' in sys.modules and not force:
        return []
    now = time.monotonic()
    if not force and (now - _HOST_USB_CACHE_TS) < 2.0:
        return list(_HOST_USB_CACHE_ITEMS)

    found: list[dict[str, Any]] = []
    seen_keys: set[tuple[int, int, str]] = set()

    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes

            setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
            DIGCF_PRESENT = 0x00000002
            DIGCF_ALLCLASSES = 0x00000004
            SPDRP_DEVICEDESC = 0x00000000
            SPDRP_HARDWAREID = 0x00000001
            SPDRP_FRIENDLYNAME = 0x0000000C

            class SP_DEVINFO_DATA(ctypes.Structure):
                _fields_ = [
                    ('cbSize', wintypes.DWORD),
                    ('ClassGuid', ctypes.c_byte * 16),
                    ('DevInst', wintypes.DWORD),
                    ('Reserved', ctypes.c_void_p),
                ]

            class DEVPROPKEY(ctypes.Structure):
                _fields_ = [
                    ('fmtid', ctypes.c_byte * 16),
                    ('pid', wintypes.ULONG),
                ]

            # DEVPKEY_Device_BusReportedDeviceDesc: {540B947E-8B40-45BC-A8A2-6A0B894CBDA2}, pid 4
            BUS_REPORTED_KEY = DEVPROPKEY(
                (ctypes.c_byte * 16).from_buffer_copy(
                    bytes.fromhex('7e940b54408bbc45a8a26a0b894cbda2')
                ),
                4,
            )

            setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
            setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
            setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
            setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
            setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ]
            setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
            setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
            setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

            get_prop_w = getattr(setupapi, 'SetupDiGetDevicePropertyW', None)
            if get_prop_w:
                get_prop_w.argtypes = [
                    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(DEVPROPKEY),
                    ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
                ]
                get_prop_w.restype = wintypes.BOOL

            import re
            vid_pid_re = re.compile(r'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})')

            oem_names: dict[tuple[int, int], str] = {}
            try:
                import winreg
                for root_hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(
                            root_hive,
                            r'System\CurrentControlSet\Control\MediaProperties\PrivateProperties\Joystick\OEM',
                        ) as oem_root:
                            idx = 0
                            while True:
                                try:
                                    sub_name = winreg.EnumKey(oem_root, idx)
                                    idx += 1
                                except OSError:
                                    break
                                m = vid_pid_re.search(sub_name)
                                if not m:
                                    continue
                                v_id, p_id = int(m.group(1), 16), int(m.group(2), 16)
                                try:
                                    with winreg.OpenKey(oem_root, sub_name) as sub_k:
                                        val, _ = winreg.QueryValueEx(sub_k, 'OEMName')
                                        if isinstance(val, str) and val.strip():
                                            oem_names[(v_id, p_id)] = val.strip()
                                except OSError:
                                    pass
                    except OSError:
                        pass
            except Exception:
                pass

            for enumerator in ('USB', 'HID'):
                hdev = setupapi.SetupDiGetClassDevsW(None, enumerator, None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
                if not hdev or hdev == ctypes.c_void_p(-1).value:
                    continue
                try:
                    dev_idx = 0
                    info = SP_DEVINFO_DATA()
                    info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
                    buf = ctypes.create_unicode_buffer(512)
                    while setupapi.SetupDiEnumDeviceInfo(hdev, dev_idx, ctypes.byref(info)):
                        dev_idx += 1
                        reg_type = wintypes.DWORD()
                        req_size = wintypes.DWORD()
                        if not setupapi.SetupDiGetDeviceRegistryPropertyW(
                            hdev, ctypes.byref(info), SPDRP_HARDWAREID,
                            ctypes.byref(reg_type), buf, ctypes.sizeof(buf), ctypes.byref(req_size)
                        ):
                            continue
                        hw_id = buf.value
                        m = vid_pid_re.search(hw_id)
                        if not m:
                            continue
                        vid, pid = int(m.group(1), 16), int(m.group(2), 16)
                        bus_desc = ''
                        if get_prop_w:
                            prop_type = wintypes.ULONG()
                            if get_prop_w(
                                hdev, ctypes.byref(info), ctypes.byref(BUS_REPORTED_KEY),
                                ctypes.byref(prop_type), buf, ctypes.sizeof(buf), ctypes.byref(req_size), 0
                            ):
                                bus_desc = buf.value.strip()
                        friendly = ''
                        if setupapi.SetupDiGetDeviceRegistryPropertyW(
                            hdev, ctypes.byref(info), SPDRP_FRIENDLYNAME,
                            ctypes.byref(reg_type), buf, ctypes.sizeof(buf), ctypes.byref(req_size)
                        ):
                            friendly = buf.value.strip()
                        dev_desc = ''
                        if setupapi.SetupDiGetDeviceRegistryPropertyW(
                            hdev, ctypes.byref(info), SPDRP_DEVICEDESC,
                            ctypes.byref(reg_type), buf, ctypes.sizeof(buf), ctypes.byref(req_size)
                        ):
                            dev_desc = buf.value.strip()

                        oem_desc = oem_names.get((vid, pid), '')
                        best_name = bus_desc or oem_desc or friendly or dev_desc
                        tp_match = (
                            detect_third_party_brand_and_model_from_name(bus_desc)
                            or detect_third_party_brand_and_model_from_name(oem_desc)
                            or detect_third_party_brand_and_model_from_name(friendly)
                            or detect_third_party_brand_and_model_from_name(dev_desc)
                        )
                        brand_by_vid = VENDOR_BRAND_NAMES.get(vid, '')
                        if brand_by_vid in THIRD_PARTY_BRANDS or tp_match:
                            key = (vid, pid, best_name)
                            if key not in seen_keys:
                                seen_keys.add(key)
                                found.append({
                                    'vendor': vid,
                                    'product': pid,
                                    'name': best_name,
                                    'bus_reported_name': bus_desc,
                                    'tp_match': tp_match,
                                })
                finally:
                    setupapi.SetupDiDestroyDeviceInfoList(hdev)
        except Exception:
            pass
    elif sys.platform == 'darwin':
        try:
            import plistlib
            import subprocess
            out = subprocess.check_output(
                ['ioreg', '-a', '-r', '-c', 'IOHIDDevice', '-l'],
                timeout=0.8,
                stderr=subprocess.DEVNULL,
            )
            items = plistlib.loads(out)
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    vid = int(item.get('VendorID') or 0)
                    pid = int(item.get('ProductID') or 0)
                    prod = str(item.get('Product') or '').strip()
                    tp_match = detect_third_party_brand_and_model_from_name(prod)
                    brand_by_vid = VENDOR_BRAND_NAMES.get(vid, '')
                    if brand_by_vid in THIRD_PARTY_BRANDS or tp_match:
                        key = (vid, pid, prod)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            found.append({
                                'vendor': vid,
                                'product': pid,
                                'name': prod,
                                'bus_reported_name': prod,
                                'tp_match': tp_match,
                            })
        except Exception:
            pass

    _HOST_USB_CACHE_TS = now
    _HOST_USB_CACHE_ITEMS = found
    return list(found)


def lookup_controller_family(vendor: int = 0, product: int = 0, name: str = '') -> str:
    """Single source of truth for inferring controller family from VID:PID or model name."""
    vid = int(vendor or 0)
    pid = int(product or 0)
    tp_from_name = detect_third_party_brand_and_model_from_name(name)
    if tp_from_name and tp_from_name.get('brand') in THIRD_PARTY_BRANDS:
        return tp_from_name.get('family', 'generic')
    known = COMMON_CONTROLLER_MODELS.get((vid, pid))
    if known and known.get('family'):
        return known['family']
    cached = get_controller_database().get_cached_entry(vid, pid, trigger_async=False)
    if cached and cached.get('family'):
        return cached['family']
    if vid in VENDOR_BRAND_NAMES and VENDOR_BRAND_NAMES[vid] in THIRD_PARTY_BRANDS:
        return 'generic'
    if vid == 0x054C:
        if pid in (0x0CE6, 0x0DF2):
            return 'dualsense'
        if pid in (0x05C4, 0x09CC, 0x0BA0, 0x0268):
            return 'dualshock4'
    if vid == 0x057E and pid in (0x2009, 0x200E, 0x2006, 0x2007):
        return 'switch'
    if vid == 0x045E:
        return 'xbox'
    if vid == 0x18D1 and pid == 0x9400:
        return 'stadia'
    if vid in (0x231D, 0x3344, 0x4098):
        return 'flightstick'
    lowered = str(name or '').lower()
    if 'stadia' in lowered:
        return 'stadia'
    if any(token in lowered for token in FLIGHTSTICK_NAME_TOKENS):
        return 'flightstick'
    if 'dualsense' in lowered:
        return 'dualsense'
    if 'dualshock' in lowered or 'ps4' in lowered:
        return 'dualshock4'
    if 'switch pro' in lowered or 'joy-con' in lowered:
        return 'switch'
    return 'generic'


class ControllerDatabase:
    """3-Tier Controller Hardware & SDL DInput Mapping Database.

    - Tier 1: Built-in `COMMON_CONTROLLER_MODELS` + local persistent cache (`device_db_cache.json`) (<0.1ms).
    - Tier 2: Non-blocking background online lookup from `SDL_GameControllerDB` & `usb.ids` when an
      unknown `(vendor, product)` device connects, hot-queuing discovered SDL mappings for `SDL_GameControllerAddMapping`.
    """

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        network_enabled: Optional[bool] = None,
        fetcher: Optional[Callable[[str, float], str]] = None,
    ):
        self._lock = threading.RLock()
        if cache_path is None:
            default_root = Path(os.environ.get('GAMEPADSTUDIO_HOME', Path.home() / '.gamepadstudio'))
            cache_path = default_root / 'device_db_cache.json'
        self.cache_path = Path(cache_path)
        if network_enabled is None:
            # Disable spontaneous background network calls under pytest unless explicitly enabled
            network_enabled = 'pytest' not in sys.modules
        self.network_enabled = bool(network_enabled)
        self._fetcher = fetcher or self._default_http_fetch
        self._cache: dict[str, dict[str, Any]] = {}
        self._guid_cache: dict[str, dict[str, Any]] = {}
        self._inflight: set[tuple[int, int]] = set()
        self._pending_sdl_mappings: list[str] = []
        self._remote_sdl_index: Optional[dict[str, Any]] = None
        self._remote_usb_index: Optional[dict[str, Any]] = None
        self._load_disk_cache()

    @staticmethod
    def _vid_pid_key(vendor: int, product: int) -> str:
        return f'{int(vendor or 0):04x}:{int(product or 0):04x}'

    @staticmethod
    def _default_http_fetch(url: str, timeout: float = 3.5) -> str:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'GamePadStudio-DeviceDB/2.2'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', 'replace')

    def _load_disk_cache(self) -> None:
        with self._lock:
            if not self.cache_path.exists():
                return
            try:
                raw = json.loads(self.cache_path.read_text(encoding='utf-8'))
                if not isinstance(raw, dict):
                    return
                devices = raw.get('devices', {})
                if isinstance(devices, dict):
                    for k, v in devices.items():
                        if isinstance(k, str) and isinstance(v, dict):
                            self._cache[k.lower()] = dict(v)
                            if v.get('sdl_mapping'):
                                self._pending_sdl_mappings.append(str(v['sdl_mapping']))
                guids = raw.get('guids', {})
                if isinstance(guids, dict):
                    for g, v in guids.items():
                        if isinstance(g, str) and isinstance(v, dict):
                            self._guid_cache[g.lower()] = dict(v)
            except Exception:
                pass

    def _save_disk_cache(self) -> None:
        with self._lock:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    'version': 1,
                    'updated_at': int(time.time()),
                    'devices': self._cache,
                    'guids': self._guid_cache,
                }
                tmp = self.cache_path.with_suffix('.tmp')
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(tmp, self.cache_path)
            except Exception:
                pass

    def drain_pending_sdl_mappings(self) -> list[str]:
        """Return and clear newly discovered SDL_GameControllerAddMapping strings."""
        with self._lock:
            if not self._pending_sdl_mappings:
                return []
            items = list(dict.fromkeys(self._pending_sdl_mappings))
            self._pending_sdl_mappings.clear()
            return items

    def get_cached_entry(
        self,
        vendor: int,
        product: int,
        guid: str = '',
        name: str = '',
        trigger_async: bool = True,
    ) -> Optional[dict[str, Any]]:
        vid = int(vendor or 0)
        pid = int(product or 0)
        guid_norm = str(guid or '').strip().lower()
        if not vid and not pid and len(guid_norm) == 32:
            vid, pid = extract_vid_pid_from_sdl_guid(guid_norm)

        with self._lock:
            if guid_norm and guid_norm in self._guid_cache:
                return dict(self._guid_cache[guid_norm])
            key = self._vid_pid_key(vid, pid)
            if key in self._cache:
                entry = dict(self._cache[key])
                if guid_norm and entry.get('sdl_mapping'):
                    entry['sdl_mapping'] = adapt_sdl_mapping_for_guid(entry['sdl_mapping'], guid_norm, entry.get('model'))
                return entry
            if (vid, pid) in COMMON_CONTROLLER_MODELS:
                return dict(COMMON_CONTROLLER_MODELS[(vid, pid)])

        if trigger_async and self.network_enabled and vid > 0:
            self.trigger_async_lookup(vid, pid, guid=guid_norm, fallback_name=name)
        return None

    def trigger_async_lookup(self, vendor: int, product: int, guid: str = '', fallback_name: str = '') -> bool:
        vid = int(vendor or 0)
        pid = int(product or 0)
        if vid <= 0:
            return False
        with self._lock:
            key = self._vid_pid_key(vid, pid)
            if key in self._cache or (vid, pid) in COMMON_CONTROLLER_MODELS or (vid, pid) in self._inflight:
                return False
            self._inflight.add((vid, pid))
        worker = threading.Thread(
            target=self._online_lookup_worker,
            args=(vid, pid, str(guid or '').strip().lower(), str(fallback_name or '')),
            daemon=True,
        )
        worker.start()
        return True

    def fetch_and_cache_sync(self, vendor: int, product: int, guid: str = '', fallback_name: str = '') -> Optional[dict[str, Any]]:
        """Synchronously query SDL_GameControllerDB and usb.ids for `(vendor, product)` and persist to local cache."""
        vid = int(vendor or 0)
        pid = int(product or 0)
        guid_norm = str(guid or '').strip().lower()
        if not vid and not pid and len(guid_norm) == 32:
            vid, pid = extract_vid_pid_from_sdl_guid(guid_norm)
        if vid <= 0:
            return None
        return self._online_lookup_worker(vid, pid, guid_norm, fallback_name)

    def _online_lookup_worker(self, vid: int, pid: int, guid_norm: str, fallback_name: str) -> Optional[dict[str, Any]]:
        try:
            sdl_index = self._ensure_remote_sdl_index()
            usb_index = self._ensure_remote_usb_index()

            sdl_hit = None
            if sdl_index:
                if guid_norm and guid_norm in sdl_index['by_guid']:
                    sdl_hit = sdl_index['by_guid'][guid_norm]
                elif (vid, pid) in sdl_index['by_vid_pid']:
                    sdl_hit = sdl_index['by_vid_pid'][(vid, pid)]

            usb_brand = VENDOR_BRAND_NAMES.get(vid, '')
            usb_model = ''
            if usb_index:
                usb_brand = usb_brand or usb_index['vendors'].get(vid, '')
                usb_model = usb_index['products'].get((vid, pid), '')

            model = (sdl_hit or {}).get('model') or usb_model or fallback_name or f'USB Controller ({vid:04x}:{pid:04x})'
            brand = usb_brand
            if not brand:
                lowered = model.lower()
                for token, label in BRAND_NAME_TOKENS:
                    if token in lowered:
                        brand = label
                        break
            brand = brand or (model.split()[0] if model and ' ' in model else 'DirectInput 外设')

            lowered_model = model.lower()
            if 'dualsense' in lowered_model:
                family = 'dualsense'
            elif 'dualshock' in lowered_model or 'ps4' in lowered_model:
                family = 'dualshock4'
            elif 'switch' in lowered_model or 'joy-con' in lowered_model:
                family = 'switch'
            elif 'stadia' in lowered_model:
                family = 'stadia'
            elif any(tok in lowered_model for tok in FLIGHTSTICK_NAME_TOKENS):
                family = 'flightstick'
            elif 'xbox' in lowered_model:
                family = 'xbox'
            else:
                family = 'generic'

            sdl_mapping = (sdl_hit or {}).get('sdl_mapping', '')
            if sdl_mapping and guid_norm:
                sdl_mapping = adapt_sdl_mapping_for_guid(sdl_mapping, guid_norm, model)

            entry = {
                'vendor': vid,
                'product': pid,
                'brand': brand,
                'model': model,
                'family': family,
                'protocol': 'hidapi' if family in ('dualsense', 'dualshock4', 'switch', 'stadia') else 'dinput',
                'dinput_layout': (sdl_hit or {}).get('dinput_layout', 'type_b'),
                'back_paddles': int((sdl_hit or {}).get('back_paddles', 0)),
                'sdl_mapping': sdl_mapping,
                'source': 'online_db',
            }
            with self._lock:
                self._cache[self._vid_pid_key(vid, pid)] = entry
                if guid_norm:
                    self._guid_cache[guid_norm] = entry
                if sdl_mapping:
                    self._pending_sdl_mappings.append(sdl_mapping)
                self._save_disk_cache()
            return entry
        finally:
            with self._lock:
                self._inflight.discard((vid, pid))

    def _ensure_remote_sdl_index(self) -> Optional[dict[str, Any]]:
        if self._remote_sdl_index is not None:
            return self._remote_sdl_index
        plat = current_sdl_platform_name()
        for url in SDL_GAMECONTROLLERDB_URLS:
            try:
                text = self._fetcher(url, 3.5)
                if text and ',platform:' in text:
                    parsed = parse_sdl_gamecontrollerdb(text, platform_name=plat)
                    if not parsed['by_guid'] and not parsed['by_vid_pid']:
                        parsed = parse_sdl_gamecontrollerdb(text, platform_name=None)
                    self._remote_sdl_index = parsed
                    return parsed
            except Exception:
                continue
        return None

    def _ensure_remote_usb_index(self) -> Optional[dict[str, Any]]:
        if self._remote_usb_index is not None:
            return self._remote_usb_index
        for url in USB_IDS_URLS:
            try:
                text = self._fetcher(url, 3.5)
                if text:
                    parsed = parse_usb_ids(text)
                    if parsed['vendors']:
                        self._remote_usb_index = parsed
                        return parsed
            except Exception:
                continue
        return None


_GLOBAL_CONTROLLER_DB: Optional[ControllerDatabase] = None


def get_controller_database() -> ControllerDatabase:
    global _GLOBAL_CONTROLLER_DB
    if _GLOBAL_CONTROLLER_DB is None:
        _GLOBAL_CONTROLLER_DB = ControllerDatabase()
    return _GLOBAL_CONTROLLER_DB


def detect_input_protocol(guid='', is_gamecontroller=True, vendor=0, product=0, name='', input_backend=''):
    """Identify whether a device uses 'dinput', 'hidapi', or 'xinput'."""
    if str(input_backend or '').lower() == 'xinput':
        return 'xinput'
    try:
        raw = bytes.fromhex(guid) if isinstance(guid, str) and guid else bytes(guid or b'')
    except (TypeError, ValueError):
        raw = b''
    if len(raw) == 16:
        if raw[14] == ord('x') and raw[15] == 1:
            return 'xinput'
        if raw[14] == ord('h'):
            return 'hidapi'
    vid = int(vendor or 0)
    pid = int(product or 0)
    if not vid and not pid and len(raw) == 16:
        vid, pid = extract_vid_pid_from_sdl_guid(raw.hex())
    known = get_controller_database().get_cached_entry(vid, pid, guid=str(guid or ''), name=name, trigger_async=False)
    if known and known.get('protocol'):
        return known['protocol']
    lowered = str(name or '').lower()
    if any(tok in lowered for tok in ('dinput', 'directinput', 'rumblepad', 'dual action', 'twin usb', 'dragonrise', 'generic usb joystick')):
        return 'dinput'
    if is_gamecontroller is False:
        return 'dinput'
    if vid in (0x054C, 0x057E, 0x18D1):
        return 'hidapi'
    if vid in (0x2DC8, 0x04B4, 0x20BC, 0x3537, 0x0079, 0x0810, 0x2563, 0x0E8F, 0x231D, 0x3344, 0x4098):
        return 'dinput'
    return 'hidapi' if is_gamecontroller else 'dinput'


def identify_controller_hardware(state, trigger_async: bool = True):
    """Return structured device recognition info (brand, model, family, protocol, capabilities, recommended_preset)."""
    if not isinstance(state, dict):
        return {
            'vendor': 0,
            'product': 0,
            'brand': 'Unknown',
            'model': 'Disconnected',
            'display_name': 'Disconnected',
            'family': 'generic',
            'protocol': 'offline',
            'dinput_layout': None,
            'back_paddles': 0,
            'sdl_mapping': '',
            'recommended_preset': 'XInput · 默认',
            'recommended_scene': None,
        }
    vendor = int(state.get('vendor') or state.get('vendor_id') or 0)
    product = int(state.get('product') or state.get('product_id') or 0)
    guid = str(state.get('guid') or '')
    if not guid and isinstance(state.get('profile_key'), str) and state['profile_key'].count(':') >= 3:
        maybe_guid = state['profile_key'].rsplit(':', 1)[-1]
        if len(maybe_guid) == 32:
            guid = maybe_guid
    if not vendor and not product and len(guid) == 32:
        vendor, product = extract_vid_pid_from_sdl_guid(guid)

    name = str(state.get('raw_name') or state.get('name') or 'Controller')
    device_path = str(state.get('device_path') or '')
    tp_info = (
        detect_third_party_brand_and_model_from_name(name)
        or detect_third_party_brand_and_model_from_name(str(state.get('name') or ''))
        or detect_third_party_brand_and_model_from_name(device_path)
    )

    if not tp_info and vendor in (0, 0x045E) and is_generic_controller_name(name):
        host_usb = probe_host_usb_controllers(force=bool(state.get('_allow_host_probe')))
        if host_usb:
            usb_hit = host_usb[0]
            if usb_hit.get('vendor') and usb_hit['vendor'] not in (0, 0x045E):
                vendor = int(usb_hit['vendor'])
                product = int(usb_hit.get('product') or 0)
            if usb_hit.get('name'):
                name = str(usb_hit['name'])
            tp_info = usb_hit.get('tp_match') or detect_third_party_brand_and_model_from_name(name)

    lowered = name.lower()
    known = get_controller_database().get_cached_entry(
        vendor, product, guid=guid, name=name, trigger_async=trigger_async
    ) or {}

    if tp_info and (not known or known.get('brand') in ('Microsoft', 'Nintendo', 'Sony', None, '')):
        merged = dict(known)
        merged.update({k: v for k, v in tp_info.items() if v not in (None, '')})
        known = merged

    from .controller_catalog import family_for
    family = state.get('family') or known.get('family') or family_for(
        state.get('controller_type', 0), vendor, product, name
    )
    if tp_info and tp_info.get('brand') in THIRD_PARTY_BRANDS and family == 'xbox':
        family = tp_info.get('family', 'generic')

    protocol = state.get('input_protocol') or detect_input_protocol(
        guid=guid,
        is_gamecontroller=state.get('is_gamecontroller', True),
        vendor=vendor,
        product=product,
        name=name,
        input_backend=state.get('input_backend', ''),
    ) or known.get('protocol')

    brand = known.get('brand') or VENDOR_BRAND_NAMES.get(vendor)
    if not brand:
        for token, label in BRAND_NAME_TOKENS:
            if token in lowered:
                brand = label
                break
    brand = brand or ('DirectInput 外设' if protocol == 'dinput' else '通用外设')
    model = known.get('model') or clean_controller_model_name(name, brand)
    if brand in THIRD_PARTY_BRANDS and is_generic_controller_name(model):
        model = f"{brand.split()[0]} Controller"
    if family == 'flightstick' or (vendor == 0 and brand not in THIRD_PARTY_BRANDS):
        dinput_layout = known.get('dinput_layout')
    else:
        dinput_layout = known.get('dinput_layout') or (
            'type_a' if protocol == 'dinput' and vendor in (0x046D, 0x0079, 0x0810, 0x0E8F)
            else 'type_b' if protocol == 'dinput'
            else None
        )
    back_paddles = int(known.get('back_paddles', 0))
    if not back_paddles and brand in THIRD_PARTY_BRANDS:
        back_paddles = infer_default_back_paddles(brand, model or name)
    if not back_paddles and any(b in (state.get('available_buttons') or ()) for b in (16, 17, 18, 19)):
        back_paddles = sum(1 for b in (16, 17, 18, 19) if b in (state.get('available_buttons') or ()))

    if family == 'dualsense':
        rec_preset = '主机体验'
        rec_scene = 'fps_gyro' if state.get('gyro') else 'sprint_3d'
    elif family == 'dualshock4':
        rec_preset = 'PS4 · 默认'
        rec_scene = 'fps_gyro' if state.get('gyro') else 'sprint_3d'
    elif family == 'switch':
        rec_preset = 'Switch · 默认'
        rec_scene = 'fps_gyro' if state.get('gyro') else 'sprint_3d'
    elif family == 'flightstick':
        rec_preset = '飞行摇杆 · 默认'
        rec_scene = None
    elif family == 'stadia':
        rec_preset = 'Stadia · 默认'
        rec_scene = 'sprint_3d'
    elif protocol == 'dinput':
        rec_preset = 'DInput · 默认'
        rec_scene = 'dnf_dash'
    else:
        rec_preset = 'Xbox · 默认' if family == 'xbox' else 'XInput · 默认'
        rec_scene = 'sprint_3d'

    return {
        'vendor': vendor,
        'product': product,
        'brand': brand,
        'model': model,
        'display_name': model,
        'family': family,
        'protocol': protocol,
        'dinput_layout': dinput_layout,
        'back_paddles': back_paddles,
        'sdl_mapping': known.get('sdl_mapping', ''),
        'has_gyro': bool(state.get('gyro') or known.get('gyro')),
        'has_touchpad': bool(state.get('touchpad') or known.get('touchpad')),
        'recommended_preset': rec_preset,
        'recommended_scene': rec_scene,
    }


def normalize_dinput_gamepad_state(
    raw_buttons,
    raw_axes,
    hat_mask=0,
    dinput_layout=None,
    num_buttons=12,
    sdl_mapping='',
    signed_trigger_axes=None,
):
    """Normalize a DirectInput gamepad into canonical SDL positions without Hat/R3 collision.

    - If `sdl_mapping` (`SDL_GameControllerDB` line) is provided, translates `xinput_mapped` raw buttons
      to their exact XInput slots (`0..14`, `LT`, `RT`) and maps `unused_buttons` to conflict-free
      canonical slots (`>= 16`) via `partition_dinput_buttons_by_sdl_db`.
    - Rescales signed DirectInput trigger axes resting at `-1.0` or `< -0.05` to `[0.0, 1.0]`.
    - Preserves FlightStick / Button-Box / Virtual raw joysticks (`>20` raw buttons or `dinput_layout=None`).
    - Maps DInput Type-A (Logitech/DragonRise: 0=West/X, 1=South/A, 2=East/B, 3=North/Y) or
      Type-B (8BitDo/Flydigi/Beitong/ShanWan: 0=South/A, 1=East/B, 2=West/X, 3=North/Y, 4=LB, 5=RB,
      6=LT, 7=RT, 8=Back, 9=Start, 10=LS/L3, 11=RS/R3, 12=Guide, 13=Capture, 14..17=P1..P4).
    - Converts POV Hat (0x01/0x04/0x08/0x02) cleanly to canonical D-Pad buttons 11..14.
    """
    raw_set = {int(b) for b in (raw_buttons or ()) if isinstance(b, int) and 0 <= b < 128}
    axes = [float(a) for a in (raw_axes or ())]

    if sdl_mapping and sdl_mapping.strip():
        while len(axes) < 6:
            axes.append(0.0)
        # Parse axis mappings if present in sdl_mapping
        for token in sdl_mapping.split(','):
            token = token.strip()
            if ':' not in token:
                continue
            field, source = token.split(':', 1)
            field = field.strip().lstrip('+-~')
            source = source.strip()
            clean_source = source.lstrip('+-~')
            if clean_source.startswith('a') and clean_source[1:].isdigit():
                axis_idx = int(clean_source[1:])
                if axis_idx < len(raw_axes or ()):
                    val = float(raw_axes[axis_idx])
                    if source.startswith('~'):
                        val = -val
                    if field == 'lefttrigger':
                        axes[4] = val
                    elif field == 'righttrigger':
                        axes[5] = val
                    elif field == 'leftx':
                        axes[0] = val
                    elif field == 'lefty':
                        axes[1] = val
                    elif field == 'rightx':
                        axes[2] = val
                    elif field == 'righty':
                        axes[3] = val

        # Rescale signed triggers: [-1.0, 1.0] -> [0.0, 1.0]
        for idx in (4, 5):
            if idx < len(axes):
                if (signed_trigger_axes and idx in signed_trigger_axes) or axes[idx] < -0.05:
                    axes[idx] = max(0.0, min(1.0, (axes[idx] + 1.0) / 2.0))

        total = max(int(num_buttons or 0), max(raw_set, default=-1) + 1)
        part = partition_dinput_buttons_by_sdl_db(total, sdl_mapping)
        normalized = set()
        for b in raw_set:
            if b in part['xinput_mapped']:
                slot = part['xinput_mapped'][b]
                if slot == 'LT':
                    axes[4] = max(axes[4], 1.0)
                elif slot == 'RT':
                    axes[5] = max(axes[5], 1.0)
                elif slot.isdigit():
                    normalized.add(int(slot))
            elif b in part['canonical_unused_slots']:
                normalized.add(part['canonical_unused_slots'][b])
        if hat_mask & 0x01:
            normalized.add(11)
        if hat_mask & 0x04:
            normalized.add(12)
        if hat_mask & 0x08:
            normalized.add(13)
        if hat_mask & 0x02:
            normalized.add(14)
        return sorted(normalized), axes[:6]

    # Non-SDL mapping branch: rescale signed triggers if present
    for idx in (4, 5):
        if idx < len(axes):
            if (signed_trigger_axes and idx in signed_trigger_axes) or axes[idx] < -0.05:
                axes[idx] = max(0.0, min(1.0, (axes[idx] + 1.0) / 2.0))

    if not dinput_layout or num_buttons > 20:
        if hat_mask & 0x01:
            raw_set.add(11)
        if hat_mask & 0x04:
            raw_set.add(12)
        if hat_mask & 0x08:
            raw_set.add(13)
        if hat_mask & 0x02:
            raw_set.add(14)
        return sorted(raw_set), axes

    while len(axes) < 6:
        axes.append(0.0)

    face_map = {0: 2, 1: 0, 2: 1, 3: 3} if dinput_layout == 'type_a' else {0: 0, 1: 1, 2: 2, 3: 3}
    btn_map = {
        **face_map,
        4: 9,   # L1 / LB
        5: 10,  # R1 / RB
        8: 4,   # Select / Back
        9: 6,   # Start
        10: 7,  # L3 / LS
        11: 8,  # R3 / RS (No longer collides with D-Pad Up 11!)
        12: 5,  # Guide / Home
        13: 15, # Capture / Share
        14: 16, # Back Paddle P1 / M1
        15: 17, # Back Paddle P3 / M2
        16: 18, # Back Paddle P2 / M3
        17: 19, # Back Paddle P4 / M4
    }
    normalized = set()
    for b in raw_set:
        if b == 6:
            axes[4] = max(axes[4], 1.0)
        elif b == 7:
            axes[5] = max(axes[5], 1.0)
        elif b in btn_map:
            normalized.add(btn_map[b])
        elif b >= 18:
            normalized.add(b + 3)

    if hat_mask & 0x01:
        normalized.add(11)
    if hat_mask & 0x04:
        normalized.add(12)
    if hat_mask & 0x08:
        normalized.add(13)
    if hat_mask & 0x02:
        normalized.add(14)

    return sorted(normalized), axes[:6]


def is_dualsense_edge(state):
    """Return True when the controller state represents a Sony DualSense Edge (054c:0df2)."""
    if not isinstance(state, dict):
        return False
    vendor_id = state.get('vendor_id') or state.get('vendor')
    product_id = state.get('product_id') or state.get('product')
    if vendor_id == SONY_VENDOR_ID and product_id == DUALSENSE_EDGE_PRODUCT_ID:
        return True
    name = str(state.get('name', '') or '').lower()
    return 'dualsense edge' in name


def gyro_protocol_hint(state):
    """Return a user-facing hint when a connected controller does not expose a gyroscope sensor."""
    if not isinstance(state, dict) or not state.get('connected'):
        return ''
    if state.get('gyro'):
        return ''
    backend = str(state.get('input_backend', '') or '').lower()
    protocol = str(state.get('input_protocol', '') or '').lower()
    family = str(state.get('family', '') or '').lower()
    if backend == 'xinput' or protocol == 'xinput' or family == 'xbox':
        return tr(
            '当前处于 XInput/Xbox 协议，底层不汇报陀螺仪；若为国产多模手柄，请切至 Switch 或 PS 模式以启用体感瞄准。'
        )
    if protocol == 'dinput':
        return tr(
            '当前处于 DirectInput (DInput) 协议，标准 DInput 轴不汇报三轴陀螺仪；若手柄支持体感，请切换至 Switch 或 DualShock4/DualSense 模式。'
        )
    return tr('当前连接协议未上报陀螺仪传感器（需使用支持 SDL2 陀螺仪的 PS5/PS4/Switch 模式连接）。')


class GyroBiasCalibrator:
    """Stationary zero-rate offset calibrator for 3-axis gyroscope samples (rad/s)."""

    def __init__(self, required_samples=50, stationary_threshold=0.06):
        self.required_samples = max(1, int(required_samples))
        self.stationary_threshold = float(stationary_threshold)
        self._samples = []
        self.bias = [0.0, 0.0, 0.0]
        self.calibrated = False

    def reset(self):
        self._samples.clear()
        self.bias = [0.0, 0.0, 0.0]
        self.calibrated = False

    def feed(self, gyro_xyz):
        """Feed one [pitch, yaw, roll] sample; updates bias once enough stationary samples accumulate."""
        if not isinstance(gyro_xyz, (list, tuple)) or len(gyro_xyz) < 3:
            return list(self.bias)
        gx, gy, gz = float(gyro_xyz[0]), float(gyro_xyz[1]), float(gyro_xyz[2])
        if self.calibrated:
            return [gx - self.bias[0], gy - self.bias[1], gz - self.bias[2]]
        if (
            abs(gx) <= self.stationary_threshold
            and abs(gy) <= self.stationary_threshold
            and abs(gz) <= self.stationary_threshold
        ):
            self._samples.append((gx, gy, gz))
            if len(self._samples) >= self.required_samples:
                count = float(len(self._samples))
                self.bias = [
                    sum(item[0] for item in self._samples) / count,
                    sum(item[1] for item in self._samples) / count,
                    sum(item[2] for item in self._samples) / count,
                ]
                self.calibrated = True
        else:
            self._samples.clear()
        return [gx - self.bias[0], gy - self.bias[1], gz - self.bias[2]]
