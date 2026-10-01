"""Only the selected physical input device appears in the device library."""
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from gamepadstudio.controller_gallery import ControllerGallery


def test_offline_gallery_only_previews_xinput_and_cannot_manage_unowned_models():
    app = QApplication.instance() or QApplication([])
    managed = []
    gallery = ControllerGallery(lambda instance: None, lambda values: None, lambda: None,
                                lambda: managed.append(True), favorites=['dualsense'])
    try:
        gallery.set_devices([], None)
        assert gallery.grid.count() == 1
        assert gallery.current_family() == 'generic'
        assert gallery.grid.itemAt(0).widget().accessibleName() in ('通用 XInput', 'Standard XInput')
        gallery.open_family('dualsense')
        assert not managed and gallery.detail_dialog('dualsense') is None
        preview = gallery.detail_dialog('generic')
        try:
            labels = ' '.join(label.text() for label in preview.findChildren(QLabel))
            assert '8BitDo' not in labels and 'DualSense' not in labels
            assert all(button.text() not in ('管理', 'Manage') for button in preview.findChildren(QPushButton))
        finally:
            preview.close()
        gallery.filter.setCurrentIndex(2)
        assert gallery.empty_state
        gallery.reset_filters()
        assert gallery.current_family() == 'generic' and not gallery.empty_state
    finally:
        gallery.close()


def test_connected_device_switcher_keeps_other_models_out_of_current_device_card():
    app = QApplication.instance() or QApplication([])
    selected, managed = [], []
    gallery = ControllerGallery(selected.append, lambda values: None, lambda: None,
                                lambda: managed.append(True))
    devices = [dict(instance_id=1, name='My Xbox', family='xbox', supported=True),
               dict(instance_id=2, name='My DualSense', family='dualsense', supported=True)]
    try:
        gallery.set_devices(devices, 1)
        assert gallery.grid.count() == 1 and gallery.current_family() == 'xbox'
        assert gallery.grid.itemAt(0).widget().accessibleName() == 'My Xbox'
        gallery.open_family('dualsense')
        assert not selected and not managed
        switch = next(button for button in gallery.connections.findChildren(QPushButton)
                      if button.text() in ('切换', 'Switch'))
        switch.click()
        assert selected == [2]
        gallery.set_devices(devices, 2)
        assert gallery.grid.count() == 1 and gallery.current_family() == 'dualsense'
        assert gallery.grid.itemAt(0).widget().accessibleName() == 'My DualSense'
        gallery.open_family('dualsense')
        assert managed == [True]
    finally:
        gallery.close()


def test_current_device_details_use_reported_capabilities_and_ignore_input_frames():
    app = QApplication.instance() or QApplication([])
    gallery = ControllerGallery(lambda instance: None, lambda values: None, lambda: None, lambda: None)
    row = dict(instance_id=4, name='Third-party pad', family='generic', supported=True)
    state = {**row, 'available_buttons': [0, 1, 2], 'available_axes': [0, 1],
             'led': False, 'rumble': False, 'touchpad': False, 'buttons': []}
    try:
        gallery.set_devices([row], 4, state)
        first = gallery.grid.itemAt(0).widget()
        info = gallery.device_info()
        assert info['name'] == 'Third-party pad'
        assert '3' in info['subtitle'] and '2' in info['subtitle']
        assert not any(word in info['subtitle'] for word in ('灯光', '震动', '触摸板', 'LED', 'Rumble', 'Touchpad'))
        assert '8BitDo' not in info['subtitle']
        gallery.set_devices([row], 4, {**state, 'buttons': [0], 'axes': [.8, 0]})
        assert gallery.grid.itemAt(0).widget() is first
        gallery.set_devices([row], 4, {**state, 'rumble': True})
        assert any(word in gallery.device_info()['subtitle'] for word in ('震动', 'Rumble'))
    finally:
        gallery.close()
