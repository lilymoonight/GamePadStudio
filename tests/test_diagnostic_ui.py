"""The support-report UI uses synthetic state and never opens real hardware."""
import json
import os
import sys
from types import ModuleType

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication, QFileDialog, QSizePolicy

from gamepadstudio.input_tester import InputTester
from gamepadstudio.i18n import init_language
from gamepadstudio.studio import Studio


class OfflineDevice:
    available = []

    def scan(self):
        pass

    def read(self):
        return None

    def close(self):
        pass


def test_offline_test_page_keeps_activity_and_diagnostic_actions_available():
    init_language('zh')
    app = QApplication.instance() or QApplication([])
    actions = []
    tester = InputTester(lambda: actions.append('events'),
                         export_diagnostic=lambda: actions.append('diagnostic'))
    try:
        tester.resize(960, 640)
        tester.update_state(None)
        tester.show()
        app.processEvents()
        assert tester.events_button.isVisible()
        assert tester.export_diagnostic_button.isVisible()
        assert tester.events_button.isEnabled()
        assert tester.export_diagnostic_button.isEnabled()
        assert '脱敏' in tester.diagnostic_note.text()
        assert '不包含原始日志或配置' in tester.export_diagnostic_button.toolTip()
        tester.resize(700, 640)
        app.processEvents()
        assert not tester.diagnostic_note.isVisible()
        assert not tester.drift_spec.isVisible()
        assert tester.drift_reading.toolTip() == tester.drift_spec.text()
        assert not tester.trigger_description.isVisible()
        assert not tester.rumble_description.isVisible()
        assert tester.trigger_gauges[0].toolTip() == tester.trigger_description.text()
        assert tester.motor_status.toolTip() == tester.rumble_description.text()
        assert tester.stick_gauges[0].sizePolicy().verticalPolicy() == QSizePolicy.Ignored
        assert tester.trigger_gauges[0].sizePolicy().verticalPolicy() == QSizePolicy.Ignored
        assert tester.events_button.isVisible()
        assert tester.export_diagnostic_button.isVisible()
        tester.events_button.click()
        tester.export_diagnostic_button.click()
        assert actions == ['events', 'diagnostic']
        tester.resize(960, 640)
        app.processEvents()
        assert tester.stick_gauges[0].sizePolicy().verticalPolicy() == QSizePolicy.Expanding
    finally:
        tester.close()


def test_export_dialog_cancel_success_and_failure_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr('gamepadstudio.studio.Device', OfflineDevice)
    app = QApplication.instance() or QApplication([])
    window = Studio(tmp_path, standalone=True, lang='zh')
    report_module = ModuleType('gamepadstudio.diagnostic_report')
    called = []
    report = {'format': 'test-diagnostic'}

    def build(root, device=None, status=None):
        called.append(('build', root, device, status))
        return report

    def save(path, value):
        called.append(('save', path, value))
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write('sanitized')

    report_module.build_report = build
    report_module.save_report = save
    monkeypatch.setitem(sys.modules, 'gamepadstudio.diagnostic_report', report_module)
    destination = tmp_path / 'diagnostic.json'
    choices = [('', ''), (str(destination), ''), (str(destination), '')]

    def choose_file(*args, **kwargs):
        assert '诊断' in args[1]
        assert args[2].endswith('.json')
        assert '*.json' in args[3]
        return choices.pop(0)

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose_file)
    try:
        assert window.tester.export_diagnostic_button.isEnabled()
        window.export_diagnostic()
        assert called == []
        assert not destination.exists()

        window.export_diagnostic()
        assert called == [('build', window.store.root, None, None),
                          ('save', str(destination), report)]
        assert destination.read_text(encoding='utf-8') == 'sanitized'
        assert '诊断报告已保存' in window.events.item(0).text()

        called.clear()
        report_module.save_report = lambda *args: (_ for _ in ()).throw(OSError('磁盘已满'))
        window.export_diagnostic()
        assert called == [('build', window.store.root, None, None)]
        assert '诊断导出失败' in window.events.item(0).text()
        assert '磁盘已满' in window.events.item(0).text()
        assert destination.read_text(encoding='utf-8') == 'sanitized'
    finally:
        window.cleanup()
        window.hide()


def test_offline_ui_exports_real_sanitized_report(tmp_path, monkeypatch):
    monkeypatch.setattr('gamepadstudio.studio.Device', OfflineDevice)
    app = QApplication.instance() or QApplication([])
    window = Studio(tmp_path, standalone=True, lang='zh')
    destination = tmp_path / 'shareable-diagnostic.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName',
                        lambda *args, **kwargs: (str(destination), ''))
    secret = 'private-test-token-9384'
    with (tmp_path / 'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'message': '连接失败：' + secret}) + '\n')
    try:
        window.tester.export_diagnostic_button.click()
        body = destination.read_text(encoding='utf-8')
        report = json.loads(body)
        assert report['format'] == 'gamepadstudio-diagnostic'
        assert report['device']['connected'] is False
        assert report['logs']['ui']['counts']['connection_failed'] == 1
        assert secret not in body
        assert str(tmp_path) not in body
        assert '诊断报告已保存' in window.events.item(0).text()
    finally:
        window.cleanup()
        window.hide()
